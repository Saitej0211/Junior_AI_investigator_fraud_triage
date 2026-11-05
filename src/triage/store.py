"""Investigator decisions and notes, kept in SQLite as an append-only audit trail.

Nothing is updated or deleted: a new decision on a case is a new row, and the
latest row is the current one. Each decision stores what the AI said at the
time, so the trail still makes sense after the AI is re-run. Overrides are the
most valuable data the tool produces: later they become labels for tuning the
weights in config.py or training a model.
"""

import sqlite3
from datetime import datetime, timezone

from triage.config import OUTPUT_DIR

DB_PATH = OUTPUT_DIR / "decisions.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY,
    case_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('accept', 'override')),
    final_risk_level TEXT NOT NULL,
    final_action TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    ai_risk_level TEXT,
    ai_action TEXT,
    investigator TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    case_id TEXT NOT NULL,
    text TEXT NOT NULL,
    investigator TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    # A connection per call: FastAPI runs requests on several threads.
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record_decision(case_id: str, decision: str, final_risk_level: str, final_action: str,
                    investigator: str, reason: str = "", ai_risk_level: str | None = None,
                    ai_action: str | None = None) -> dict:
    row = dict(case_id=case_id, decision=decision, final_risk_level=final_risk_level,
               final_action=final_action, reason=reason, ai_risk_level=ai_risk_level,
               ai_action=ai_action, investigator=investigator, created_at=_now())
    with _connect() as conn:
        conn.execute(f"INSERT INTO decisions ({', '.join(row)}) "
                     f"VALUES ({', '.join('?' * len(row))})", list(row.values()))
    return row


def add_note(case_id: str, text: str, investigator: str) -> dict:
    row = dict(case_id=case_id, text=text, investigator=investigator, created_at=_now())
    with _connect() as conn:
        conn.execute("INSERT INTO notes (case_id, text, investigator, created_at) "
                     "VALUES (?, ?, ?, ?)", list(row.values()))
    return row


def latest_decisions() -> dict[str, dict]:
    """Current decision per case: the most recent row for each case_id."""
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM decisions ORDER BY id").fetchall()
    return {r["case_id"]: dict(r) for r in rows}


def case_history(case_id: str) -> dict:
    """Every decision and note on a case, oldest first."""
    with _connect() as conn:
        decisions = conn.execute("SELECT * FROM decisions WHERE case_id = ? ORDER BY id",
                                 (case_id,)).fetchall()
        notes = conn.execute("SELECT * FROM notes WHERE case_id = ? ORDER BY id",
                             (case_id,)).fetchall()
    return {"decisions": [dict(r) for r in decisions], "notes": [dict(r) for r in notes]}
