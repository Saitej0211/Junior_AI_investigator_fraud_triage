"""Plain-English names and values for every fact, for people who don't know the data.

The LLM and the grounding check work with exact field names; anything shown
to an investigator goes through here first.
"""

import pandas as pd

from triage.config import SIGNALS

FIELD_LABELS = {
    "case_id": "Case",
    "claim_number": "Claim number",
    "claim_date": "Claim date",
    "care_type": "Type of care",
    "claim_amount_usd": "Amount billed",
    "state": "State",
    **{s.column: s.label for s in SIGNALS},
    # Cross-case facts
    "shares_claim_number_with": "Same claim number also used on",
    "cases": "Clean cases used for comparison",
    "typical_amount_usd": "Typical amount for this type of care",
    "typical_weekly_visits": "Typical visits per week for this type of care",
    "typical_distance_miles": "Typical distance for this type of care",
    "amount_times_usual": "Amount compared with usual for this type of care",
    "visits_times_usual": "Visits compared with usual for this type of care",
    "distance_times_usual": "Distance compared with usual for this type of care",
    "referral_system_amount_vs_similar_pct": "Amount compared with similar claims",
    # Rules engine
    "risk_score_out_of_100": "Risk score (out of 100)",
    "automatic_risk_level": "Risk level from the automatic check",
    "kept_open_by_hard_evidence": "Kept open because of hard evidence",
    "kept_open_reason": "Why it's kept open",
}

_YES_NO = {s.column for s in SIGNALS if s.is_binary}
_SHARE_OF_BILLS = {"weekend_billing_ratio", "round_dollar_billing_ratio"}
_DOLLARS = {"claim_amount_usd", "typical_amount_usd"}
_MILES = {"member_provider_distance_miles", "typical_distance_miles"}


def label(field: str) -> str:
    return FIELD_LABELS.get(field, field.replace("_", " ").capitalize())


def format_value(field: str, value) -> str:
    """e.g. weekend_billing_ratio 0.44 -> "44% of bills"; flags -> "Yes"/"No"."""
    if hasattr(value, "item"):  # numpy scalar
        value = value.item()
    if value is None:
        return "None"
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    if isinstance(value, list):
        return ", ".join(map(str, value)) or "None"
    if isinstance(value, bool) or field in _YES_NO:
        return "Yes" if value else "No"
    if field in _SHARE_OF_BILLS:
        return f"{value:.0%} of bills"
    if field in ("amount_vs_peer_avg_pct", "referral_system_amount_vs_similar_pct"):
        return f"{abs(value):g}% {'above' if value >= 0 else 'below'} similar claims"
    if field.endswith("_times_usual"):
        return f"{value:g} times the usual"
    if field in _DOLLARS:
        return f"${value:,.0f}"
    if field in _MILES:
        return f"{value:g} miles"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)
