"""LLM calls (Groq): an assessment per case, and answers to questions about one case.

The LLM explains; it does not decide alone. It receives the raw row, the
rules score and cross-case facts, and must return JSON matching `Assessment`
or `Answer`. grounding.py then checks what it said against the data.
"""

import json
import os
import time
from typing import Literal

import pandas as pd
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from triage.config import PROJECT_ROOT
from triage.scoring import CaseScore

load_dotenv(PROJECT_ROOT / ".env")

RiskLevel = Literal["Low", "Medium", "High"]
Action = Literal["Close as false positive", "Request documentation", "Escalate to SIU"]


class Indicator(BaseModel):
    field: str  # exact field name from the facts
    value: str  # its value, as given
    why: str


class Assessment(BaseModel):
    summary: str
    key_indicators: list[Indicator]
    risk_score: int = Field(ge=0, le=100)  # the AI's own score, same bands as the rules
    risk_level: RiskLevel
    confidence: RiskLevel
    recommended_action: Action
    next_steps: list[str]
    open_questions: list[str]


class Answer(BaseModel):
    answer: str
    citations: list[Indicator]  # fields the answer relies on, checked like indicators
    answerable: bool  # False when the case data can't answer the question


class LLMError(RuntimeError):
    """The model couldn't produce a valid reply; callers fall back to rules only."""


class LLMConfigError(LLMError):
    """Missing key or rejected request: retrying or trying other cases won't help."""


# Shared by both prompts: the reader may know nothing about the data.
PLAIN_ENGLISH = """
How to write (the reader has never seen this data):
- Write like you are briefing a colleague who knows nothing about data or
  statistics: plain, everyday words about what happened in real life.
- summary: at most 3 short sentences (about 60 words): what stands out, how
  it compares with what is usual, and why the suggested action fits.
- Each "why" is one short sentence. next_steps and open_questions: at most 3
  each, one short sentence each.
- Say "the patient", "the provider", "similar cases", "usually", "the
  automatic check" and "risk level". Describe what happened ("the same
  service was billed twice") rather than naming a data item.
- Use dollars for amounts, miles for distances, and percentages of bills
  (write 0.44 as "44% of the bills").
- To compare with what is usual, use compared_with_usual (2.2 means "about
  2.2 times the usual") and typical_for_this_care_type; never do your own
  arithmetic. For big gaps quote both numbers ("$32,373, when about $4,400
  is usual"). referral_system_amount_vs_similar_pct is the referral system's
  own comparison and may disagree; if you use it, say it's theirs.
- Data key names (with underscores) never appear in text you write. They
  appear only in a cited item's "field", copied exactly from the facts.
"""

SYSTEM_PROMPT = """\
You are a junior fraud investigator at a long-term-care insurer doing first-pass
triage of referrals from an automated fraud engine; most turn out to be false
positives. A senior investigator reviews your work.

Rules:
- Use ONLY the facts provided. Never invent data, names, history or documents.
- Each key indicator's "field" is a key copied exactly from the facts, e.g.
  {"field": "weekly_visit_frequency", "value": "12", "why": "..."}. Its value
  is that key's value. Never put a description in "field".
- The automatic check has scored the case. Explain it; if you disagree with its
  risk level, say why in the summary.
- risk_score is YOUR own 0-100 judgement of how likely this is fraud, weighing
  the evidence yourself; it is shown next to the automatic check's score for
  comparison, so don't just copy risk_score_out_of_100. Same bands: under 20
  Low, 20-49 Medium, 50 or more High; your risk_level must match your score.
- If kept_open_by_hard_evidence is true, never recommend "Close as false
  positive"; kept_open_reason says what must be explained first.
- Warning signs at 0 are normal; none at all is evidence for a false positive.
- next_steps are concrete actions; open_questions are what this data can't answer.
- confidence is how clear-cut the evidence is, not how risky the case is.
""" + PLAIN_ENGLISH

QA_SYSTEM_PROMPT = """\
You are a junior fraud investigator answering a senior investigator's questions
about ONE referral.

Rules:
- Use ONLY the facts provided (this case, its score, cross-case facts and the
  earlier assessment). You can't see other cases, records or documents.
- If the facts can't answer, say so, set answerable to false and name the record
  that would. Never guess.
- citations list each fact your answer relies on: "field" is a key copied
  exactly from the facts (e.g. "weekly_visit_frequency"), "value" its value.
- Answer in 2-5 sentences. You advise; the investigator decides.
""" + PLAIN_ENGLISH

# A short shape description costs far fewer tokens than the full JSON schema;
# pydantic still validates every reply.
_INDICATOR = '{"field": str, "value": str, "why": str}'
SHAPES = {
    Assessment: ('{"summary": str, "key_indicators": [' + _INDICATOR + '], '
                 '"risk_score": int 0-100, "risk_level": "Low"|"Medium"|"High", "confidence": "Low"|"Medium"|"High", '
                 '"recommended_action": "Close as false positive"|"Request documentation"|'
                 '"Escalate to SIU", "next_steps": [str], "open_questions": [str]}'),
    Answer: '{"answer": str, "citations": [' + _INDICATOR + '], "answerable": bool}',
}

# Bump when the prompts change, so the batch re-assesses instead of reusing
# answers written to the old instructions.
PROMPT_VERSION = 8

# Main model first, then the smaller one. Free-tier rate limits are per model,
# so the batch gives each its own share of cases (see model_for).
DEFAULT_MODELS = "openai/gpt-oss-120b,openai/gpt-oss-20b"
REQUEST_TIMEOUT_S = 60


def _models() -> list[str]:
    return [m.strip() for m in os.getenv("GROQ_MODEL", DEFAULT_MODELS).split(",")]


def model_for(case_score: CaseScore) -> str:
    """Clear Low cases go to the smaller, faster model; the rest to the main one.

    The two models have separate rate limits, so the batch runs both at once.
    """
    models = _models()
    return models[-1] if case_score.tier == "Low" else models[0]


def to_plain(value):
    """pandas/numpy values -> JSON-friendly Python values."""
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    return value.item() if hasattr(value, "item") else value


# CSV columns renamed before the model sees them: the original name nudges it
# into writing "peer" in text meant for non-experts.
FIELD_ALIASES = {"amount_vs_peer_avg_pct": "referral_system_amount_vs_similar_pct"}


def case_fields(row: pd.Series) -> dict:
    return {FIELD_ALIASES.get(k, k): to_plain(v) for k, v in row.items()}


def build_case_facts(row: pd.Series, case_score: CaseScore, cross_case: dict) -> dict:
    return {
        "case": case_fields(row),
        "automatic_check": {
            "risk_score_out_of_100": case_score.score,
            "automatic_risk_level": case_score.tier,
            "kept_open_by_hard_evidence": case_score.floor_applied,
            "kept_open_reason": case_score.floor_reason,
            "warning_signs": [
                {"field": FIELD_ALIASES.get(t.column, t.column), "value": t.value,
                 "severity": t.severity}
                for t in case_score.triggered
            ],
        },
        "cross_case": cross_case,
    }


def _compact(obj) -> str:
    # No indentation: whitespace is tokens, and tokens are the free tier's limit.
    return json.dumps(obj, separators=(",", ":"))


def _keys() -> list[str]:
    """GROQ_API_KEY may hold several keys, comma-separated, tried in order."""
    return [k.strip() for k in os.getenv("GROQ_API_KEY", "").split(",") if k.strip()]


# (key number, model) pairs that hit the free-tier daily token limit, and keys
# Groq rejected. Skipped for the rest of the process (the daily limit is a
# rolling 24 hours), so calls go straight to a key or model that still works.
_OUT_FOR_TODAY: set[tuple[int, str]] = set()
_BAD_KEYS: set[int] = set()


def _generate_json(system: str, user: str, schema: type[BaseModel],
                   reasoning: str = "low", models: list[str] | None = None,
                   wait: bool = False) -> str:
    """Try each model in order, and for each model every key with quota left.

    A rate-limited key moves on to the next key at once. A batch lane
    (wait=True) then waits for its own model rather than eating into the other
    lane's limit; an investigator waiting on an answer moves to the next model.
    """
    import groq

    keys = _keys()
    if not keys:
        raise LLMConfigError("GROQ_API_KEY is not set in .env")

    def call(key: str, model: str, retries: int) -> str:
        # The SDK retries 429/5xx itself, honouring the server's retry-after.
        client = groq.Groq(api_key=key, timeout=REQUEST_TIMEOUT_S, max_retries=retries)
        response = client.chat.completions.create(
            model=model,
            temperature=0,
            reasoning_effort=reasoning,
            response_format={"type": "json_object"},
            messages=[{"role": "system",
                       "content": f"{system}\nReply with only JSON shaped like: "
                                  f"{SHAPES[schema]}"},
                      {"role": "user", "content": user}],
        )
        return response.choices[0].message.content

    busy = (groq.RateLimitError, groq.InternalServerError, groq.APITimeoutError,
            groq.APIConnectionError)
    models = models or _models()
    last_error: Exception | None = None
    for model in models:
        usable = [i for i in range(len(keys))
                  if i not in _BAD_KEYS and (i, model) not in _OUT_FOR_TODAY]
        for i in usable:
            try:
                return call(keys[i], model, retries=0)
            except busy as e:
                last_error = e
                if "per day" in str(e):
                    _OUT_FOR_TODAY.add((i, model))
            except (groq.AuthenticationError, groq.PermissionDeniedError) as e:
                last_error = e
                _BAD_KEYS.add(i)  # e.g. a revoked key; the others may still work
            except groq.NotFoundError as e:
                raise LLMConfigError(f"Groq doesn't offer model {model}: {e}") from e
        # Every key for this model is busy for the minute: a batch lane waits.
        waitable = [i for i in usable if i not in _BAD_KEYS and (i, model) not in _OUT_FOR_TODAY]
        if wait and waitable:
            return call(keys[waitable[0]], model, retries=5)

    if len(_BAD_KEYS) == len(keys):
        raise LLMConfigError(f"Groq rejected every key in GROQ_API_KEY: {last_error}")
    raise last_error or LLMError("No Groq key or model is available")


def _ask(system: str, user: str, schema: type[BaseModel], attempts: int,
         reasoning: str = "low", models: list[str] | None = None, wait: bool = False):
    """Call the LLM and parse its JSON, retrying on bad JSON or transient errors."""
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return schema.model_validate_json(
                _generate_json(system, user, schema, reasoning=reasoning, models=models,
                               wait=wait))
        except LLMError:
            raise  # configuration problem; retrying won't help
        except Exception as e:  # invalid JSON (ValidationError) or API error
            last_error = e
            if attempt < attempts - 1:
                time.sleep(2 * (attempt + 1))
    raise LLMError(f"No valid reply after {attempts} attempts: {last_error}")


def assess_case(row: pd.Series, case_score: CaseScore, cross_case: dict,
                model: str | None = None) -> Assessment:
    """Ask the LLM for an assessment of one case, on `model` only if given."""
    facts = build_case_facts(row, case_score, cross_case)
    # Low effort halves latency and tokens, and is enough to explain clear
    # cases. Medium cases are the ambiguous ones, where a real second opinion
    # matters, so the model thinks harder there (about 9 of 50 cases).
    reasoning = "medium" if case_score.tier == "Medium" else "low"
    return _ask(SYSTEM_PROMPT, f"Assess this referral.\n{_compact(facts)}", Assessment,
                attempts=3, reasoning=reasoning, wait=model is not None,
                # The lane's own model, then the other only as a last resort
                # (e.g. the lane's model is out of tokens for the day).
                models=[model, *(m for m in _models() if m != model)] if model else None)


# Earlier turns give follow-ups ("and the weekend bills?") their context; a few
# are enough and keep the prompt small.
MAX_HISTORY_TURNS = 6


def answer_question(row: pd.Series, case_score: CaseScore, cross_case: dict,
                    assessment: dict | None, question: str,
                    history: list[dict] | None = None) -> Answer:
    """Answer a question about one case from its facts and assessment only.

    history is earlier turns as [{"question": ..., "answer": ...}].
    """
    facts = build_case_facts(row, case_score, cross_case)
    if assessment:  # the gist is enough; the full record repeats the facts
        facts["earlier_ai_assessment"] = {k: assessment[k] for k in (
            "summary", "risk_level", "recommended_action", "next_steps", "open_questions")}
    turns = (history or [])[-MAX_HISTORY_TURNS:]
    user = (f"Case facts:\n{_compact(facts)}\n"
            f"Earlier questions:\n{_compact(turns)}\n"
            f"Question: {question}")
    return _ask(QA_SYSTEM_PROMPT, user, Answer, attempts=2)
