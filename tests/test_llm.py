"""LLM and grounding tests. No network: the provider call is replaced with canned JSON."""

import json

import pytest

from triage import llm
from triage.data import load_cases
from triage.grounding import validate_assessment
from triage.llm import Assessment, LLMError, assess_case
from triage.scoring import apply_shared_claim_floor, score_all, score_case

GOOD = {
    "summary": "Duplicate billing and a 205-mile distance point to phantom visits.",
    "key_indicators": [
        {"field": "duplicate_service_billed", "value": "1", "why": "Billed twice"},
        {"field": "member_provider_distance_miles", "value": "205 miles", "why": "Too far"},
        {"field": "weekend_billing_ratio", "value": "44%", "why": "Ratio as a percent"},
        {"field": "amount_vs_peer_avg_pct", "value": "+57%", "why": "Above peers"},
    ],
    "risk_level": "High",
    "confidence": "High",
    "recommended_action": "Escalate to SIU",
    "next_steps": ["Pull visit logs"],
    "open_questions": ["Is the provider licensed in WA?"],
}


@pytest.fixture(scope="module")
def df():
    return load_cases()


@pytest.fixture(scope="module")
def scores(df):
    return score_all(df)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda _: None)


def test_assess_case_parses_valid_json(monkeypatch, df, scores):
    monkeypatch.setattr(llm, "_generate_json", lambda system, user: json.dumps(GOOD))
    result = assess_case(df.loc["C1006"], scores["C1006"], {})
    assert result.recommended_action == "Escalate to SIU"


def test_assess_case_retries_bad_json(monkeypatch, df, scores):
    replies = iter(["not json", json.dumps(GOOD)])
    monkeypatch.setattr(llm, "_generate_json", lambda system, user: next(replies))
    assert assess_case(df.loc["C1006"], scores["C1006"], {}).risk_level == "High"


def test_assess_case_gives_up_after_retries(monkeypatch, df, scores):
    monkeypatch.setattr(llm, "_generate_json", lambda system, user: "not json")
    with pytest.raises(LLMError):
        assess_case(df.loc["C1006"], scores["C1006"], {})


def test_grounding_accepts_correct_citations(df, scores):
    _, issues = validate_assessment(df.loc["C1006"], scores["C1006"], Assessment(**GOOD))
    assert issues == []


def test_grounding_flags_wrong_value_and_unknown_field(df, scores):
    bad = {**GOOD, "key_indicators": [
        {"field": "member_provider_distance_miles", "value": "20", "why": "wrong"},
        {"field": "provider_license_status", "value": "revoked", "why": "invented"},
    ]}
    checked, issues = validate_assessment(df.loc["C1006"], scores["C1006"], Assessment(**bad))
    assert [i["verified"] for i in checked["key_indicators"]] == [False, False]
    assert len(issues) == 2


def test_grounding_accepts_cross_case_facts(df, scores):
    cited = {**GOOD, "key_indicators": [
        {"field": "shares_claim_number_with", "value": "C1001", "why": "Same claim number"},
        {"field": "median_amount_usd", "value": "$3,077", "why": "Peer baseline"},
    ]}
    cross_case = {"shares_claim_number_with": ["C1001"],
                  "low_risk_peer_medians_for_care_type": {"median_amount_usd": 3077.0}}
    checked, _ = validate_assessment(df.loc["C1031"], scores["C1031"], Assessment(**cited),
                                     cross_case)
    assert [i["verified"] for i in checked["key_indicators"]] == [True, True]


def test_grounding_flags_disagreement_with_rules(df, scores):
    low = {**GOOD, "key_indicators": [], "risk_level": "Low",
           "recommended_action": "Close as false positive"}
    _, issues = validate_assessment(df.loc["C1006"], scores["C1006"], Assessment(**low))
    assert any("rules engine" in i for i in issues)


def test_grounding_accepts_prefixed_field_names(df, scores):
    cited = {**GOOD, "key_indicators": [
        {"field": "cross_case.median_amount_usd", "value": "$3,077", "why": "Peer baseline"},
        {"field": "rules_engine.score_0_to_100", "value": str(scores["C1031"].score),
         "why": "Rules score"},
    ]}
    cross_case = {"low_risk_peer_medians_for_care_type": {"median_amount_usd": 3077.0}}
    checked, _ = validate_assessment(df.loc["C1031"], scores["C1031"], Assessment(**cited),
                                     cross_case)
    assert [i["verified"] for i in checked["key_indicators"]] == [True, True]


def test_grounding_flags_closing_a_shared_claim_number(df):
    case_score = apply_shared_claim_floor(score_case(df.loc["C1001"]), ["C1031"])
    close = {**GOOD, "key_indicators": [], "risk_level": "Medium",
             "recommended_action": "Close as false positive"}
    _, issues = validate_assessment(df.loc["C1001"], case_score, Assessment(**close))
    assert any("C1031" in i and "closing" in i for i in issues)
