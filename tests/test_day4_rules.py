import json

import pytest

from app.crossref import run_crossref, x04_pan_type_vs_entity, x08_bank_holder, usable
from app.decision import APPROVED, PENDING, REJECTED, decide, vendor_ask
from app.documents import read_documents
from app.events import RunLog
from app.intake import load_submission
from app.llm import ExtractionError
from app.messages import internal_note, vendor_message
from app.risk import load_vendor_master, run_risk
from app.validators import FAIL, PASS, SKIP, WARN, RuleResult
from tests.support import CASES, OracleReader, oracle_for


def setup(case="01_happy_path", tweak=None, sub_edit=None):
    sub = load_submission(CASES / case)
    if sub_edit:
        sub_edit(sub)
    docs = read_documents(sub, OracleReader(case, tweak=tweak), RunLog())
    return sub, docs


def st(results):
    return {r.rule_id: r.status for r in results}


MASTER = load_vendor_master()


# ----------------------------------------------------------------- risk rules
def test_same_account_number_different_bank_is_only_a_warning():
    sub, docs = setup(sub_edit=lambda s: s["bank"].update(account_number="045601503321", ifsc="HDFC0001234"),
                      tweak={"cancelled_cheque": lambda e: e["fields"].update(account_number="045601503321", ifsc="HDFC0001234")})
    r = {x.rule_id: x for x in run_risk(sub, docs, MASTER, [])}
    assert r["RISK-01"].status == WARN


def test_duplicate_gstin_is_rejected_and_pan_rule_defers_to_it():
    v = MASTER[0]
    sub, docs = setup(sub_edit=lambda s: s["tax"].update(gstin=v["gstin"], pan=v["pan"]))
    r = {x.rule_id: x for x in run_risk(sub, docs, MASTER, [])}
    assert r["RISK-02"].status == FAIL and r["RISK-03"].status == SKIP


def test_same_pan_same_name_is_additional_registration_warning():
    v = MASTER[1]  # Bluepeak Cleaning Services LLP
    sub, docs = setup(sub_edit=lambda s: (s["tax"].update(pan=v["pan"]), s["company"].update(vendor_name="Bluepeak Cleaning Services LLP")))
    r = {x.rule_id: x for x in run_risk(sub, docs, MASTER, [])}
    assert r["RISK-02"].status == PASS and r["RISK-03"].status == WARN


def test_same_pan_different_name_is_identity_conflict():
    v = MASTER[1]
    sub, docs = setup(sub_edit=lambda s: s["tax"].update(pan=v["pan"]))
    assert {x.rule_id: x for x in run_risk(sub, docs, MASTER, [])}["RISK-03"].status == FAIL


def test_three_separate_crossref_failures_trigger_the_pattern_rule():
    fake = [RuleResult(f"XREF-0{i}", "x", FAIL, "d") for i in (1, 3, 6)]
    sub, docs = setup()
    r = {x.rule_id: x for x in run_risk(sub, docs, MASTER, fake)}
    assert r["RISK-04"].status == FAIL
    two = {x.rule_id: x for x in run_risk(sub, docs, MASTER, fake[:2])}
    assert two["RISK-04"].status == PASS


def test_free_email_warns_for_company_but_not_for_sole_proprietor():
    sub, docs = setup(sub_edit=lambda s: s["company"]["contact"].update(email="x@gmail.com"))
    assert {x.rule_id: x for x in run_risk(sub, docs, MASTER, [])}["RISK-05"].status == WARN
    sub2, docs2 = setup("02_trade_name_vs_legal_name")        # sole proprietor already uses gmail
    assert {x.rule_id: x for x in run_risk(sub2, docs2, MASTER, [])}["RISK-05"].status == PASS


# ------------------------------------------------------------ cross-ref rules
def test_company_cannot_use_individual_account_even_if_name_is_on_gst_certificate():
    sub, docs = setup(tweak={"cancelled_cheque": lambda e: e["fields"].update(account_holder_name="SOMEONE ELSE")})
    assert x08_bank_holder(sub, docs).status == FAIL


def test_sole_proprietor_link_requires_individual_pan():
    # Same trade-name situation as case 02, but PAN type is 'C' (company) -> link must NOT be accepted
    sub, docs = setup("02_trade_name_vs_legal_name", sub_edit=lambda s: s["tax"].update(pan="BQRCS7712D"))
    assert x08_bank_holder(sub, docs).status == FAIL


def test_pan_type_vs_entity():
    sub, docs = setup()
    assert x04_pan_type_vs_entity(sub, docs).status == PASS
    sub["tax"]["pan"] = "AAKPK4821M"   # P = individual, but entity is a company
    assert x04_pan_type_vs_entity(sub, docs).status == FAIL


def test_pan_mismatch_form_vs_card_and_gstin_vs_certificate():
    sub, docs = setup(tweak={"pan_card": lambda e: e["fields"].update(pan="ZZZZZ9999Z"),
                             "gst_certificate": lambda e: e["fields"].update(gstin="27AAKCK4821M1Z0")})
    r = {x.rule_id: x for x in run_crossref(sub, docs)}
    assert r["XREF-03"].status == FAIL and r["XREF-01"].status == FAIL


def test_cheque_not_marked_cancelled_fails_and_unknown_warns():
    sub, docs = setup(tweak={"cancelled_cheque": lambda e: e["fields"].update(marked_cancelled=False)})
    assert {x.rule_id: x for x in run_crossref(sub, docs)}["XREF-11"].status == FAIL
    sub, docs = setup(tweak={"cancelled_cheque": lambda e: e["fields"].update(marked_cancelled=None)})
    assert {x.rule_id: x for x in run_crossref(sub, docs)}["XREF-11"].status == WARN


def test_unreadable_document_is_not_used_for_crossrefs():
    sub, docs = setup(tweak={"gst_certificate": lambda e: e.update(legibility="poor")})
    assert usable(docs, "gst_certificate") is None
    assert {x.rule_id: x for x in run_crossref(sub, docs)}["XREF-01"].status == SKIP


# ------------------------------------------------------------------- decision
def R(rid, status, **ev):
    return RuleResult(rid, "n", status, "detail", ev)


def test_decision_policy():
    assert decide([R("FMT-PAN-01", PASS)]).status == APPROVED
    assert decide([R("FMT-PAN-01", PASS), R("RISK-05", WARN)]).status == APPROVED
    assert decide([R("XREF-05", FAIL, ask="fix it"), R("XREF-01", SKIP)]).status == PENDING
    assert decide([R("XREF-05", FAIL, ask="x"), R("RISK-01", FAIL)]).status == REJECTED   # reject outranks pending
    assert decide([R("XREF-01", SKIP)]).status == APPROVED                                 # skip alone never blocks


def test_asks_are_deduplicated_and_vendor_safe():
    d = decide([R("XREF-05", FAIL, ask="same"), R("XREF-06", FAIL, ask="same"), R("XREF-03", FAIL, ask="other")])
    assert d.asks == ["same", "other"]


def test_vendor_ask_wording_for_non_xref_rules():
    assert "business type" in vendor_ask(R("CMP-01", FAIL))
    assert "GST registration certificate" in vendor_ask(R("CMP-03", FAIL, missing=["GST registration certificate"]))
    assert "clearer copy of your PAN card" in vendor_ask(R("DOC-02", FAIL, slot="pan_card"))


# ------------------------------------------------------------------- messages
def test_llm_framing_is_used_but_asks_stay_verbatim():
    reader = oracle_for("04_gst_state_mismatch")
    res = vendor_message({"status": PENDING, "asks": ["Ask one.", "Ask two."]},
                         load_submission(CASES / "04_gst_state_mismatch"), reader)
    assert res["source"] == "llm" and "1. Ask one." in res["body"] and "2. Ask two." in res["body"]
    assert "thank you for sending" in res["body"]


@pytest.mark.parametrize("reply", [
    "Only one line", "a\nb\nc", "Hi\nWe flagged possible fraud on your account.",
    "Hi\nOur risk team found a duplicate.", "x" * 500 + "\nfine"])
def test_unsafe_or_malformed_llm_text_falls_back_to_template(reply):
    class R_:
        def draft(self, s, p): return reply
    res = vendor_message({"status": PENDING, "asks": ["Ask one."]}, load_submission(CASES / "01_happy_path"), R_())
    assert res["source"] == "template" and "1. Ask one." in res["body"]


def test_model_outage_falls_back_to_template():
    class Down:
        def draft(self, s, p): raise ExtractionError("503")
    res = vendor_message({"status": PENDING, "asks": ["Ask one."]}, load_submission(CASES / "01_happy_path"), Down())
    assert res["source"] == "template"


def test_rejected_message_never_calls_the_model():
    class Boom:
        def draft(self, s, p): raise AssertionError("must not be called")
    res = vendor_message({"status": REJECTED, "asks": []}, load_submission(CASES / "03_bank_account_reused"), Boom())
    assert res["source"] == "template"


def test_internal_note_contains_rule_ids_and_next_step():
    d = decide([R("RISK-01", FAIL)]).to_dict()
    note = internal_note(d, load_submission(CASES / "03_bank_account_reused"))
    assert "[RISK-01]" in note and "REJECTED" in note and "Do NOT onboard" in note
