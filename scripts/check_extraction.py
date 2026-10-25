"""
Compare what the model READ from each document with what we KNOW is on it.
Usage:  python scripts/check_extraction.py out.json
(out.json comes from:  python -m app.run_stage3 --all --json out.json)
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def n(v):
    return re.sub(r"[^A-Z0-9]", "", str(v or "").upper())


def expected_for(slot, sub):
    if slot == "gst_certificate":
        return {"gstin": sub["tax"]["gstin"]}
    if slot == "pan_card":
        return {"pan": sub["tax"]["pan"]}
    if slot == "cancelled_cheque":
        return {"account_number": sub["bank"]["account_number"], "ifsc": sub["bank"]["ifsc"]}
    if slot == "certificate_of_incorporation":
        return {"cin_or_llpin": sub["cin"], "pan": sub["tax"]["pan"]}
    return {}


def main(path):
    results = json.loads(Path(path).read_text())
    total = bad = 0
    for r in results:
        sub = json.loads((ROOT / "test_cases" / r["case"] / "submission.json").read_text())
        print(f"\n{r['case']}")
        for slot, d in r["documents"].items():
            ex = d.get("extraction") or {}
            doc01 = next((x for x in d["rules"] if x["rule_id"] == "DOC-01"), None)
            if doc01 and doc01["status"] == "FAIL":
                print(f"  {slot:<30} flagged as wrong document -> {doc01['detail']}")
                continue
            fields = ex.get("fields") or {}
            for key, want in expected_for(slot, sub).items():
                got = fields.get(key)
                total += 1
                if n(got) == n(want):
                    print(f"  {slot:<30} {key:<14} OK")
                else:
                    bad += 1
                    print(f"  {slot:<30} {key:<14} MISMATCH  read={got!r}  expected={want!r}")
            print(f"  {'':<30} route={d['route']}, legibility={ex.get('legibility')}, "
                  f"type={ex.get('doc_type')} ({ex.get('classification_confidence')})")
    print(f"\n{total - bad}/{total} identifiers read correctly")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "out.json")
