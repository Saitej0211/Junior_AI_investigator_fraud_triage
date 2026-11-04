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
   shows, such as two cases sharing one claim number, plus peer statistics by
   care type.
3. **LLM assessment** (`src/triage/llm.py`, *in progress*). Claude explains
   the score in plain English and recommends the next step, using only the
   facts it's given.
4. **Grounding** (`src/triage/grounding.py`, *in progress*). Every value the
   LLM cites is checked against the CSV. Disagreements with the rules score
   are flagged, not hidden.
5. **UI** (`app/streamlit_app.py`). Reads the cached results, so it loads
   instantly.

## Run it locally

Requires Python 3.10+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # then add your ANTHROPIC_API_KEY
python scripts/run_triage.py      # writes outputs/triage.json
streamlit run app/streamlit_app.py
```

Open http://localhost:8501.

Run the tests with:

```bash
pytest
```

## Project layout

```
data/            sample_cases.csv (50 synthetic referrals)
src/triage/      core logic, with no UI code
scripts/         batch triage job
app/             Streamlit UI
tests/           scoring and cross-case tests
outputs/         generated triage results (gitignored)
```

See [ASSUMPTIONS.md](ASSUMPTIONS.md) for the business assumptions behind the
design.
