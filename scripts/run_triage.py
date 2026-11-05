"""The "overnight" batch job: score and assess every case, write outputs/triage.json.

The app only reads this file, so investigators see a finished triage the
moment they open it.

Clear Low cases go to the smaller model and the rest to the main one. The two
have separate free-tier rate limits, so both lanes run at the same time.

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
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

from triage.config import OUTPUT_DIR
from triage.data import load_cases
from triage.grounding import validate_assessment
from triage.llm import (PROMPT_VERSION, Assessment, LLMConfigError, LLMError, assess_case,
                        model_for)
from triage.pipeline import score_with_context

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
    scores, context = score_with_context(df)
    previous = {} if refresh else _load_previous()

    records: dict[str, dict] = {}
    finished: dict[str, dict] = {}  # what's safe to save mid-run
    todo: list[str] = []
    for case_id, cs in scores.items():
        record = cs.to_dict()
        cross_case = context[case_id]
        record["shares_claim_number_with"] = cross_case["shares_claim_number_with"]
        record["assessment"] = None
        record["grounding_issues"] = []
        record["ai_status"] = "skipped"
        record["ai_model"] = None
        record["ai_prompt_version"] = None
        records[case_id] = record

        # Reuse a successful assessment only if it was written to the current
        # prompt and the rules input it explained is unchanged.
        prior = previous.get(case_id)
        if (use_llm and prior and prior["ai_status"] == "ok"
                and prior.get("ai_prompt_version") == PROMPT_VERSION
                and prior["score"] == cs.score and prior["tier"] == cs.tier):
            # Re-check the cached answer against today's facts and checks; that
            # costs nothing and keeps old runs from hiding new grounding issues.
            record["assessment"], record["grounding_issues"] = validate_assessment(
                df.loc[case_id], cs, Assessment(**prior["assessment"]), cross_case)
            record["ai_status"] = "ok"
            record["ai_model"] = prior.get("ai_model")
            record["ai_prompt_version"] = PROMPT_VERSION
            finished[case_id] = record
        elif use_llm:
            todo.append(case_id)
        else:
            finished[case_id] = record

    if use_llm:
        print(f"  {len(scores) - len(todo)} cached, {len(todo)} to assess")
    lock = threading.Lock()
    stop = threading.Event()  # set on a config error: every call would fail

    def assess(case_id: str, model: str) -> None:
        cs, record = scores[case_id], records[case_id]
        row, cross_case = df.loc[case_id], context[case_id]
        try:
            assessment = assess_case(row, cs, cross_case, model=model)
            record["assessment"], record["grounding_issues"] = validate_assessment(
                row, cs, assessment, cross_case)
            record["ai_status"] = "ok"
            record["ai_model"] = model
            record["ai_prompt_version"] = PROMPT_VERSION
        except LLMConfigError:
            raise  # handled by run_lane: stops both lanes
        except LLMError as e:
            # The case still shows its rules score; the UI marks AI as unavailable.
            record["ai_status"] = f"unavailable: {e}"
        with lock:
            finished[case_id] = record
            print(f"  [{len(finished)}/{len(scores)}] {case_id} ({model}): "
                  f"{record['ai_status'][:50]}")
            # Keep not-yet-reached cases from the previous run, so stopping
            # midway never throws away assessments that already succeeded.
            _save(list({**previous, **finished}.values()))

    def run_lane(model: str, case_ids: list[str]) -> None:
        for case_id in case_ids:
            if stop.is_set():
                return
            try:
                assess(case_id, model)
            except LLMConfigError as e:
                # Missing or rejected key: every case would fail the same way.
                print(f"  AI disabled: {e}")
                stop.set()

    lanes: dict[str, list[str]] = defaultdict(list)
    for case_id in todo:
        lanes[model_for(scores[case_id])].append(case_id)
    with ThreadPoolExecutor(max_workers=max(len(lanes), 1)) as pool:
        for future in [pool.submit(run_lane, m, ids) for m, ids in lanes.items()]:
            future.result()
    use_llm = use_llm and not stop.is_set()

    results = list(records.values())
    _save(results)

    tiers = Counter(r["tier"] for r in results)
    print(f"Scored {len(results)} cases -> {OUT_PATH}")
    print(f"  High: {tiers['High']}  Medium: {tiers['Medium']}  Low: {tiers['Low']}")
    if use_llm:
        ok = sum(r["ai_status"] == "ok" for r in results)
        flagged = sum(bool(r["grounding_issues"]) for r in results)
        print(f"  AI assessments: {ok}/{len(results)} ok, "
              f"{flagged} with grounding issues")
        if ok < len(results):
            print("  Rerun the same command to retry only the failed cases.")
    for r in results:
        if r["shares_claim_number_with"]:
            print(f"  ! {r['case_id']} shares its claim number with "
                  f"{', '.join(r['shares_claim_number_with'])}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-llm", action="store_true", help="rules score only")
    parser.add_argument("--refresh", action="store_true", help="ignore cached assessments")
    args = parser.parse_args()
    main(use_llm=not args.no_llm, refresh=args.refresh)
