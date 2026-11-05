import pytest

from triage import store


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "decisions.db")


def test_latest_decision_wins_and_history_keeps_all():
    store.record_decision("C1001", "accept", "Medium", "Request documentation", "Sam",
                          ai_risk_level="Medium", ai_action="Request documentation")
    store.record_decision("C1001", "override", "High", "Escalate to SIU", "Ana",
                          reason="Found a second duplicate", ai_risk_level="Medium",
                          ai_action="Request documentation")
    assert store.latest_decisions()["C1001"]["investigator"] == "Ana"
    assert [d["decision"] for d in store.case_history("C1001")["decisions"]] == ["accept",
                                                                                 "override"]


def test_notes_are_per_case():
    store.add_note("C1001", "Called provider", "Sam")
    assert store.case_history("C1001")["notes"][0]["text"] == "Called provider"
    assert store.case_history("C1002")["notes"] == []
