"""Claude call that turns a scored case into an investigator-ready assessment.

The LLM explains; it does not decide alone. It receives the raw row, the
rules score and cross-case facts, and must return JSON matching
ASSESSMENT_SCHEMA. grounding.py then checks what it said.
"""

# TODO (step 2): implement assess_case() with the Anthropic SDK.
#   - load ANTHROPIC_API_KEY / ANTHROPIC_MODEL from .env (python-dotenv)
#   - system prompt: only use the facts given; cite field names; say "not in data"
#   - user message: row values + CaseScore.to_dict() + duplicate/peer facts
#   - parse JSON, retry once on invalid output, else return a fallback that
#     shows the rules-only assessment marked "AI unavailable"

ASSESSMENT_SCHEMA = {
    "summary": "2-3 sentence plain-English summary for the investigator",
    "key_indicators": [
        {"field": "CSV column name", "value": "exact value from the case", "why": "one line"}
    ],
    "risk_level": "Low | Medium | High",
    "confidence": "Low | Medium | High",
    "recommended_action": "Close as false positive | Request documentation | Escalate to SIU",
    "next_steps": ["specific actions, e.g. request visit logs for weekends"],
    "open_questions": ["what the data can't answer"],
}


def assess_case(row, case_score, cross_case_facts) -> dict:
    raise NotImplementedError("Step 2: LLM assessment")


def answer_question(row, assessment, question: str, history: list[dict]) -> str:
    """Case-scoped chat: answers only from this case's data and peer stats."""
    raise NotImplementedError("Step 5: follow-up chat")
