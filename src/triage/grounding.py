"""Check an LLM assessment against the source data before anyone sees it.

Anything the model cites must exist in the CSV with the value it claims.
Failures are surfaced in the UI as warnings, never silently dropped, so the
investigator can see exactly which parts of the AI output to distrust.
"""

import re

import pandas as pd

from triage.llm import Assessment, to_plain
from triage.scoring import CaseScore

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def _values_match(cited: str, actual) -> bool:
    actual = to_plain(actual)
    if isinstance(actual, list):  # e.g. case ids sharing a claim number
        return bool(actual) and all(str(item) in cited for item in actual)
    if isinstance(actual, str):
        return cited.strip().lower() == actual.strip().lower()

    # Numeric: tolerate "$32,373", "+57%", "205 miles", and ratios written as
    # percentages ("44%" for 0.44).
    match = _NUMBER.search(cited.replace(",", ""))
    if not match:
        return False
    number = float(match.group())
    return any(abs(candidate - actual) < 1e-6 for candidate in (number, number / 100))


def _known_facts(row: pd.Series, case_score: CaseScore, cross_case: dict | None) -> dict:
    """Everything the model was given: the CSV row, rules score and cross-case facts, flattened."""
    facts = row.to_dict()
    facts.update(score_0_to_100=case_score.score, tier=case_score.tier)
    for key, value in (cross_case or {}).items():
        if isinstance(value, dict):
            facts.update(value)
        else:
            facts[key] = value
    return facts


def validate_assessment(row: pd.Series, case_score: CaseScore, assessment: Assessment,
                        cross_case: dict | None = None) -> tuple[dict, list[str]]:
    """Return the assessment with each indicator marked verified, plus any issues."""
    facts = _known_facts(row, case_score, cross_case)
    issues: list[str] = []
    indicators = []

    for ind in assessment.key_indicators:
        # Models sometimes cite the path they saw ("cross_case.median_amount_usd");
        # the last segment is the field name.
        field = ind.field.rsplit(".", 1)[-1]
        if field not in facts:
            verified = False
            issues.append(f"Cited field '{ind.field}' does not exist in the case data")
        elif not _values_match(ind.value, facts[field]):
            verified = False
            issues.append(f"Cited {field} = {ind.value}, "
                          f"but the case data says {to_plain(facts[field])}")
        else:
            verified = True
        indicators.append({**ind.model_dump(), "verified": verified})

    if assessment.risk_level != case_score.tier:
        issues.append(f"AI rated this {assessment.risk_level} but the rules engine "
                      f"rated it {case_score.tier}; review both")
    if case_score.floor_applied and assessment.risk_level == "Low":
        issues.append(f"AI rated this Low despite hard evidence ({case_score.floor_reason})")
    if assessment.recommended_action == "Close as false positive":
        if case_score.floor_applied:
            issues.append(f"AI recommends closing a case with hard evidence "
                          f"({case_score.floor_reason})")
        elif case_score.tier == "High":
            issues.append("AI recommends closing a case the rules engine rates High")

    return {**assessment.model_dump(), "key_indicators": indicators}, issues
