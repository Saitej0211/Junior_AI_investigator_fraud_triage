"""The "overnight" batch job: score and assess every case, write outputs/triage.json.

The app only reads this file, so investigators see a finished triage the
moment they open it.

Results are saved after every case, and a rerun reuses AI assessments that
already succeeded, so an interrupted or rate-limited run picks up where it
stopped instead of paying for the same calls again.

Usage:
  python scripts/run_triage.py            # rules score + LLM assessment (resumes)
  python scripts/run_triage.py --refresh  # re-assess every case from scratch
  python scripts/run_triage.py --no-llm   # rules score only (no API key needed)
"""

import argparse
import json
import os
from collections import Counter

from triage.config import OUTPUT_DIR
from triage.cross_case import duplicate_claim_numbers, peer_stats
from triage.data import load_cases
from triage.grounding import validate_assessment
from triage.llm import Assessment, LLMConfigError, LLMError, assess_case
from triage.scoring import apply_shared_claim_floor, score_all

OUT_PATH = OUTPUT_DIR / "triage.json"


def _load_previous() -> dict[str, dict]:
    if not OUT_PATH.exists():
        return {}
    return {r["case_id"]: r for r in json.loads(OUT_PATH.read_text())}


def _save(results: list[dict]) -> None:
    OUT_PATH.parent.mkdir(exist_ok=True)
    ordered = sorted(results, key=lambda r: r["score"], reverse=True)
    tmp = OUT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(ordered, indent=2))
    tmp.replace(OUT_PATH)  # atomic, so the app never reads a half-written file


def main(use_llm: bool = True, refresh: bool = False) -> None:
    df = load_cases()
    duplicates = duplicate_claim_numbers(df)
    scores = {case_id: apply_shared_claim_floor(cs, duplicates.get(case_id, []))
              for case_id, cs in score_all(df).items()}
    peers = peer_stats(df, baseline_ids=[c for c, s in scores.items() if s.tier == "Low"])
    previous = {} if refresh else _load_previous()
    provider = os.getenv("LLM_PROVIDER", "groq").lower()

    results = []
    for i, (case_id, cs) in enumerate(scores.items(), start=1):
        record = cs.to_dict()
        record["shares_claim_number_with"] = duplicates.get(case_id, [])
        record["assessment"] = None
        record["grounding_issues"] = []
        record["ai_status"] = "skipped"
        record["ai_provider"] = None

        # Reuse a successful assessment only if the same provider wrote it and the
        # rules input it explained is unchanged; mixing providers in one queue
        # would make assessments inconsistent.
        prior = previous.get(case_id)
        row = df.loc[case_id]
        cross_case = {
            "shares_claim_number_with": record["shares_claim_number_with"],
            "low_risk_peer_medians_for_care_type": peers.loc[row["care_type"]].to_dict(),
        }
        if (use_llm and prior and prior["ai_status"] == "ok"
                and prior.get("ai_provider") == provider
                and prior["score"] == cs.score and prior["tier"] == cs.tier):
            # Re-check the cached answer against today's facts and checks; that
            # costs nothing and keeps old runs from hiding new grounding issues.
            record["assessment"], record["grounding_issues"] = validate_assessment(
                row, cs, Assessment(**prior["assessment"]), cross_case)
            record["ai_status"], record["ai_provider"] = "ok", provider
            print(f"  [{i}/{len(scores)}] {case_id}: cached")
        elif use_llm:
            try:
                assessment = assess_case(row, cs, cross_case)
                record["assessment"], record["grounding_issues"] = validate_assessment(
                    row, cs, assessment, cross_case)
                record["ai_status"] = "ok"
                record["ai_provider"] = provider
            except LLMConfigError as e:
                # Missing key or bad provider: every case would fail the same way.
                print(f"  AI disabled: {e}")
                use_llm = False
            except LLMError as e:
                # The case still shows its rules score; the UI marks AI as unavailable.
                record["ai_status"] = f"unavailable: {e}"
            print(f"  [{i}/{len(scores)}] {case_id}: {record['ai_status'][:60]}")

        results.append(record)
        # Keep not-yet-reached cases from the previous run, so stopping
        # midway never throws away assessments that already succeeded.
        _save(list({**previous, **{r["case_id"]: r for r in results}}.values()))

    _save(results)

    tiers = Counter(r["tier"] for r in results)
    print(f"Scored {len(results)} cases -> {OUT_PATH}")
    print(f"  High: {tiers['High']}  Medium: {tiers['Medium']}  Low: {tiers['Low']}")
    if use_llm:
        ok = sum(r["ai_status"] == "ok" for r in results)
        flagged = sum(bool(r["grounding_issues"]) for r in results)
        print(f"  AI assessments: {ok}/{len(results)} ok ({provider}), "
              f"{flagged} with grounding issues")
        if ok < len(results):
            print("  Rerun the same command to retry only the failed cases.")
    for case_id, others in duplicates.items():
        print(f"  ! {case_id} shares its claim number with {', '.join(others)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-llm", action="store_true", help="rules score only")
    parser.add_argument("--refresh", action="store_true", help="ignore cached assessments")
    args = parser.parse_args()
    main(use_llm=not args.no_llm, refresh=args.refresh)
