# Vendor Onboarding — Zamp ASA Case Study (PS-2)

Takes a vendor submission (form data + documents) and returns **APPROVED / PENDING / REJECTED** with every check visible and cited, plus a drafted message back to the vendor.

## Status
- [x] Day 2 — test data, specimen documents, format validators (+ tests)
- [x] Day 3 — intake, completeness checks, document classification & extraction (Claude)
- [ ] Day 4 — cross-reference + risk checks, decision engine, message drafting
- [ ] Day 5 — edge cases end-to-end, resubmission loop
- [ ] Day 6 — UI (live run view + dashboard), deploy
- [ ] Day 7 — demo prep, video, submit

## Setup
```bash
cd ~/Desktop/zamp-vendor-onboarding
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/build_test_data.py   # regenerates data/ and test_cases/
python -m pytest -q
```

## Run Day 3 (needs GEMINI_API_KEY in .env - free key from aistudio.google.com)
```bash
python -m app.run_stage3 test_cases/01_happy_path
python -m app.run_stage3 --all --json out.json
```

## Test cases
| Case | Vendor | Expected | What it tests |
|---|---|---|---|
| 01_happy_path | Kavya Packaging Solutions Pvt Ltd | APPROVED | Clean company submission |
| 02_trade_name_vs_legal_name | Sharma Electricals | APPROVED | Bank name ≠ form name, but GST cert links proprietor to trade name; GST cert is a scan |
| 03_bank_account_reused | Nexora Tech Supplies Pvt Ltd | REJECTED | Bank account already belongs to vendor V-0001 |
| 04_gst_state_mismatch | Deccan Logistics LLP | PENDING | Karnataka GSTIN, Maharashtra billing address |
| 05a_wrong_document | Greenleaf Facility Services | PENDING | PAN card uploaded in the GST certificate slot |
| 05b_wrong_document_resubmitted | Greenleaf Facility Services | APPROVED | Same vendor, v2 with the correct document |

`test_cases/manifest.json` holds the expected outcomes; the pipeline never reads it.

See `ASSUMPTIONS.md` for scope, decision definitions and the rule ID scheme.
