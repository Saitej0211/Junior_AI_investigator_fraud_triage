"""Investigator UI. Reads the cached triage; never calls the scorer directly.

Usage: streamlit run app/streamlit_app.py
"""

import json

import pandas as pd
import streamlit as st

from triage.config import OUTPUT_DIR

TRIAGE_PATH = OUTPUT_DIR / "triage.json"
TIER_ICON = {"High": "🔴", "Medium": "🟠", "Low": "🟢"}

st.set_page_config(page_title="Case Review", layout="wide")
st.title("Morning triage")

if not TRIAGE_PATH.exists():
    st.error("No triage yet. Run `python scripts/run_triage.py` first.")
    st.stop()

cases = json.loads(TRIAGE_PATH.read_text())

counts = pd.Series([c["tier"] for c in cases]).value_counts()
cols = st.columns(3)
for col, tier in zip(cols, ["High", "Medium", "Low"]):
    col.metric(f"{TIER_ICON[tier]} {tier}", int(counts.get(tier, 0)))

for c in cases:
    if c["shares_claim_number_with"]:
        st.warning(f"{c['case_id']} shares its claim number with "
                   f"{', '.join(c['shares_claim_number_with'])}")

# TODO (step 3): replace with queue cards, filters, bulk-accept for Low.
# TODO (step 4): case detail view with accept/override and notes.
st.dataframe(
    pd.DataFrame([{
        "Case": c["case_id"],
        "Tier": f"{TIER_ICON[c['tier']]} {c['tier']}",
        "Score": c["score"],
        "Top signals": ", ".join(t["label"] for t in c["triggered"][:3]),
    } for c in cases]),
    hide_index=True,
    use_container_width=True,
)
