"""Signal definitions, thresholds and weights.

Every number the scorer uses lives here, next to the reason for it, so a
reviewer can ask "why?" about any score and get an answer from one file.

Weights reflect how directly a signal points at fraud:
  3   = hard evidence (the billing itself is impossible or duplicated)
  2-2.5 = strong pattern (hard to explain innocently)
  1-1.5 = circumstantial (suspicious only in combination)
"""

from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_PATH = PROJECT_ROOT / "data" / "sample_cases.csv"
OUTPUT_DIR = PROJECT_ROOT / "outputs"


@dataclass(frozen=True)
class Signal:
    column: str
    label: str  # plain English: shown to investigators who don't know the data
    weight: float
    rationale: str
    # Numeric signals only. At or above `elevated` earns half the weight,
    # at or above `severe` earns the full weight. Binary signals (0/1) earn
    # the full weight when they are 1.
    elevated: float | None = None
    severe: float | None = None
    unit: str = ""

    @property
    def is_binary(self) -> bool:
        return self.elevated is None


SIGNALS: list[Signal] = [
    # Hard evidence
    Signal("duplicate_service_billed", "Same service billed twice", 3.0,
           "The same service was billed more than once."),
    Signal("service_overlap_other_provider", "Another provider billed for the same time", 3.0,
           "Another provider billed for the same time; the member can't be in two places."),
    # Strong patterns
    Signal("shared_contact_with_provider", "Patient and provider share contact details", 2.5,
           "Member and provider share a phone, address or email: possible collusion."),
    Signal("amount_vs_peer_avg_pct", "Amount compared with similar claims", 2.0,
           "Billing well above similar claims suggests upcoding or padding.",
           elevated=25, severe=75, unit="%"),
    Signal("weekly_visit_frequency", "Visits per week", 2.0,
           "More than one visit a day is rarely plausible for these care types.",
           elevated=7, severe=12, unit="/wk"),
    Signal("member_provider_distance_miles", "Distance between patient and provider", 2.0,
           "In-person care from far away is unusual and may mean visits didn't happen.",
           elevated=40, severe=100, unit=" mi"),
    # Circumstantial
    Signal("round_dollar_billing_ratio", "Bills in round dollar amounts", 1.5,
           "Invented bills tend to be round numbers; real ones are messy.",
           elevated=0.30, severe=0.60),
    Signal("weekend_billing_ratio", "Billing on weekends", 1.5,
           "Most of these services rarely run on weekends.",
           elevated=0.20, severe=0.40),
    Signal("recent_policy_change_flag", "Policy changed just before the claim", 1.5,
           "Coverage changed shortly before the claim."),
    Signal("prior_claims_last_12mo", "Claims in the past year", 1.0,
           "A pile of recent claims; weak on its own, since sick people claim often.",
           elevated=4, severe=8),
]

MAX_POINTS = sum(s.weight for s in SIGNALS)

# Tier cut-offs on the 0-100 score.
HIGH_THRESHOLD = 50
MEDIUM_THRESHOLD = 20

# Any of these alone forces at least Medium, whatever the total score:
# hard evidence should never be auto-cleared.
FLOOR_TO_MEDIUM = {"duplicate_service_billed", "service_overlap_other_provider"}
