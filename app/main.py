"""Investigator API and UI. Reads the cached triage; never re-scores on request.

Usage: uvicorn app.main:app --reload
"""

import json
from functools import lru_cache
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from triage.config import OUTPUT_DIR
from triage.data import load_cases
from triage.grounding import validate_answer
from triage.llm import LLMConfigError, LLMError, answer_question
from triage.pipeline import score_with_context
from triage.scoring import CaseScore

TRIAGE_PATH = OUTPUT_DIR / "triage.json"
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Case Review")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


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


@app.get("/api/cases")
def list_cases() -> list[dict]:
    """The queue, highest risk first."""
    return list(_load_triage().values())


@app.get("/api/cases/{case_id}")
def get_case(case_id: str) -> dict:
    """One case: the triage result plus the raw claim data it was based on."""
    case = _load_triage().get(case_id)
    if case is None:
        raise HTTPException(404, f"Unknown case {case_id}")

    row = _context()[0].loc[case_id].to_dict()
    row["claim_date"] = row["claim_date"].date().isoformat()
    # numpy scalars -> plain Python so they serialize as JSON
    claim = {k: v.item() if hasattr(v, "item") else v for k, v in row.items()}
    return {**case, "claim": claim}


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


# TODO (step 4): POST /api/cases/{case_id}/decision and /notes via triage.store
