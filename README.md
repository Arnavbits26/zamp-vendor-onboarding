# Vendor Onboarding — Zamp ASA Case Study (PS-2)

Takes a vendor submission (form data + documents) and returns **APPROVED / PENDING / REJECTED** with every check visible and cited, plus a drafted message back to the vendor.

## Status
- [x] Day 2 — test data, specimen documents, format validators (+ tests)
- [x] Day 3 — intake, completeness checks, document classification & extraction (Claude)
- [x] Day 4 — cross-reference + risk checks, decision engine, message drafting
- [x] Day 5 — edge cases end-to-end, resubmission loop, run history
- [x] Day 6 — UI (live run view + dashboard); deploy: see below
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

## Run the whole process (Day 4)
```bash
python -m app.run_pipeline test_cases/04_gst_state_mismatch
python -m app.run_pipeline --all          # runs all six and prints a scoreboard
```

## Resubmission loop + history (Day 5)
```bash
python -m app.run_pipeline --demo-resubmission   # 05a (wrong document) then 05b (corrected)
python -m app.history                            # every run
python -m app.history SUB-1005                   # all versions of one submission, with what changed
```

## Web UI (Day 6)
```bash
uvicorn app.server:app --reload        # then open http://127.0.0.1:8000
```
* **Run** page: pick a test case or build a custom submission (start from a case, edit any field, replace any file) and watch each stage run live. *Playback* sets the pace of the animation; the work itself happens at full speed.
* **Dashboard**: every run, filter and search, drill into any run's checks, documents, messages and what changed, and resubmit with changes.

Optional browser-level test (needs Node): `cd tests/e2e && npm install`, then `python tests/e2e/serve.py 8765 &` and `node tests/e2e/run.mjs http://127.0.0.1:8765`.

## Deploy (Render, free)
1. Push to GitHub (never commit `.env`).
2. Render > New + > Blueprint > choose the repo (it reads `render.yaml`).
3. Enter `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_FALLBACK_MODEL` when asked.
4. Free instances sleep after ~15 minutes idle and take about a minute to wake: open the link before a demo. Run history lives on the instance's disk and resets on redeploy or restart, so run the 05a then 05b cases once to repopulate the dashboard.

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
