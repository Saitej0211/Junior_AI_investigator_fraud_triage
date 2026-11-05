"""LLM and grounding tests. No network: the provider call is replaced with canned JSON."""

import json

import pytest

from triage import llm
from triage.data import load_cases
from triage.grounding import validate_answer, validate_assessment
from triage.llm import Assessment, LLMError, assess_case
from triage.scoring import apply_shared_claim_floor, score_all, score_case

GOOD = {
    "summary": "Duplicate billing and a 205-mile distance point to phantom visits.",
    "key_indicators": [
        {"field": "duplicate_service_billed", "value": "1", "why": "Billed twice"},
        {"field": "member_provider_distance_miles", "value": "205 miles", "why": "Too far"},
        {"field": "weekend_billing_ratio", "value": "44%", "why": "Most bills on weekends"},
        {"field": "amount_vs_peer_avg_pct", "value": "+57%", "why": "Well above similar claims"},
    ],
    "risk_score": 80,
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
    monkeypatch.setattr(llm, "_generate_json", lambda system, user, schema, reasoning="low", **_: json.dumps(GOOD))
    result = assess_case(df.loc["C1006"], scores["C1006"], {})
    assert result.recommended_action == "Escalate to SIU"


def test_assess_case_retries_bad_json(monkeypatch, df, scores):
    replies = iter(["not json", json.dumps(GOOD)])
    monkeypatch.setattr(llm, "_generate_json", lambda system, user, schema, reasoning="low", **_: next(replies))
    assert assess_case(df.loc["C1006"], scores["C1006"], {}).risk_level == "High"


def test_assess_case_gives_up_after_retries(monkeypatch, df, scores):
    monkeypatch.setattr(llm, "_generate_json", lambda system, user, schema, reasoning="low", **_: "not json")
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
        {"field": "typical_amount_usd", "value": "$3,077", "why": "Peer baseline"},
    ]}
    cross_case = {"shares_claim_number_with": ["C1001"],
                  "typical_for_this_care_type": {"typical_amount_usd": 3077.0}}
    checked, _ = validate_assessment(df.loc["C1031"], scores["C1031"], Assessment(**cited),
                                     cross_case)
    assert [i["verified"] for i in checked["key_indicators"]] == [True, True]


def test_grounding_flags_disagreement_with_rules(df, scores):
    low = {**GOOD, "key_indicators": [], "risk_level": "Low",
           "recommended_action": "Close as false positive"}
    _, issues = validate_assessment(df.loc["C1006"], scores["C1006"], Assessment(**low))
    assert any("automatic check" in i for i in issues)


def test_grounding_accepts_prefixed_field_names(df, scores):
    cited = {**GOOD, "key_indicators": [
        {"field": "typical_for_this_care_type.typical_amount_usd", "value": "$3,077", "why": "Peer baseline"},
        {"field": "automatic_check.risk_score_out_of_100", "value": str(scores["C1031"].score),
         "why": "Rules score"},
    ]}
    cross_case = {"typical_for_this_care_type": {"typical_amount_usd": 3077.0}}
    checked, _ = validate_assessment(df.loc["C1031"], scores["C1031"], Assessment(**cited),
                                     cross_case)
    assert [i["verified"] for i in checked["key_indicators"]] == [True, True]


def test_grounding_flags_closing_a_shared_claim_number(df):
    case_score = apply_shared_claim_floor(score_case(df.loc["C1001"]), ["C1031"])
    close = {**GOOD, "key_indicators": [], "risk_level": "Medium",
             "recommended_action": "Close as false positive"}
    _, issues = validate_assessment(df.loc["C1001"], case_score, Assessment(**close))
    assert any("C1031" in i and "closing" in i for i in issues)


def test_answer_question_is_grounded(monkeypatch, df, scores):
    reply = {"answer": "The member lives 205 miles from the provider.", "answerable": True,
             "citations": [{"field": "member_provider_distance_miles", "value": "205",
                            "why": "Distance"},
                           {"field": "provider_license_status", "value": "revoked",
                            "why": "invented"}]}
    seen = {}

    def fake(system, user, schema, reasoning="low", **_):
        seen["user"] = user
        return json.dumps(reply)

    monkeypatch.setattr(llm, "_generate_json", fake)
    answer = llm.answer_question(df.loc["C1006"], scores["C1006"], {}, None,
                                 "How far away is the provider?",
                                 [{"question": "Earlier?", "answer": "Yes."}])
    assert "How far away is the provider?" in seen["user"] and "Earlier?" in seen["user"]

    checked, issues = validate_answer(df.loc["C1006"], scores["C1006"], answer)
    assert [c["verified"] for c in checked["citations"]] == [True, False]
    assert len(issues) == 1


def test_grounding_accepts_floor_fields(df):
    case_score = apply_shared_claim_floor(score_case(df.loc["C1001"]), ["C1031"])
    cited = {**GOOD, "risk_score": 35, "risk_level": "Medium",
             "recommended_action": "Request documentation",
             "key_indicators": [
                 {"field": "kept_open_by_hard_evidence", "value": "true", "why": "Kept open"},
                 {"field": "kept_open_reason",
                  "value": "the same claim number was also used on case C1031", "why": "Reason"},
             ]}
    checked, issues = validate_assessment(df.loc["C1001"], case_score, Assessment(**cited))
    assert [i["verified"] for i in checked["key_indicators"]] == [True, True]
    assert issues == []


def test_citations_get_plain_labels(df, scores):
    checked, _ = validate_assessment(df.loc["C1006"], scores["C1006"], Assessment(**GOOD))
    weekend = checked["key_indicators"][2]
    assert weekend["label"] == "Billing on weekends"
    assert weekend["shown"] == "44% of bills"


def test_grounding_flags_jargon_but_not_red_flag(df, scores):
    plain = {**GOOD, "summary": "The same service was billed twice, a red flag."}
    _, issues = validate_assessment(df.loc["C1006"], scores["C1006"], Assessment(**plain))
    assert issues == []

    jargon = {**GOOD, "summary": "Billing is far above peers; weekend_billing_ratio is high."}
    _, issues = validate_assessment(df.loc["C1006"], scores["C1006"], Assessment(**jargon))
    assert len(issues) == 1
    assert '"peers"' in issues[0] and "a field name" in issues[0]


def test_medium_cases_get_more_reasoning(monkeypatch, df, scores):
    seen = []
    monkeypatch.setattr(llm, "_generate_json",
                        lambda system, user, schema, reasoning="low", **_:
                        seen.append(reasoning) or json.dumps(GOOD))
    medium = next(c for c, s in scores.items() if s.tier == "Medium")
    assess_case(df.loc[medium], scores[medium], {})
    assess_case(df.loc["C1006"], scores["C1006"], {})
    assert seen == ["medium", "low"]


def test_grounding_accepts_no_warning_signs(df, scores):
    clean = next(c for c, s in scores.items() if not s.triggered)
    cited = {**GOOD, "risk_level": "Low", "recommended_action": "Close as false positive",
             "key_indicators": [{"field": "warning_signs", "value": "[]", "why": "Nothing unusual"}]}
    checked, _ = validate_assessment(df.loc[clean], scores[clean], Assessment(**cited))
    assert checked["key_indicators"][0]["verified"]


def test_grounding_flags_ai_score_that_doesnt_match_its_level(df, scores):
    _, issues = validate_assessment(df.loc["C1006"], scores["C1006"],
                                    Assessment(**{**GOOD, "risk_score": 35}))
    assert issues == ["The AI's score (35 out of 100) doesn't match the risk level it gave (High)"]


def test_ai_score_must_be_0_to_100():
    with pytest.raises(ValueError):
        Assessment(**{**GOOD, "risk_score": 140})
