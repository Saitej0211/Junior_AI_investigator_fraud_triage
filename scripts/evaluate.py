"""How well is the AI doing? Prints numbers for the write-up from outputs/triage.json
and, once investigators have used the app, outputs/decisions.db.

There are no fraud labels in the synthetic data, so this measures what can be
measured: coverage, agreement with the rules, grounding, readability and,
from real use, how often investigators override the AI.

Usage: python scripts/evaluate.py
"""

import json
import sqlite3
from collections import Counter

from triage import store
from triage.config import OUTPUT_DIR

TRIAGE_PATH = OUTPUT_DIR / "triage.json"
LEVELS = ["Low", "Medium", "High"]


def _issue_kind(issue: str) -> str:
    if "technical wording" in issue:
        return "Technical wording"
    if "doesn't match the risk level" in issue:
        return "AI score doesn't match its level"
    if "automatic check rates" in issue:
        return "Disagrees with the automatic check"
    if "closing" in issue or "Low risk even though" in issue:
        return "Would close a case with hard evidence"
    return "Cited fact not confirmed"


def summarize(cases: list[dict], decisions: list[dict]) -> dict:
    assessed = [c for c in cases if c["assessment"]]
    citations = [i for c in assessed for i in c["assessment"]["key_indicators"]]
    words = [len(c["assessment"]["summary"].split()) for c in assessed]
    # Older results have no AI score; compare only those that do.
    gaps = [c["assessment"]["risk_score"] - c["score"] for c in assessed
            if c["assessment"].get("risk_score") is not None]
    latest = {d["case_id"]: d for d in decisions}  # rows come oldest first
    return {
        "cases": len(cases),
        "assessed": len(assessed),
        "by_model": Counter(c.get("ai_model") or "unknown" for c in assessed),
        "agreement": Counter((c["tier"], c["assessment"]["risk_level"]) for c in assessed),
        "actions": Counter((c["tier"], c["assessment"]["recommended_action"]) for c in assessed),
        "citations": len(citations),
        "citations_verified": sum(i["verified"] for i in citations),
        "issues": Counter(_issue_kind(i) for c in assessed for i in c["grounding_issues"]),
        "cases_with_issues": sum(bool(c["grounding_issues"]) for c in assessed),
        "avg_summary_words": round(sum(words) / len(words)) if words else 0,
        "scored": len(gaps),
        "avg_score_gap": round(sum(abs(g) for g in gaps) / len(gaps), 1) if gaps else None,
        "ai_scored_higher": sum(g > 0 for g in gaps),
        "decided": len(latest),
        "overridden": sum(d["decision"] == "override" for d in latest.values()),
    }


def _pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}%" if whole else "n/a"


def _load_decisions() -> list[dict]:
    if not store.DB_PATH.exists():
        return []
    with sqlite3.connect(store.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM decisions ORDER BY id")]


def main() -> None:
    s = summarize(json.loads(TRIAGE_PATH.read_text()), _load_decisions())
    n = s["assessed"]
    agree = sum(v for (rules, ai), v in s["agreement"].items() if rules == ai)

    print(f"AI coverage: {n} of {s['cases']} cases assessed")
    for model, count in s["by_model"].most_common():
        print(f"  {model}: {count}")

    print(f"\nAgreement with the automatic check: {agree} of {n} ({_pct(agree, n)})")
    print("  rows = automatic check, columns = AI")
    print("  " + "".join(f"{lvl:>8}" for lvl in LEVELS))
    for rules in LEVELS:
        print(f"  {rules:<6}" + "".join(f"{s['agreement'][(rules, ai)]:>8}" for ai in LEVELS))

    if s["scored"]:
        print(f"AI score vs automatic score: {s['avg_score_gap']} points apart on average; "
              f"AI higher on {s['ai_scored_higher']} of {s['scored']}")

    print("\nSuggested action by risk level:")
    for rules in LEVELS:
        acts = {a: v for (t, a), v in s["actions"].items() if t == rules}
        print(f"  {rules}: " + ", ".join(f"{a} {v}" for a, v in sorted(acts.items())))

    print(f"\nGrounding: {s['citations_verified']} of {s['citations']} cited facts confirmed "
          f"({_pct(s['citations_verified'], s['citations'])})")
    print(f"Cases with a warning: {s['cases_with_issues']} of {n}")
    for kind, count in s["issues"].most_common():
        print(f"  {kind}: {count}")
    print(f"Average summary length: {s['avg_summary_words']} words")

    if s["decided"]:
        print(f"\nInvestigator decisions: {s['decided']} cases, "
              f"{s['overridden']} overridden ({_pct(s['overridden'], s['decided'])})")
    else:
        print("\nInvestigator decisions: none yet (override rate appears once the app is used)")


if __name__ == "__main__":
    main()
