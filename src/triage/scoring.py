"""Deterministic, explainable risk score for a single case.

This runs before any LLM call. It gives the LLM a fixed anchor to explain,
and gives us something to check the LLM against.
"""

from dataclasses import asdict, dataclass

import pandas as pd

from triage.config import (
    FLOOR_TO_MEDIUM, HIGH_THRESHOLD, MAX_POINTS, MEDIUM_THRESHOLD, SIGNALS, Signal,
)


@dataclass
class TriggeredSignal:
    column: str
    label: str
    value: float
    severity: str  # "elevated" or "severe"
    points: float
    rationale: str


@dataclass
class CaseScore:
    case_id: str
    score: float  # 0-100
    tier: str  # "Low", "Medium", "High"
    triggered: list[TriggeredSignal]
    floor_applied: bool
    floor_reason: str | None = None  # why the tier was raised to Medium

    def to_dict(self) -> dict:
        return asdict(self)


def _evaluate(signal: Signal, value: float) -> TriggeredSignal | None:
    if signal.is_binary:
        if value != 1:
            return None
        severity, points = "severe", signal.weight
    elif value >= signal.severe:
        severity, points = "severe", signal.weight
    elif value >= signal.elevated:
        severity, points = "elevated", signal.weight / 2
    else:
        return None
    return TriggeredSignal(signal.column, signal.label, float(value), severity, points,
                           signal.rationale)


def _tier(score: float) -> str:
    if score >= HIGH_THRESHOLD:
        return "High"
    if score >= MEDIUM_THRESHOLD:
        return "Medium"
    return "Low"


def score_case(row: pd.Series) -> CaseScore:
    triggered = [t for s in SIGNALS if (t := _evaluate(s, row[s.column]))]
    score = round(100 * sum(t.points for t in triggered) / MAX_POINTS, 1)
    tier = _tier(score)

    floor_applied = tier == "Low" and any(t.column in FLOOR_TO_MEDIUM for t in triggered)
    if floor_applied:
        tier = "Medium"

    triggered.sort(key=lambda t: t.points, reverse=True)
    return CaseScore(row["case_id"], score, tier, triggered, floor_applied,
                     "duplicate billing or provider overlap" if floor_applied else None)


def apply_shared_claim_floor(case_score: CaseScore, shares_claim_with: list[str]) -> CaseScore:
    """A claim number reused across cases is hard evidence no single row shows.

    The row can look perfectly clean (C1001 scores 0), so without this the case
    would be auto-cleared and its twin investigated alone.
    """
    if shares_claim_with and case_score.tier == "Low":
        case_score.tier = "Medium"
        case_score.floor_applied = True
        case_score.floor_reason = f"claim number also billed on {', '.join(shares_claim_with)}"
    return case_score


def score_all(df: pd.DataFrame) -> dict[str, CaseScore]:
    return {case_id: score_case(row) for case_id, row in df.iterrows()}
