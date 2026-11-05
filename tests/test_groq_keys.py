"""Key and model fallback in llm._generate_json, with a fake Groq client (no network)."""

from types import SimpleNamespace

import groq
import httpx
import pytest

from triage import llm
from triage.llm import Assessment, LLMConfigError

REQUEST = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")


def _error(cls, status, message):
    return cls(message, response=httpx.Response(status, request=REQUEST), body=None)


PER_DAY = lambda: _error(groq.RateLimitError, 429, "Rate limit reached on tokens per day (TPD)")
PER_MINUTE = lambda: _error(groq.RateLimitError, 429, "Rate limit reached on tokens per minute")
BAD_KEY = lambda: _error(groq.AuthenticationError, 401, "Invalid API Key")


@pytest.fixture
def fake_groq(monkeypatch):
    """outcomes[(key, model)] is an exception factory, or missing for success."""
    outcomes, calls = {}, []

    class FakeGroq:
        def __init__(self, api_key, **_):
            self.chat = SimpleNamespace(completions=SimpleNamespace(
                create=lambda model, **__: self._create(api_key, model)))

        @staticmethod
        def _create(key, model):
            calls.append((key, model))
            if (key, model) in outcomes:
                raise outcomes[(key, model)]()
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=f"{key}/{model}"))])

    monkeypatch.setattr(groq, "Groq", FakeGroq)
    monkeypatch.setenv("GROQ_API_KEY", "k1,k2")
    monkeypatch.setattr(llm, "_OUT_FOR_TODAY", set())
    monkeypatch.setattr(llm, "_BAD_KEYS", set())
    return outcomes, calls


def generate(**kwargs):
    return llm._generate_json("system", "user", Assessment, models=["big", "small"], **kwargs)


def test_first_key_is_used_while_it_has_quota(fake_groq):
    assert generate() == "k1/big"


def test_out_of_quota_key_moves_to_next_key_and_is_skipped_after(fake_groq):
    outcomes, calls = fake_groq
    outcomes[("k1", "big")] = PER_DAY
    assert generate() == "k2/big"
    calls.clear()
    assert generate() == "k2/big"
    assert calls == [("k2", "big")]  # k1 not tried again for this model


def test_per_minute_limit_also_tries_next_key(fake_groq):
    fake_groq[0][("k1", "big")] = PER_MINUTE
    assert generate() == "k2/big"


def test_both_keys_out_moves_to_next_model(fake_groq):
    outcomes, _ = fake_groq
    outcomes[("k1", "big")] = outcomes[("k2", "big")] = PER_DAY
    assert generate() == "k1/small"


def test_rejected_key_is_skipped_but_all_rejected_is_config_error(fake_groq):
    outcomes, _ = fake_groq
    outcomes[("k1", "big")] = BAD_KEY
    assert generate() == "k2/big"
    outcomes[("k2", "big")] = outcomes[("k2", "small")] = BAD_KEY
    with pytest.raises(LLMConfigError):
        generate()
