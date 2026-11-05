"""Investigator API and UI. Reads the cached triage; never re-scores on request.

Usage: uvicorn app.main:app --reload
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from triage.config import OUTPUT_DIR
from triage.data import load_cases
from triage.grounding import validate_answer
from triage import store
from triage.labels import format_value, label
from triage.llm import Action, LLMConfigError, LLMError, RiskLevel, answer_question
from triage.pipeline import concerns, score_with_context
from triage.scoring import CaseScore

TRIAGE_PATH = OUTPUT_DIR / "triage.json"
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Case Review")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def no_stale_ui(request, call_next):
    # The UI has no build step to version its files, so make browsers recheck
    # them (a cheap 304 when unchanged) instead of showing yesterday's page.
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@lru_cache
def _context() -> tuple[pd.DataFrame, dict[str, CaseScore], dict[str, dict]]:
    """Cases, rules scores and cross-case facts; cheap, and fixed while the server runs."""
    df = load_cases()
    return df, *score_with_context(df)


def _load_triage() -> dict[str, dict]:
    if not TRIAGE_PATH.exists():
        raise HTTPException(503, "No triage yet. Run `python scripts/run_triage.py` first.")
    return {c["case_id"]: c for c in json.loads(TRIAGE_PATH.read_text())}


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


def _is_clean_low(case: dict) -> bool:
    """Safe to bulk-accept: the rules and the AI both say Low, the AI says close,
    and nothing about the case needs a second look."""
    a = case["assessment"]
    return (case["tier"] == "Low" and not case["floor_applied"] and a is not None
            and a["risk_level"] == "Low" and a["recommended_action"] == "Close as false positive"
            and not case["grounding_issues"])


@app.get("/api/cases")
def list_cases() -> list[dict]:
    """The queue, highest risk first, with each case's concerns and current decision."""
    decisions = store.latest_decisions()
    _, scores, context = _context()
    return [{**c, "decision": decisions.get(c["case_id"]), "clean_low": _is_clean_low(c),
             "concerns": concerns(scores[c["case_id"]], context[c["case_id"]])}
            for c in _load_triage().values()]


@app.get("/api/cases/{case_id}")
def get_case(case_id: str) -> dict:
    """One case: the triage result plus the raw claim data it was based on."""
    case = _load_triage().get(case_id)
    if case is None:
        raise HTTPException(404, f"Unknown case {case_id}")

    row = _context()[0].loc[case_id].to_dict()
    # The same data in plain words, for people who don't know the columns.
    claim_display = [{"label": label(k), "value": format_value(k, v)} for k, v in row.items()]
    row["claim_date"] = row["claim_date"].date().isoformat()
    # numpy scalars -> plain Python so they serialize as JSON
    claim = {k: v.item() if hasattr(v, "item") else v for k, v in row.items()}
    history = store.case_history(case_id)
    decision = history["decisions"][-1] if history["decisions"] else None
    return {**case, "claim": claim, "claim_display": claim_display,
            "decision": decision, "history": history}


class Turn(BaseModel):
    question: str
    answer: str


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    history: list[Turn] = []


@app.post("/api/cases/{case_id}/ask")
def ask(case_id: str, body: Question) -> dict:
    """Ask the AI about one case. Answers use only that case's facts, and every
    field the answer cites is checked against the data."""
    case = _load_triage().get(case_id)
    if case is None:
        raise HTTPException(404, f"Unknown case {case_id}")

    df, scores, context = _context()
    row, case_score, cross_case = df.loc[case_id], scores[case_id], context[case_id]
    try:
        answer = answer_question(row, case_score, cross_case, case["assessment"],
                                 body.question, [t.model_dump() for t in body.history])
    except LLMConfigError as e:
        raise HTTPException(503, f"AI is not configured: {e}") from e
    except LLMError as e:
        raise HTTPException(502, f"AI is unavailable right now, try again: {e}") from e

    checked, issues = validate_answer(row, case_score, answer, cross_case)
    return {**checked, "grounding_issues": issues}


class Decision(BaseModel):
    decision: Literal["accept", "override"]
    investigator: str = Field(min_length=1, max_length=100)
    # Only for overrides: what the investigator decided instead, and why.
    risk_level: RiskLevel | None = None
    action: Action | None = None
    reason: str = Field("", max_length=2000)

    @model_validator(mode="after")
    def _override_needs_details(self):
        if self.decision == "override" and not (self.risk_level and self.action
                                                and self.reason.strip()):
            raise ValueError("An override needs a risk level, an action and a reason")
        return self


def _record(case: dict, body: Decision) -> dict:
    a = case["assessment"]
    if body.decision == "accept":
        if a is None:
            raise HTTPException(409, "There is no AI assessment to accept; override instead")
        final_level, final_action = a["risk_level"], a["recommended_action"]
    else:
        final_level, final_action = body.risk_level, body.action
        if a and (final_level, final_action) == (a["risk_level"], a["recommended_action"]):
            # An "override" that agrees with the AI would mislead the audit trail.
            raise HTTPException(422, "That matches the AI's call; use Accept instead, "
                                     "or change the risk level or action")
    return store.record_decision(
        case["case_id"], body.decision, final_level, final_action, body.investigator,
        reason=body.reason.strip(), ai_risk_level=a and a["risk_level"],
        ai_action=a and a["recommended_action"])


@app.post("/api/cases/{case_id}/decision")
def decide(case_id: str, body: Decision) -> dict:
    """Accept the AI's call or override it. Every decision is kept (audit trail)."""
    case = _load_triage().get(case_id)
    if case is None:
        raise HTTPException(404, f"Unknown case {case_id}")
    return _record(case, body)


class Note(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    investigator: str = Field(min_length=1, max_length=100)


@app.post("/api/cases/{case_id}/notes")
def add_note(case_id: str, body: Note) -> dict:
    if case_id not in _load_triage():
        raise HTTPException(404, f"Unknown case {case_id}")
    return store.add_note(case_id, body.text.strip(), body.investigator)


class BulkAccept(BaseModel):
    case_ids: list[str]
    investigator: str = Field(min_length=1, max_length=100)


@app.post("/api/bulk-accept")
def bulk_accept(body: BulkAccept) -> dict:
    """Accept the AI's call on many clean Low cases at once. The server re-checks
    each one, so a stale page can't bulk-close a case that needs a look."""
    cases, decided = _load_triage(), store.latest_decisions()
    accepted, skipped = [], []
    for case_id in body.case_ids:
        case = cases.get(case_id)
        if case is None or case_id in decided or not _is_clean_low(case):
            skipped.append(case_id)
            continue
        _record(case, Decision(decision="accept", investigator=body.investigator))
        accepted.append(case_id)
    return {"accepted": accepted, "skipped": skipped}
