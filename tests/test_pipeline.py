import json

from app.events import RunLog
from app.pipeline import run_submission
from tests.support import CASES, OracleReader, oracle_for

MANIFEST = {m["case"]: m for m in json.loads((CASES / "manifest.json").read_text())}


def run(case, **kw):
    return run_submission(CASES / case, kw.pop("reader", None) or oracle_for(case), **kw)


def by_id(res, rid):
    return next(r for r in res["rules"] if r["rule_id"] == rid)


def failing(res):
    return sorted(r["rule_id"] for r in res["rules"] if r["status"] == "FAIL")


# ---- every test case reaches the outcome its design says it should
def test_all_cases_reach_expected_outcome():
    for case, m in MANIFEST.items():
        assert run(case)["status"] == m["expected_outcome"], case


def test_happy_path_is_clean():
    res = run("01_happy_path")
    assert failing(res) == [] and res["decision"]["warnings"] == []
    assert res["decision"]["counts"]["skipped"] == 0
    assert res["messages"]["vendor"]["subject"].startswith("Approved")


# ---- edge case 1: the trade-name / owner-name "mismatch" is legitimate
def test_trade_name_case_is_approved_and_explains_why():
    res = run("02_trade_name_vs_legal_name")
    r = by_id(res, "XREF-08")
    assert r["status"] == "PASS" and r["evidence"]["linked_via"] == "gst_certificate"
    assert "sole proprietorship" in r["detail"]


def test_same_owner_name_is_NOT_accepted_for_a_company():
    # Company, but the cheque is in an individual's name -> must fail even though a person's name is on the GST cert
    case = "01_happy_path"
    reader = OracleReader(case, tweak={"cancelled_cheque": lambda ex: ex["fields"].update(account_holder_name="ANANYA RAO")})
    res = run(case, reader=reader)
    assert by_id(res, "XREF-08")["status"] == "FAIL"


# ---- edge case 2: reused bank account -> rejected, vendor learns nothing
def test_reused_bank_account_is_rejected_with_neutral_vendor_message():
    res = run("03_bank_account_reused")
    assert res["status"] == "REJECTED" and failing(res) == ["RISK-01"]
    assert "V-0001" in by_id(res, "RISK-01")["detail"]
    v = res["messages"]["vendor"]
    assert v["source"] == "template"
    text = (v["subject"] + v["body"]).lower()
    for leaked in ("orbit", "v-0001", "bank", "fraud", "account", "risk", "duplicate"):
        assert leaked not in text
    assert "V-0001" in res["messages"]["internal"] and "Do NOT onboard" in res["messages"]["internal"]


# ---- edge case 3: state mismatch is fixable, not fraud
def test_state_mismatch_is_pending_with_a_specific_ask():
    res = run("04_gst_state_mismatch")
    assert res["status"] == "PENDING" and failing(res) == ["XREF-05"]
    ask = res["decision"]["asks"][0]
    assert "Maharashtra" in ask and "Karnataka" in ask
    body = res["messages"]["vendor"]["body"]
    assert ask in body and "1." in body


# ---- edge case 4: wrong document, then resubmission
def test_wrong_document_is_pending_and_dependent_checks_are_skipped_not_failed():
    res = run("05a_wrong_document")
    assert res["status"] == "PENDING" and failing(res) == ["DOC-01"]
    assert by_id(res, "XREF-01")["status"] == "SKIP" and by_id(res, "XREF-06")["status"] == "SKIP"
    assert len(res["decision"]["asks"]) == 1
    assert "GST registration certificate" in res["decision"]["asks"][0]
    assert "PAN card" in res["decision"]["asks"][0]


def test_resubmission_with_correct_document_is_approved():
    assert run("05b_wrong_document_resubmitted")["status"] == "APPROVED"


# ---- run log + serialisation
def test_events_cover_every_stage_and_result_is_json_serialisable():
    log = RunLog()
    res = run("01_happy_path", log=log)
    stages = [e.stage for e in log.events]
    for s in ("intake", "completeness", "format", "documents", "crossref", "risk", "decision", "messages"):
        assert s in stages
    json.dumps(res)


def test_pipeline_does_not_read_expected_outcomes():
    import app.pipeline, inspect
    assert "manifest" not in inspect.getsource(app.pipeline)
