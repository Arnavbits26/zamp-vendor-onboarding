"""
Run Stage 1-3 (intake, completeness, document reading) on a test case.

    python -m app.run_stage3 test_cases/01_happy_path
    python -m app.run_stage3 --all
    python -m app.run_stage3 test_cases/02_trade_name_vs_legal_name --json out.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.completeness import check_completeness
from app.documents import read_documents
from app.events import Event, RunLog
from app.intake import load_submission
from app.llm import ExtractionError, get_reader

ROOT = Path(__file__).resolve().parents[1]
ICON = {"started": "▶", "done": "✔", "info": "·", "warn": "!", "error": "✘"}


def printer(ev: Event):
    print(f"  {ICON.get(ev.status, '·')} [{ev.stage}] {ev.message}")


def run_case(case_dir: Path, reader) -> dict:
    print(f"\n=== {case_dir.name} ===")
    log = RunLog(on_event=printer)
    sub = load_submission(case_dir, log)
    completeness = check_completeness(sub, log)
    docs = read_documents(sub, reader, log)

    print("\n  Extracted fields:")
    for slot, e in docs.items():
        f = {k: v for k, v in ((e["extraction"] or {}).get("fields") or {}).items() if v not in (None, "")}
        print(f"    {slot}: {json.dumps(f, ensure_ascii=False)}")
    return {"case": case_dir.name, "completeness": [r.to_dict() for r in completeness],
            "documents": {s: {"route": e["route"], "extraction": e["extraction"],
                              "rules": [r.to_dict() for r in e["rules"]]} for s, e in docs.items()},
            "events": [ev.to_dict() for ev in log.events]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("case", nargs="?", help="path to a test case folder")
    ap.add_argument("--all", action="store_true", help="run every test case")
    ap.add_argument("--json", help="also save the full result to this file")
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
        print(f"\nERROR: {e}")
        sys.exit(1)
    inner = getattr(reader, "inner", reader)
    inner.notify = lambda msg: print(f"  ! [model] {msg}", flush=True)
    backup = getattr(inner, "fallback_model", None)
    print(f"Using {type(inner).__name__} / {getattr(inner, 'model', '?')}"
          f"  (backup: {backup or 'none set'})")
    results = []
    try:
        for c in cases:
            results.append(run_case(c, reader))
    except ExtractionError as e:
        print(f"\nERROR: {e}")
        sys.exit(1)

    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2, ensure_ascii=False))
        print(f"\nSaved full result to {args.json}")


if __name__ == "__main__":
    main()
