"""Check an LLM assessment against the source data before anyone sees it.

Anything the model cites must exist in the CSV with the value it claims.
Failures are surfaced in the UI, not silently dropped.
"""

# TODO (step 2): implement validate_assessment().
#   - every key_indicators[].field is a real column
#   - every cited value matches the row (tolerate formatting like 0.5 vs 50%)
#   - risk_level disagrees with the rules tier -> flag "AI and rules disagree"
#   - return the assessment plus a list of grounding issues


def validate_assessment(row, case_score, assessment: dict) -> tuple[dict, list[str]]:
    raise NotImplementedError("Step 2: grounding checks")
