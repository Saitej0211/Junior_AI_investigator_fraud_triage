# Case Review: a Junior AI Investigator

Every case in the queue is already a fraud referral. The investigator's real
problem is deciding which referrals deserve their time. This tool triages the
queue overnight so that, when they open it in the morning, each case already
has a risk tier, the evidence behind it and a recommended next step. The
investigator stays in control: they accept or override every call.

## How it works

1. **Rules score** (`src/triage/scoring.py`). A transparent, weighted score
   over the 10 signals. Weights and thresholds live in `src/triage/config.py`,
   each with its reason. Hard evidence (duplicate billing, overlapping
   providers) can never be auto-cleared.
2. **Cross-case checks** (`src/triage/cross_case.py`). Facts that no single row
   shows, such as two cases sharing one claim number (which also floors the
   case to Medium), plus peer statistics by care type.
3. **LLM assessment** (`src/triage/llm.py`). The LLM explains the score in
   plain English, cites the fields behind it and recommends one of three
   actions, using only the facts it's given. It runs on Groq's free tier:
   clear Low cases go to gpt-oss-20b and the rest to gpt-oss-120b, and since
   each model has its own rate limit, both run at the same time. Compact
   prompts and low reasoning effort (more for Medium cases) keep it fast. If
   the AI is unavailable the case still shows its rules score.
4. **Grounding** (`src/triage/grounding.py`). Every value the LLM cites is
   checked against the CSV. Wrong values, invented fields and disagreements
   with the rules score are flagged in the UI, not hidden.
5. **API and UI** (`app/`). A FastAPI app serves the cached results as JSON
   and a plain HTML/JS page that reads them, so it loads instantly and needs
   no frontend build step. Interactive API docs are at `/docs`.
6. **Ask about a case** (`POST /api/cases/{id}/ask`). Click a case to open it
   and ask follow-up questions. The LLM answers only from that case's facts,
   says so when the data can't answer, and every field it cites is checked
   the same way as the assessment.
7. **Investigator decisions** (`src/triage/store.py`). The investigator accepts
   the AI's call or overrides it with a reason, and can add notes. Every
   decision is kept in SQLite (`outputs/decisions.db`) with who, when and what
   the AI had said: an audit trail, and later the labels for tuning the rules
   or training a model. The queue can be filtered to cases that need a closer
   look, and clean Low cases (rules and AI agree, nothing flagged) can be
   accepted in one step; the server re-checks each one.
8. **Evaluation** (`scripts/evaluate.py`). Prints AI coverage, agreement with
   the automatic check, how many cited facts were confirmed, warnings by kind
   and, once the app is used, the investigator override rate.

## Run it locally

Requires Python 3.10+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # then add your GROQ_API_KEY
python scripts/run_triage.py      # writes outputs/triage.json
uvicorn app.main:app --reload
```

Get a free Groq key at https://console.groq.com/keys. You can list several
keys, comma-separated (`GROQ_API_KEY=key1,key2`); when one runs out of quota
the next is used. The batch takes a few
minutes on the free tier; if it's interrupted, rerun the same command and it
resumes. Without a key, `python scripts/run_triage.py --no-llm` produces the
rules-only triage.

Open http://localhost:8000 for the app, or http://localhost:8000/docs for the API.

Run the tests with:

```bash
pytest
```

Print the evaluation report with:

```bash
python scripts/evaluate.py
```

## Project layout

```
data/            sample_cases.csv (50 synthetic referrals)
src/triage/      core logic, with no UI code
scripts/         batch triage job and evaluation report
app/             FastAPI app (main.py) and static UI (static/)
tests/           scoring, LLM/grounding, batch, decisions and API tests (all offline)
outputs/         triage results and decisions database (gitignored)
```

See [ASSUMPTIONS.md](ASSUMPTIONS.md) for the business assumptions behind the
design.
