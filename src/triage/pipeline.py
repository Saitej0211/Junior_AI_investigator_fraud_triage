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
    cross_case = {
        case_id: {
            "shares_claim_number_with": duplicates.get(case_id, []),
            "low_risk_peer_medians_for_care_type": peers.loc[df.loc[case_id, "care_type"]].to_dict(),
        }
        for case_id in scores
    }
    return scores, cross_case
