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

ROOT = Path(__file__).resolve().parents[1]
ICON = {"started": "▶", "done": "✔", "info": "·", "warn": "!", "error": "✘"}
MARK = {"PASS": "✔", "FAIL": "✘", "WARN": "!", "SKIP": "-"}


def printer(ev: Event):
    if ev.stage in ("documents",) and ev.status == "info":
        print(f"  {ICON[ev.status]} [{ev.stage}] {ev.message}")
    elif ev.status != "info":
        print(f"  {ICON.get(ev.status, '·')} [{ev.stage}] {ev.message}")


def show(result: dict, verbose: bool):
    print("\n  Checks that were not a plain PASS:" if any(r["status"] != "PASS" for r in result["rules"])
          else "\n  All checks passed.")
    for r in result["rules"]:
        if r["status"] != "PASS" or verbose:
            print(f"    {MARK[r['status']]} {r['rule_id']:<8} {r['status']:<5} {r['name']}\n"
                  f"{'':<22}{r['detail']}")
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
    args = ap.parse_args()
    if args.all:
        cases = sorted(p.parent for p in (ROOT / "test_cases").glob("*/submission.json"))
    elif args.case:
        cases = [Path(args.case)]
    else:
        ap.error("give a test case folder or --all")

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
            res = run_submission(c, reader, log=RunLog(on_event=printer), use_llm_email=not args.no_llm_email)
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
