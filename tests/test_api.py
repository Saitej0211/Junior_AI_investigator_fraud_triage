import json

import pytest
from fastapi.testclient import TestClient

import app.main
import scripts.run_triage
from triage import store
from triage.llm import Answer, LLMError


CLEAN = {"summary": "Nothing unusual.", "key_indicators": [], "risk_score": 5,
         "risk_level": "Low",
         "confidence": "High", "recommended_action": "Close as false positive",
         "next_steps": [], "open_questions": []}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    # Temp files, so tests never touch the real outputs/triage.json or decisions.db.
    outputs = tmp_path_factory.mktemp("outputs")
    triage_path = outputs / "triage.json"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(scripts.run_triage, "OUT_PATH", triage_path)
        mp.setattr(app.main, "TRIAGE_PATH", triage_path)
        mp.setattr(store, "DB_PATH", outputs / "decisions.db")
        scripts.run_triage.main(use_llm=False)
        # Give two clean Low cases an AI assessment, so they can be bulk-accepted.
        cases = json.loads(triage_path.read_text())
        for c in cases:
            if c["case_id"] in ("C1002", "C1003"):
                c["assessment"], c["ai_status"] = CLEAN, "ok"
        triage_path.write_text(json.dumps(cases))
        yield TestClient(app.main.app)


def test_queue_sorted_by_risk(client):
    cases = client.get("/api/cases").json()
    assert len(cases) == 50
    scores = [c["score"] for c in cases]
    assert scores == sorted(scores, reverse=True)


def test_case_detail_includes_claim_data(client):
    case = client.get("/api/cases/C1031").json()
    assert case["claim"]["claim_number"] == "LTC-2034786"
    assert case["shares_claim_number_with"] == ["C1001"]


def test_unknown_case_is_404(client):
    assert client.get("/api/cases/NOPE").status_code == 404


def test_ask_returns_checked_answer(client, monkeypatch):
    def fake(row, case_score, cross_case, assessment, question, history):
        assert cross_case["shares_claim_number_with"] == ["C1001"]
        return Answer(answer="Shares a claim number with C1001.", answerable=True,
                      citations=[{"field": "shares_claim_number_with", "value": "C1001",
                                  "why": "Duplicate claim"}])

    monkeypatch.setattr(app.main, "answer_question", fake)
    res = client.post("/api/cases/C1031/ask", json={"question": "Any duplicates?"})
    assert res.status_code == 200
    body = res.json()
    assert body["citations"][0]["verified"] is True
    assert body["grounding_issues"] == []


def test_ask_reports_ai_unavailable(client, monkeypatch):
    def fail(*args):
        raise LLMError("rate limited")

    monkeypatch.setattr(app.main, "answer_question", fail)
    assert client.post("/api/cases/C1031/ask", json={"question": "Why?"}).status_code == 502


def test_ask_unknown_case_is_404(client):
    assert client.post("/api/cases/NOPE/ask", json={"question": "Why?"}).status_code == 404


def test_override_needs_reason(client):
    res = client.post("/api/cases/C1006/decision",
                      json={"decision": "override", "investigator": "Sam", "risk_level": "Low",
                            "action": "Close as false positive"})
    assert res.status_code == 422


def test_override_and_note_are_kept_in_history(client):
    client.post("/api/cases/C1006/decision",
                json={"decision": "override", "investigator": "Sam", "risk_level": "Medium",
                      "action": "Request documentation", "reason": "Provider explained distance"})
    client.post("/api/cases/C1006/notes", json={"text": "Called provider", "investigator": "Sam"})
    case = client.get("/api/cases/C1006").json()
    assert case["decision"]["final_action"] == "Request documentation"
    assert case["history"]["notes"][0]["text"] == "Called provider"
    queue = {c["case_id"]: c for c in client.get("/api/cases").json()}
    assert queue["C1006"]["decision"]["decision"] == "override"


def test_accept_without_ai_assessment_is_refused(client):
    res = client.post("/api/cases/C1031/decision", json={"decision": "accept", "investigator": "Sam"})
    assert res.status_code == 409


def test_bulk_accept_only_takes_clean_low_cases(client):
    queue = {c["case_id"]: c for c in client.get("/api/cases").json()}
    assert queue["C1002"]["clean_low"] and not queue["C1006"]["clean_low"]

    res = client.post("/api/bulk-accept", json={"case_ids": ["C1002", "C1003", "C1006", "C1001"],
                                                "investigator": "Sam"}).json()
    assert res == {"accepted": ["C1002", "C1003"], "skipped": ["C1006", "C1001"]}
    # Already decided: a second bulk-accept doesn't record it again.
    again = client.post("/api/bulk-accept", json={"case_ids": ["C1002"], "investigator": "Sam"})
    assert again.json()["accepted"] == []


def test_override_that_matches_ai_is_refused(client):
    res = client.post("/api/cases/C1003/decision",
                      json={"decision": "override", "investigator": "Sam", "risk_level": "Low",
                            "action": "Close as false positive", "reason": "Same as AI"})
    assert res.status_code == 422


def test_queue_lists_concerns_including_shared_claim_number(client):
    queue = {c["case_id"]: c for c in client.get("/api/cases").json()}
    assert queue["C1001"]["concerns"][0] == "Same claim number as C1031"
    assert "Amount 2.2× the usual" in queue["C1009"]["concerns"]
    assert queue["C1002"]["concerns"] == []
