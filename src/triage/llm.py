"""LLM call that turns a scored case into an investigator-ready assessment.

The LLM explains; it does not decide alone. It receives the raw row, the
rules score and cross-case facts, and must return JSON matching
`Assessment`. grounding.py then checks what it said against the CSV.

The provider is picked by LLM_PROVIDER in .env: "groq" (default: free tier,
fast, generous limits), "gemini" (free tier, small daily quota) or
"anthropic". All go through `_generate_json`, so the rest of the code never
knows which one ran.
"""

import json
import os
import time
from typing import Literal

import pandas as pd
from dotenv import load_dotenv
from pydantic import BaseModel

from triage.config import PROJECT_ROOT, SIGNALS
from triage.scoring import CaseScore

load_dotenv(PROJECT_ROOT / ".env")

RiskLevel = Literal["Low", "Medium", "High"]
Action = Literal["Close as false positive", "Request documentation", "Escalate to SIU"]


class Indicator(BaseModel):
    field: str  # CSV column name
    value: str  # value exactly as it appears in the case data
    why: str


class Assessment(BaseModel):
    summary: str
    key_indicators: list[Indicator]
    risk_level: RiskLevel
    confidence: RiskLevel
    recommended_action: Action
    next_steps: list[str]
    open_questions: list[str]


class LLMError(RuntimeError):
    """The model couldn't produce a valid assessment; callers fall back to rules only."""


class LLMConfigError(LLMError):
    """Missing key or unknown provider: retrying or trying other cases won't help."""


SYSTEM_PROMPT = """\
You are a junior fraud investigator at a long-term-care insurer. Every case you
see has already been referred by an automated fraud engine; most referrals turn
out to be false positives. Your job is a first-pass triage that a senior human
investigator will review.

Rules:
- Use ONLY the facts provided. Never invent data, names, history or documents.
- Every key indicator must cite a field name and its exact value. Use the bare
  field name exactly as it appears in the facts (e.g. "median_amount_usd", not
  "cross_case.median_amount_usd").
- A rules engine has already scored the case. Explain it; if you disagree with
  its tier, say why in the summary. Do not silently contradict it.
- If the rules engine applied a hard-evidence floor, never recommend
  "Close as false positive"; the floor reason says what must be explained first.
- Signals at 0 are normal. Absence of signals is evidence for a false positive.
- Be concise and specific. Investigators skim.
- recommended_action must be one of: "Close as false positive",
  "Request documentation", "Escalate to SIU".
- next_steps are concrete actions (e.g. "Request weekend visit logs for June").
- open_questions list what this data cannot answer.
- confidence reflects how clear-cut the evidence is, not how risky the case is.
"""


def to_plain(value):
    """pandas/numpy values -> JSON-friendly Python values."""
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    return value.item() if hasattr(value, "item") else value


def build_case_facts(row: pd.Series, case_score: CaseScore, cross_case: dict) -> dict:
    signal_info = {s.column: s for s in SIGNALS}
    return {
        "case": {k: to_plain(v) for k, v in row.items()},
        "rules_engine": {
            "score_0_to_100": case_score.score,
            "tier": case_score.tier,
            "hard_evidence_floor_applied": case_score.floor_applied,
            "hard_evidence_floor_reason": case_score.floor_reason,
            "triggered_signals": [
                {"field": t.column, "value": t.value, "severity": t.severity,
                 "meaning": signal_info[t.column].rationale}
                for t in case_score.triggered
            ],
        },
        "cross_case": cross_case,
    }


# Under heavy load a provider can accept a request and never answer; without
# a timeout the whole batch hangs on one case.
REQUEST_TIMEOUT_S = 60


def _generate_json(system: str, user: str) -> str:
    provider = os.getenv("LLM_PROVIDER", "groq").lower()
    if provider == "groq":
        return _groq(system, user)
    if provider == "gemini":
        return _gemini(system, user)
    if provider == "anthropic":
        return _anthropic(system, user)
    raise LLMConfigError(
        f"Unknown LLM_PROVIDER {provider!r}; use 'groq', 'gemini' or 'anthropic'")


def _with_schema(system: str) -> str:
    """For providers that don't enforce a schema themselves, spell it out."""
    schema = json.dumps(Assessment.model_json_schema())
    return f"{system}\nReply with only a JSON object matching this schema:\n{schema}"


def _strip_fence(text: str) -> str:
    """Tolerate a ```json fence around the object."""
    text = text.strip()
    return text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()


DEFAULT_GROQ_MODELS = "openai/gpt-oss-120b,qwen/qwen3.8-27b,openai/gpt-oss-20b"


def _groq(system: str, user: str) -> str:
    import groq

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise LLMConfigError("GROQ_API_KEY is not set in .env")

    # The SDK already retries 429/5xx with backoff before raising.
    client = groq.Groq(api_key=api_key, timeout=REQUEST_TIMEOUT_S, max_retries=2)
    # GROQ_MODEL may list fallbacks: free-tier limits are per model.
    models = [m.strip() for m in os.getenv("GROQ_MODEL", DEFAULT_GROQ_MODELS).split(",")]
    for model in models:
        try:
            response = client.chat.completions.create(
                model=model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": _with_schema(system)},
                          {"role": "user", "content": user}],
            )
            return _strip_fence(response.choices[0].message.content)
        except (groq.RateLimitError, groq.InternalServerError, groq.APITimeoutError,
                groq.APIConnectionError):
            if model == models[-1]:
                raise  # every model is busy: let assess_case back off and retry
        except (groq.AuthenticationError, groq.PermissionDeniedError, groq.NotFoundError) as e:
            raise LLMConfigError(f"Groq rejected the request ({model}): {e}") from e
        # BadRequestError (e.g. output that failed Groq's JSON check) propagates
        # to assess_case, which retries the whole call.


DEFAULT_GEMINI_MODELS = "gemini-3.6-flash,gemini-3.8-flash,gemini-flash-latest"


def _gemini(system: str, user: str) -> str:
    import httpx
    from google import genai
    from google.genai import errors, types

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise LLMConfigError("GEMINI_API_KEY is not set in .env")

    client = genai.Client(api_key=api_key,
                          http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_S * 1000))
    config = types.GenerateContentConfig(
        system_instruction=system,
        temperature=0,
        # Gemini enforces the schema itself, so malformed output is rare.
        response_mime_type="application/json",
        response_schema=Assessment,
    )
    # GEMINI_MODEL may list fallbacks: popular models often return 503 under load.
    models = [m.strip() for m in os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODELS).split(",")]
    for model in models:
        try:
            return client.models.generate_content(model=model, contents=user, config=config).text
        except (errors.ServerError, httpx.TimeoutException):
            if model == models[-1]:
                raise  # every model is busy: let assess_case back off and retry
        except errors.ClientError as e:
            if e.code == 429:
                # Free-tier quotas are per model, so another model may still have room.
                if model == models[-1]:
                    raise  # all exhausted: let assess_case back off and retry
                continue
            # 400/403/404: bad key or retired model; retrying won't help
            raise LLMConfigError(f"Gemini rejected the request ({model}): {e}") from e


def _anthropic(system: str, user: str) -> str:
    import anthropic

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise LLMConfigError("ANTHROPIC_API_KEY is not set in .env")

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
        max_tokens=1500,
        temperature=0,
        system=_with_schema(system),
        messages=[{"role": "user", "content": user}],
    )
    return _strip_fence(response.content[0].text)


def assess_case(row: pd.Series, case_score: CaseScore, cross_case: dict,
                attempts: int = 3) -> Assessment:
    """Ask the LLM for an assessment, retrying on bad JSON or transient API errors."""
    facts = build_case_facts(row, case_score, cross_case)
    user = f"Assess this referral.\n\n{json.dumps(facts, indent=2)}"

    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return Assessment.model_validate_json(_generate_json(SYSTEM_PROMPT, user))
        except LLMError:
            raise  # configuration problem; retrying won't help
        except Exception as e:  # invalid JSON (ValidationError) or API/rate-limit error
            last_error = e
            if attempt < attempts - 1:
                time.sleep(2 ** attempt * 5)
    raise LLMError(f"No valid assessment after {attempts} attempts: {last_error}")


def answer_question(row, assessment, question: str, history: list[dict]) -> str:
    """Case-scoped chat: answers only from this case's data and peer stats."""
    raise NotImplementedError("Step 5: follow-up chat")
