"""Load the referral queue and fail loudly if the CSV isn't what we expect."""

from pathlib import Path

import pandas as pd

from triage.config import DATA_PATH, SIGNALS

ATTRIBUTE_COLUMNS = [
    "case_id", "claim_number", "claim_date", "care_type", "claim_amount_usd", "state",
]
REQUIRED_COLUMNS = ATTRIBUTE_COLUMNS + [s.column for s in SIGNALS]


def load_cases(path: Path = DATA_PATH) -> pd.DataFrame:
    df = pd.read_csv(path)

    missing = set(REQUIRED_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"CSV is missing columns: {sorted(missing)}")

    if df["case_id"].duplicated().any():
        raise ValueError("case_id must be unique")

    df["claim_date"] = pd.to_datetime(df["claim_date"])
    signal_cols = [s.column for s in SIGNALS]
    df[signal_cols] = df[signal_cols].apply(pd.to_numeric, errors="raise")
    return df.set_index("case_id", drop=False)
