"""Batch job tests. No network: assess_case is replaced with a stub."""

import json
import threading

import pytest

import scripts.run_triage as run_triage
from triage.llm import Assessment, LLMConfigError

STUB = Assessment(summary="Nothing unusual.", key_indicators=[], risk_score=5, risk_level="Low",
                  confidence="High", recommended_action="Close as false positive",
                  next_steps=[], open_questions=[])


@pytest.fixture
def out_path(tmp_path, monkeypatch):
    path = tmp_path / "triage.json"
    monkeypatch.setattr(run_triage, "OUT_PATH", path)
    monkeypatch.setenv("GROQ_MODEL", "openai/gpt-oss-120b,openai/gpt-oss-20b")
    return path


def test_low_and_other_cases_run_on_separate_models_at_once(out_path, monkeypatch):
    calls = []
    threads = set()

    def fake(row, case_score, cross_case, model=None):
        calls.append((case_score.tier, model))
        threads.add(threading.get_ident())
        return STUB

    monkeypatch.setattr(run_triage, "assess_case", fake)
    run_triage.main(refresh=True)

    models_by_tier = {tier: {m for t, m in calls if t == tier} for tier, _ in calls}
    assert models_by_tier["Low"] == {"openai/gpt-oss-20b"}
    assert models_by_tier["High"] == models_by_tier["Medium"] == {"openai/gpt-oss-120b"}
    assert len(threads) == 2  # one lane per model
    saved = json.loads(out_path.read_text())
    assert len(saved) == 50 and all(r["ai_status"] == "ok" for r in saved)


def test_config_error_stops_both_lanes(out_path, monkeypatch):
    def fail(*args, **kwargs):
        raise LLMConfigError("GROQ_API_KEY is not set in .env")

    monkeypatch.setattr(run_triage, "assess_case", fail)
    run_triage.main(refresh=True)
    saved = json.loads(out_path.read_text())
    assert len(saved) == 50 and all(r["ai_status"] == "skipped" for r in saved)
