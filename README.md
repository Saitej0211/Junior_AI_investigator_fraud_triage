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
   actions, using only the facts it's given. Groq is the default (free tier,
   fast); set `LLM_PROVIDER` to `gemini` or `anthropic` to switch. Each
   provider has fallback models, a 60-second timeout and retries, and if the
   AI is unavailable the case still shows its rules score.
4. **Grounding** (`src/triage/grounding.py`). Every value the LLM cites is
   checked against the CSV. Wrong values, invented fields and disagreements
   with the rules score are flagged in the UI, not hidden.
5. **API and UI** (`app/`). A FastAPI app serves the cached results as JSON
   and a plain HTML/JS page that reads them, so it loads instantly and needs
   no frontend build step. Interactive API docs are at `/docs`.

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

Get a free Groq key at https://console.groq.com/keys. The batch takes a few
minutes on the free tier; if it's interrupted, rerun the same command and it
resumes. Without a key, `python scripts/run_triage.py --no-llm` produces the
rules-only triage.

Open http://localhost:8000 for the app, or http://localhost:8000/docs for the API.

Run the tests with:

```bash
pytest
```

## Project layout

```
data/            sample_cases.csv (50 synthetic referrals)
src/triage/      core logic, with no UI code
scripts/         batch triage job
app/             FastAPI app (main.py) and static UI (static/)
tests/           scoring, LLM/grounding (offline) and API tests
outputs/         generated triage results (gitignored)
```

See [ASSUMPTIONS.md](ASSUMPTIONS.md) for the business assumptions behind the
design.
