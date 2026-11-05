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
- There are no fraud labels, so the AI's risk score is a judgement, not a
  trained prediction. Investigator overrides in the audit trail are the labels
  to evaluate and improve it later.

## Risk and hard evidence

- The AI sets the risk score (0-100) and level: under 20 Low, 20-49 Medium,
  50 or more High. There is no separate rules score.
- Duplicate billing, overlapping providers and a claim number shared with
  another case are hard evidence and never closed: the AI is told so, and any
  case it would close or rate Low anyway is flagged to the investigator.
- Shared contact details, frequent visits, long distances and billing above
  similar claims are strong patterns; weekend billing, round-dollar billing, a
  recent policy change and prior claims are suspicious mainly in combination.
  The AI weighs these itself.

## AI

- Every fact the AI cites is checked against the data, and anything it can't
  confirm is shown to the investigator rather than hidden.
- "Usual" values are medians per care type over the 50 cases. Every case is a
  referral, so these are somewhat inflated by fraud; a real deployment would
  use medians over all claims.
- A free-tier LLM (Groq) is enough for 50 cases. The synthetic data means
  sending it to a free-tier API is acceptable; real claims data would need a
  paid tier with data-retention guarantees or a private deployment.
