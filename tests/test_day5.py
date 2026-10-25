import json
import shutil

from app.changes import compare_versions, diff_form
from app.events import RunLog
from app.pipeline import run_submission
from app.store import Store
from tests.support import CASES, OracleReader, oracle_for


def run(case, store, **kw):
    reader = kw.pop("reader", None) or oracle_for(case)
    res = run_submission(CASES / case, reader, store=store, **kw)
    return res, reader


# ------------------------------------------------------------------- store
def test_store_saves_lists_and_loads_runs():
    st = Store(":memory:")
    res, _ = run("01_happy_path", st)
    assert res["run_id"] == 1
    row = st.list_runs()[0]
    assert row["submission_id"] == "SUB-1001" and row["status"] == "APPROVED"
    assert st.get_run(1)["headline"] == res["headline"]
    assert st.get_run(999) is None


def test_previous_run_is_latest_run_of_an_earlier_version_only():
    st = Store(":memory:")
    a1, _ = run("05a_wrong_document", st)
    run("05a_wrong_document", st)                                  # same version twice: still version 1
    assert st.previous_run("SUB-1005", before_version=1) is None   # nothing earlier than v1
    assert st.previous_run("SUB-1005", before_version=2)["version"] == 1
    assert st.previous_run("SUB-9999", before_version=2) is None
    assert [v["version"] for v in st.versions("SUB-1005")] == [1]


def test_reset_clears_everything():
    st = Store(":memory:")
    run("01_happy_path", st)
    st.reset()
    assert st.list_runs() == [] and st.approved_vendors() == []


# ------------------------------------------------------- the resubmission loop
def test_resubmission_resolves_the_problem_and_reuses_unchanged_files():
    st = Store(":memory:")
    v1, _ = run("05a_wrong_document", st)
    assert v1["status"] == "PENDING" and "changes" not in v1

    log = RunLog()
    v2, reader = run("05b_wrong_document_resubmitted", st, log=log)
    assert v2["status"] == "APPROVED" and v2["version"] == 2

    c = v2["changes"]
    assert (c["status_before"], c["status_after"]) == ("PENDING", "APPROVED")
    assert c["documents_changed"] == ["gst_certificate"]
    assert c["documents_unchanged"] == ["cancelled_cheque", "certificate_of_incorporation", "pan_card"]
    assert reader.reads == 1                       # ONLY the new GST certificate went to the model
    assert [d["reused"] for s, d in v2["documents"].items()] == [False, True, True, True]

    assert {r["rule_id"] for r in c["resolved"]} == {"DOC-01"}
    unlocked = {r["rule_id"] for r in c["newly_checkable"]}
    assert {"DOC-02", "XREF-01", "XREF-06"} <= unlocked
    assert c["regressed"] == [] and c["still_failing"] == []
    assert any("no model call" in e.message for e in log.events)
    assert any(e.stage == "resubmission" and e.status == "done" for e in log.events)


def test_resubmission_email_thanks_the_vendor_for_corrections():
    st = Store(":memory:")
    run("05a_wrong_document", st)
    v2, _ = run("05b_wrong_document_resubmitted", st)
    assert "corrected information" in v2["messages"]["vendor"]["body"]
    assert "SUB-1005, v2" in v2["messages"]["vendor"]["subject"]


def test_without_a_store_nothing_is_compared_or_saved():
    res = run_submission(CASES / "05b_wrong_document_resubmitted", oracle_for("05b_wrong_document_resubmitted"))
    assert "changes" not in res and "run_id" not in res and res["status"] == "APPROVED"


def test_unreadable_earlier_document_is_re_read_not_reused():
    st = Store(":memory:")
    bad = OracleReader("05a_wrong_document", wrong={"gst_certificate": "pan_card"},
                       tweak={"pan_card": lambda e: e.update(legibility="poor")})
    run("05a_wrong_document", st, reader=bad)                     # v1: pan_card was unreadable (DOC-02 FAIL)
    v2, reader = run("05b_wrong_document_resubmitted", st)
    assert reader.reads == 2                                      # gst (new) + pan_card (re-read, not reused)
    assert [d["reused"] for d in v2["documents"].values()] == [False, False, True, True]


# --------------------------------------------------------- change comparison
def test_diff_form_reports_dotted_fields():
    old = {"bank": {"ifsc": "A", "account_number": "1"}, "cin": "X"}
    new = {"bank": {"ifsc": "B", "account_number": "1"}, "cin": "X", "extra": 1}
    assert diff_form(old, new) == [{"field": "bank.ifsc", "old": "A", "new": "B"},
                                   {"field": "extra", "old": None, "new": 1}]


def rr(rid, status, name="n"):
    return {"rule_id": rid, "name": name, "status": status, "detail": "d"}


def test_compare_versions_categories():
    prev = {"version": 1, "status": "PENDING", "form": {}, "doc_hashes": {"a": "1", "b": "2"},
            "rules": [rr("R1", "FAIL"), rr("R2", "SKIP"), rr("R3", "PASS"), rr("R4", "FAIL"), rr("R5", "WARN")]}
    new = {"version": 2, "status": "PENDING", "form": {}, "doc_hashes": {"a": "1", "b": "9"},
           "rules": [rr("R1", "PASS"), rr("R2", "FAIL"), rr("R3", "FAIL"), rr("R4", "FAIL"), rr("R5", "PASS")]}
    c = compare_versions(prev, new)
    assert [r["rule_id"] for r in c["resolved"]] == ["R1"]
    assert [r["rule_id"] for r in c["newly_checkable"]] == ["R2"]
    assert [r["rule_id"] for r in c["regressed"]] == ["R3"]
    assert [r["rule_id"] for r in c["still_failing"]] == ["R4"]
    assert c["documents_changed"] == ["b"] and c["documents_unchanged"] == ["a"]


# ------------------------------------------------- the vendor records grow
def test_approved_vendor_blocks_a_later_fraud_attempt(tmp_path):
    st = Store(":memory:")
    ok, _ = run("01_happy_path", st)                       # Kavya approved -> joins vendor records
    assert ok["status"] == "APPROVED" and st.approved_vendors()[0]["vendor_id"] == "NEW-SUB-1001"

    clone = tmp_path / "clone"
    shutil.copytree(CASES / "02_trade_name_vs_legal_name", clone)
    sub = json.loads((clone / "submission.json").read_text())
    sub["submission_id"] = "SUB-2001"
    sub["bank"].update(account_number="50200078453219", ifsc="HDFC0004521")   # Kavya's account
    (clone / "submission.json").write_text(json.dumps(sub))

    res = run_submission(clone, OracleReader("x", case_dir=clone), store=st)
    assert res["status"] == "REJECTED"
    risk = next(r for r in res["rules"] if r["rule_id"] == "RISK-01")
    assert risk["status"] == "FAIL" and "NEW-SUB-1001" in risk["detail"]


def test_a_submission_is_never_flagged_as_a_duplicate_of_itself():
    st = Store(":memory:")
    run("01_happy_path", st)
    again, _ = run("01_happy_path", st)                    # re-run after it was already approved
    assert again["status"] == "APPROVED"
    assert len(st.approved_vendors()) == 1


def test_pending_and_rejected_runs_do_not_become_vendors():
    st = Store(":memory:")
    run("03_bank_account_reused", st)
    run("04_gst_state_mismatch", st)
    assert st.approved_vendors() == []
