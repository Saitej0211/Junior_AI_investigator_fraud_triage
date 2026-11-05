"""Score every case and gather the cross-case facts the LLM sees.

Shared by the batch job and the API, so a question asked in the app is
answered from exactly the facts the overnight assessment used.
"""

import pandas as pd

from triage.cross_case import duplicate_claim_numbers, peer_stats
from triage.scoring import CaseScore, apply_shared_claim_floor, score_all


def score_with_context(df: pd.DataFrame) -> tuple[dict[str, CaseScore], dict[str, dict]]:
    """Return (rules score, cross-case facts) per case_id."""
    duplicates = duplicate_claim_numbers(df)
    scores = {case_id: apply_shared_claim_floor(cs, duplicates.get(case_id, []))
              for case_id, cs in score_all(df).items()}
    # "Normal" comes from cases the rules consider clean; the referral queue
    # itself is full of suspicious ones.
    peers = peer_stats(df, baseline_ids=[c for c, s in scores.items() if s.tier == "Low"])
    cross_case = {}
    for case_id in scores:
        row = df.loc[case_id]
        typical = peers.loc[row["care_type"]]
        cross_case[case_id] = {
            "shares_claim_number_with": duplicates.get(case_id, []),
            "typical_for_this_care_type": typical.to_dict(),
            # Pre-computed so the LLM never does its own arithmetic. Multiples
            # ("2.2 times the usual") because the model misreads "+115%" as
            # "115% of", and so would many readers.
            "compared_with_usual": {
                "amount_times_usual": _times(row["claim_amount_usd"],
                                             typical["typical_amount_usd"]),
                "visits_times_usual": _times(row["weekly_visit_frequency"],
                                             typical["typical_weekly_visits"]),
                "distance_times_usual": _times(row["member_provider_distance_miles"],
                                               typical["typical_distance_miles"]),
            },
        }
    return scores, cross_case


def _times(value: float, typical: float) -> float | None:
    return round(value / typical, 1) if typical else None


# Gaps from what's usual for the care type that are worth a look even when
# the automatic check didn't count them (it uses the referral system's own
# comparison for amounts). Each maps to the warning sign covering the same thing.
_GAPS = [
    ("amount_times_usual", "Amount", "amount_vs_peer_avg_pct"),
    ("visits_times_usual", "Visits", "weekly_visit_frequency"),
    ("distance_times_usual", "Distance", "member_provider_distance_miles"),
]
BIG_GAP = 2.0


def concerns(case_score: CaseScore, cross_case: dict) -> list[str]:
    """What stands out about a case, most serious first, in plain words."""
    items = []
    others = cross_case["shares_claim_number_with"]
    if others:
        items.append(f"Same claim number as {', '.join(others)}")
    items += [t.label for t in case_score.triggered]
    counted = {t.column for t in case_score.triggered}
    for key, what, sign in _GAPS:
        times = cross_case["compared_with_usual"][key]
        if times is not None and times >= BIG_GAP and sign not in counted:
            items.append(f"{what} {times:g}× the usual")
    return items
