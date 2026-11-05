"""Checks that need the whole queue, not one row.

A per-case LLM can't see these, which is why they're computed here and
handed to it as facts.
"""

import pandas as pd


def duplicate_claim_numbers(df: pd.DataFrame) -> dict[str, list[str]]:
    """Map each case_id to the other case_ids that share its claim number."""
    groups = df.groupby("claim_number")["case_id"].apply(list)
    shared = groups[groups.str.len() > 1]
    return {
        case_id: [other for other in case_ids if other != case_id]
        for case_ids in shared
        for case_id in case_ids
    }


def peer_stats(df: pd.DataFrame, baseline_ids: list[str] | None = None) -> pd.DataFrame:
    """Median claim amount and visit frequency per care type, for 'vs normal' context.

    Every case here is a fraud referral, so medians over the whole queue are
    pulled up by the fraudulent ones. Pass `baseline_ids` (e.g. the Low-tier
    cases) to compute "normal" from clean cases only.
    """
    if baseline_ids is not None:
        df = df.loc[baseline_ids]
    return df.groupby("care_type").agg(
        cases=("case_id", "count"),
        typical_amount_usd=("claim_amount_usd", "median"),
        typical_weekly_visits=("weekly_visit_frequency", "median"),
        typical_distance_miles=("member_provider_distance_miles", "median"),
    )
