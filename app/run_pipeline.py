"""
Run the complete process on a test case.

    python -m app.run_pipeline test_cases/04_gst_state_mismatch
    python -m app.run_pipeline --all                 # runs all six and scores them
    python -m app.run_pipeline --all --no-llm-email  # skip the extra model call for emails
    python -m app.run_pipeline --all --json runs.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.events import Event, RunLog
from app.llm import ExtractionError, get_reader
from app.pipeline import run_submission
from app.store import DEFAULT_DB, Store

ROOT = Path(__file__).resolve().parents[1]
ICON = {"started": "▶", "done": "✔", "info": "·", "warn": "!", "error": "✘"}
MARK = {"PASS": "✔", "FAIL": "✘", "WARN": "!", "SKIP": "-"}


def printer(ev: Event):
    if ev.stage == "resubmission" or (ev.stage in ("documents",) and ev.status == "info"):
        print(f"  {ICON[ev.status]} [{ev.stage}] {ev.message}")
    elif ev.status != "info":
        print(f"  {ICON.get(ev.status, '·')} [{ev.stage}] {ev.message}")


def show_changes(c: dict):
    print(f"\n  --- What changed since version {c['from_version']} ---")
    print(f"  Outcome:  {c['status_before']}  ->  {c['status_after']}")
    if c["documents_changed"]:
        print(f"  Files the vendor changed:  {', '.join(c['documents_changed'])}")
    if c["documents_unchanged"]:
        print(f"  Files re-sent unchanged:   {', '.join(c['documents_unchanged'])}")
    for f in c["form_fields_changed"]:
        print(f"  Form field changed: {f['field']}: {f['old']!r} -> {f['new']!r}")
    for title, key in (("Problems fixed", "resolved"), ("Checks that became possible", "newly_checkable"),
                       ("NEW problems", "regressed"), ("Still failing", "still_failing")):
        for r in c[key]:
            print(f"  {title}: {r['rule_id']}  {r['name']}  ({r['was']} -> {r['now']})")


def show(result: dict, verbose: bool):
    print("\n  Checks that were not a plain PASS:" if any(r["status"] != "PASS" for r in result["rules"])
          else "\n  All checks passed.")
    for r in result["rules"]:
        if r["status"] != "PASS" or verbose:
            print(f"    {MARK[r['status']]} {r['rule_id']:<8} {r['status']:<5} {r['name']}\n"
                  f"{'':<22}{r['detail']}")
    if result.get("changes"):
        show_changes(result["changes"])
    print(f"\n  >>> DECISION: {result['status']}  -  {result['headline']}")
    v = result["messages"]["vendor"]
    print(f"\n  --- Email to vendor ({v['source']}) ---\n  Subject: {v['subject']}")
    print("\n".join("  " + l for l in v["body"].splitlines()))
    print("\n  --- Internal note ---")
    print("\n".join("  " + l for l in result["messages"]["internal"].splitlines()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("case", nargs="?")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--json")
    ap.add_argument("-v", "--verbose", action="store_true", help="also list every passing check")
    ap.add_argument("--no-llm-email", action="store_true")
    ap.add_argument("--no-save", action="store_true", help="do not record this run in data/runs.db")
    ap.add_argument("--reset", action="store_true", help="wipe run history and approved vendors first")
    ap.add_argument("--demo-resubmission", action="store_true",
                    help="reset, then run 05a (wrong document) followed by 05b (corrected) to show the loop")
    args = ap.parse_args()
    store = None if args.no_save else Store(DEFAULT_DB)
    if store and (args.reset or args.demo_resubmission):
        store.reset()
        print("Run history and approved vendors cleared.")
    if args.demo_resubmission:
        cases = [ROOT / "test_cases" / "05a_wrong_document", ROOT / "test_cases" / "05b_wrong_document_resubmitted"]
    elif args.all:
        cases = sorted(p.parent for p in (ROOT / "test_cases").glob("*/submission.json"))
    elif args.case:
        cases = [Path(args.case)]
    else:
        ap.error("give a test case folder, --all or --demo-resubmission")

    try:
        reader = get_reader()
    except ExtractionError as e:
        sys.exit(f"ERROR: {e}")
    inner = getattr(reader, "inner", reader)
    inner.notify = lambda m: print(f"  ! [model] {m}", flush=True)
    print(f"Using {type(inner).__name__} / {getattr(inner, 'model', '?')}  (backup: {getattr(inner, 'fallback_model', None) or 'none'})")

    expected = {m["case"]: m["expected_outcome"]
                for m in json.loads((ROOT / "test_cases" / "manifest.json").read_text())}
    results, board = [], []
    try:
        for c in cases:
            print(f"\n=== {c.name} ===")
            res = run_submission(c, reader, log=RunLog(on_event=printer), use_llm_email=not args.no_llm_email,
                                 store=store)
            show(res, args.verbose)
            results.append({"case": c.name, **res})
            board.append((c.name, expected.get(c.name, "?"), res["status"]))
    except ExtractionError as e:
        print(f"\nERROR: {e}")
        sys.exit(1)

    if len(board) > 1:
        print("\n=== SCOREBOARD ===")
        for name, exp, got in board:
            print(f"  {'✔' if exp == got else '✘'} {name:<34} expected {exp:<9} got {got}")
        ok = sum(e == g for _, e, g in board)
        print(f"\n  {ok}/{len(board)} cases gave the expected outcome")
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2, ensure_ascii=False))
        print(f"\nSaved to {args.json}")


if __name__ == "__main__":
    main()
