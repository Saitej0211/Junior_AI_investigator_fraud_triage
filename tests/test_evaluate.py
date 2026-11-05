from scripts.evaluate import summarize


def _case(case_id, tier, ai_level, action, verified=(True,), issues=(), score=0, ai_score=None):
    return {"case_id": case_id, "tier": tier, "score": score, "ai_model": "m",
            "grounding_issues": list(issues),
            "assessment": {"risk_level": ai_level, "risk_score": ai_score, "recommended_action": action,
                           "summary": "one two three",
                           "key_indicators": [{"verified": v} for v in verified]}}


def test_summarize_counts_agreement_grounding_and_overrides():
    cases = [
        _case("C1", "High", "High", "Escalate to SIU", score=60, ai_score=70),
        _case("C2", "Medium", "High", "Escalate to SIU", verified=(True, False),
              issues=["The AI rates this High risk, but the automatic check rates it Medium.",
                      'The AI used technical wording ("peer")']),
        {"case_id": "C3", "tier": "Low", "assessment": None, "grounding_issues": []},
    ]
    decisions = [
        {"case_id": "C1", "decision": "accept"},
        {"case_id": "C2", "decision": "accept"},
        {"case_id": "C2", "decision": "override"},  # latest wins
    ]
    s = summarize(cases, decisions)
    assert (s["cases"], s["assessed"]) == (3, 2)
    assert s["agreement"][("Medium", "High")] == 1
    assert (s["citations"], s["citations_verified"]) == (3, 2)
    assert s["issues"]["Technical wording"] == 1
    assert s["issues"]["Disagrees with the automatic check"] == 1
    assert (s["decided"], s["overridden"]) == (2, 1)
    assert s["avg_summary_words"] == 3
    # Only C1 has an AI score: 10 points higher than the rules.
    assert (s["scored"], s["avg_score_gap"], s["ai_scored_higher"]) == (1, 10.0, 1)
