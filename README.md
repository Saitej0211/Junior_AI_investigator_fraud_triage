# Case Review: a Junior AI Investigator

Every case in the queue is already a fraud referral; most turn out to be false
alarms. The investigator's real problem is deciding which referrals deserve
their time. This notebook gives each case an AI risk score, a plain-English
explanation and a recommended next step, then lets the investigator review the
queue, ask questions and accept or override every call.

Everything is in one notebook, [fraud_triage.ipynb](fraud_triage.ipynb):

1. **The cases, and what no single row shows.** Loads the 50 cases and adds
   cross-case facts: other cases with the same claim number, and how the
   amount, visits and distance compare with the usual for that type of care.
2. **The AI** (Groq's free tier, gpt-oss-120b, falling back to gpt-oss-20b).
   For each case it returns a 0-100 risk score, a risk level, a short summary,
   the facts behind it, a recommended action and next steps, using only the
   facts it's given.
3. **Checking the AI's work.** Every fact the AI cites is checked against the
   data (✓ or ⚠). Hard evidence (double billing, overlapping visits, a shared
   claim number) can never be closed: the AI is told so, and any case it would
   close or rate Low anyway is flagged.
4. **Plain-English labels and the look of the app.**
5. **Review the queue**, an app inside the notebook: summary cards, filters
   (by risk level, "needs a closer look", hide reviewed), the queue highest AI
   risk first, and a sidebar for the open case (click its case ID). There you
   see the AI's score and reasons, accept or override its call with a reason,
   leave notes, see the decision history and ask follow-up questions (answered
   only from that case's facts, and checked the same way). Clean Low cases can
   be accepted in one click. Decisions and notes go to `outputs/audit.csv`, an
   append-only audit trail.

AI results are saved to `data/ai_assessments.json`, so the notebook opens
instantly and reviewers can browse every case without a key (only questions
need one). Delete the file to assess again (about 3 minutes).

## Run it in Google Colab

1. Open `fraud_triage.ipynb` in Colab (File → Upload notebook).
2. Add your Groq key under Secrets (🔑 in the left sidebar) as `GROQ_API_KEY`.
3. Runtime → Run all, and when asked, upload `data/sample_cases.csv` and
   `data/ai_assessments.json`.

Colab forgets uploaded files and the audit trail when the session ends.

## Run it locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env               # then add your GROQ_API_KEY
jupyter notebook fraud_triage.ipynb
```

Get a free Groq key at https://console.groq.com/keys. You can list several
keys, comma-separated (`GROQ_API_KEY=key1,key2`), for more free quota.

See [ASSUMPTIONS.md](ASSUMPTIONS.md) for the assumptions behind the design.
