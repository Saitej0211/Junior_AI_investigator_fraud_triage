"""The "overnight" batch job: score every case and write outputs/triage.json.

The app only reads this file, so investigators see a finished triage the
moment they open it.

Usage: python scripts/run_triage.py
"""

import json
from collections import Counter

from triage.config import OUTPUT_DIR
from triage.cross_case import duplicate_claim_numbers
from triage.data import load_cases
from triage.scoring import score_all


def main() -> None:
    df = load_cases()
    scores = score_all(df)
    duplicates = duplicate_claim_numbers(df)

    results = []
    for case_id, cs in scores.items():
        record = cs.to_dict()
        record["shares_claim_number_with"] = duplicates.get(case_id, [])
        # TODO (step 2): record["assessment"] = assess_case(...) + grounding
        results.append(record)

    results.sort(key=lambda r: r["score"], reverse=True)
    OUTPUT_DIR.mkdir(exist_ok=True)
    out_path = OUTPUT_DIR / "triage.json"
    out_path.write_text(json.dumps(results, indent=2))

    tiers = Counter(r["tier"] for r in results)
    print(f"Scored {len(results)} cases -> {out_path}")
    print(f"  High: {tiers['High']}  Medium: {tiers['Medium']}  Low: {tiers['Low']}")
    for case_id, others in duplicates.items():
        print(f"  ! {case_id} shares its claim number with {', '.join(others)}")


if __name__ == "__main__":
    main()
