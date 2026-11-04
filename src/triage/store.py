"""Investigator decisions and notes, kept in SQLite as an audit trail.

Overrides are the most valuable data the tool produces: later they become
labels for tuning the weights in config.py.
"""

# TODO (step 4): implement with sqlite3 (stdlib).
#   decisions(case_id, ai_risk_level, decision: accept|override,
#             final_risk_level, reason, investigator, created_at)
#   notes(case_id, text, investigator, created_at)


def record_decision(case_id: str, ai_risk_level: str, decision: str,
                    final_risk_level: str, reason: str = "") -> None:
    raise NotImplementedError("Step 4: decisions")


def add_note(case_id: str, text: str) -> None:
    raise NotImplementedError("Step 4: notes")


def get_case_history(case_id: str) -> dict:
    raise NotImplementedError("Step 4: history")
