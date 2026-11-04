"""Investigator API and UI. Reads the cached triage; never re-scores on request.

Usage: uvicorn app.main:app --reload
"""

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from triage.config import OUTPUT_DIR
from triage.data import load_cases

TRIAGE_PATH = OUTPUT_DIR / "triage.json"
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Case Review")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


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

    row = load_cases().loc[case_id].to_dict()
    row["claim_date"] = row["claim_date"].date().isoformat()
    # numpy scalars -> plain Python so they serialize as JSON
    claim = {k: v.item() if hasattr(v, "item") else v for k, v in row.items()}
    return {**case, "claim": claim}


# TODO (step 4): POST /api/cases/{case_id}/decision and /notes via triage.store
# TODO (step 5): POST /api/cases/{case_id}/ask via triage.llm.answer_question
