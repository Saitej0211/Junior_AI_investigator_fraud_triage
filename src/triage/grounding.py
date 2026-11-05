"""Check an LLM assessment against the source data before anyone sees it.

Anything the model cites must exist in the CSV with the value it claims.
Failures are surfaced in the UI as warnings, never silently dropped, so the
investigator can see exactly which parts of the AI output to distrust.
"""

import re

import pandas as pd

from triage.labels import format_value, label
from triage.llm import Answer, Assessment, Indicator, case_fields, to_plain
from triage.scoring import CaseScore, tier_for

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")

# Words a reader without data training won't follow; the prompt asks for plain
# replacements, and this catches what slips through. ("Flagged" is everyday English.)
_JARGON = re.compile(r"\b(?:tiers?|floor|medians?|signals?|ratios?|peers?|rules engine)\b"
                     r"|\w_\w", re.IGNORECASE)


def _jargon_issue(texts: list[str]) -> list[str]:
    found = sorted({m.group(0).lower() for t in texts for m in _JARGON.finditer(t)})
    if not found:
        return []
    shown = ["a field name" if "_" in w else f'"{w}"' for w in found]
    return [f"The AI used technical wording ({', '.join(dict.fromkeys(shown))})"]


def _values_match(cited: str, actual) -> bool:
    actual = to_plain(actual)
    if isinstance(actual, bool):
        return cited.strip().lower() in (("true", "yes") if actual else ("false", "no"))
    if actual is None:
        return False
    if isinstance(actual, list):  # e.g. case ids sharing a claim number
        if not actual:  # "no warning signs" is a real, checkable fact
            return cited.strip().lower() in ("[]", "none", "0", "no", "")
        return all(str(item) in cited for item in actual)
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
    # The original CSV names too, in case the model cites one of those.
    facts = {**row.to_dict(), **case_fields(row)}
    facts.update(risk_score_out_of_100=case_score.score, automatic_risk_level=case_score.tier,
                 warning_signs=[t.column for t in case_score.triggered],
                 kept_open_by_hard_evidence=case_score.floor_applied,
                 kept_open_reason=case_score.floor_reason)
    for key, value in (cross_case or {}).items():
        if isinstance(value, dict):
            facts.update(value)
        else:
            facts[key] = value
    return facts


def _check_citations(cited: list[Indicator], facts: dict) -> tuple[list[dict], list[str]]:
    """Mark each cited field verified or not against the facts the model was given."""
    issues: list[str] = []
    indicators = []

    for ind in cited:
        # Models sometimes cite the path they saw ("typical_for_this_care_type.typical_amount_usd");
        # the last segment is the field name.
        field = ind.field.rsplit(".", 1)[-1]
        if field not in facts:
            verified = False
            issues.append(f"The AI mentioned \"{label(field)}\", which isn't in the case data")
        elif not _values_match(ind.value, facts[field]):
            verified = False
            issues.append(f"The AI said {label(field).lower()} is {ind.value}, "
                          f"but the case data says {format_value(field, facts[field])}")
        else:
            verified = True
        # label/shown are what people see; field/value stay for the audit trail.
        shown = format_value(field, facts[field]) if verified else ind.value
        indicators.append({**ind.model_dump(), "verified": verified,
                           "label": label(field), "shown": shown})
    return indicators, issues


def validate_assessment(row: pd.Series, case_score: CaseScore, assessment: Assessment,
                        cross_case: dict | None = None) -> tuple[dict, list[str]]:
    """Return the assessment with each indicator marked verified, plus any issues."""
    indicators, issues = _check_citations(
        assessment.key_indicators, _known_facts(row, case_score, cross_case))

    issues += _jargon_issue([assessment.summary, *assessment.next_steps,
                             *assessment.open_questions,
                             *(i.why for i in assessment.key_indicators)])
    if tier_for(assessment.risk_score) != assessment.risk_level:
        issues.append(f"The AI's score ({assessment.risk_score} out of 100) doesn't match "
                      f"the risk level it gave ({assessment.risk_level})")
    if assessment.risk_level != case_score.tier:
        issues.append(f"The AI rates this {assessment.risk_level} risk "
                      f"({assessment.risk_score} out of 100), but the automatic check rates it "
                      f"{case_score.tier} ({case_score.score:.0f} out of 100). Worth a closer look.")
    if case_score.floor_applied and assessment.risk_level == "Low":
        issues.append(f"The AI rates this Low risk even though {case_score.floor_reason}")
    if assessment.recommended_action == "Close as false positive":
        if case_score.floor_applied:
            issues.append(f"The AI suggests closing this case even though "
                          f"{case_score.floor_reason}")
        elif case_score.tier == "High":
            issues.append("The AI suggests closing a case the automatic check rates "
                          "High risk")

    return {**assessment.model_dump(), "key_indicators": indicators}, issues


def validate_answer(row: pd.Series, case_score: CaseScore, answer: Answer,
                    cross_case: dict | None = None) -> tuple[dict, list[str]]:
    """Return a chat answer with each citation marked verified, plus any issues."""
    citations, issues = _check_citations(
        answer.citations, _known_facts(row, case_score, cross_case))
    issues += _jargon_issue([answer.answer, *(c.why for c in answer.citations)])
    return {**answer.model_dump(), "citations": citations}, issues
