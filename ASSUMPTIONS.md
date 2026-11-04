# Assumptions

## Business

- Every case is already a referral from the fraud engine. The job is to decide
  which referrals deserve investigator time, not to find fraud among normal
  claims.
- An investigator works a queue of roughly 50 cases a day. Most referrals turn
  out to be false positives, so clearing them quickly is the biggest time
  saving.
- The AI recommends and the investigator decides. Nothing is closed or
  escalated without a human action.
- Possible outcomes are: close as a false positive, request documentation, or
  escalate to the Special Investigations Unit (SIU).

## Data

- A signal value of 0 means "not triggered" or "normal", as the brief states.
- The brief says there are 9 signals; the CSV has 10. All 10 are used.
- `amount_vs_peer_avg_pct` is already relative to peers of the same care type.
- A claim number should be unique. The same claim number appearing in two
  cases with different states and care types is treated as a data-integrity
  red flag (C1001 / C1031).
- There are no fraud labels, so weights are set by judgment and documented in
  `src/triage/config.py`. Investigator overrides would become labels for tuning
  them later.

## Scoring

- Duplicate billing, overlapping providers and a claim number shared with
  another case are hard evidence and never auto-cleared: the rules engine
  raises them to at least Medium, and the UI flags any AI recommendation to
  close them.
- Shared contact info, high visit frequency, distance and peer overbilling are
  strong patterns.
- Weekend billing, round-dollar billing, a recent policy change and prior
  claim count are circumstantial: suspicious mainly in combination.

## AI

- The rules score is the anchor; the LLM explains it and may disagree, but
  any disagreement is shown to the investigator rather than resolved silently.
- A free-tier LLM (Groq by default) is enough for 50 cases. The synthetic data
  means sending it to a free-tier API is acceptable; real claims data would
  need a paid tier with data-retention guarantees or a private deployment.
- "Normal" peer values are computed from the Low-tier cases only. Every case
  is a referral, so medians over the whole queue are inflated by fraud.
