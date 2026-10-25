"""
Stage 5 - Cross-referencing: does the story hold together?

Every rule compares two or more independent sources (the form, and documents read by
the model). Each returns PASS / FAIL / WARN / SKIP.

SKIP means "can't check yet" (e.g. the GST certificate was the wrong file). A skipped
rule never blocks approval on its own - the reason it was skipped already did.
FAIL rules carry an `ask`: vendor-safe wording of what to fix.
"""
from __future__ import annotations

import re

from app.completeness import COMPANY_TYPES
from app.names import CLOSE, DIFFERENT, EXACT, best_match, classify
from app.validators import (ENTITY_TYPE_TO_PAN_CODES, FAIL, GST_STATE_CODES, GSTIN_RE, PAN_HOLDER_TYPES,
                            PAN_RE, PASS, SKIP, WARN, RuleResult)

ENTITY_LABEL = {"private_limited": "Private Limited company", "public_limited": "Public Limited company",
                "llp": "LLP", "partnership": "partnership firm", "sole_proprietorship": "sole proprietorship",
                "trust": "trust"}
_STATE_ALIASES = {"orissa": "odisha", "uttaranchal": "uttarakhand", "pondicherry": "puducherry",
                  "nct of delhi": "delhi", "new delhi": "delhi"}


# ------------------------------------------------------------------ helpers
def _id(v) -> str:
    return re.sub(r"[\s\-]", "", str(v or "")).upper()


def _digits(v) -> str:
    return re.sub(r"\D", "", str(v or ""))


def _state_key(v) -> str:
    s = re.sub(r"\s+", " ", str(v or "").strip().lower())
    return _STATE_ALIASES.get(s, s)


def usable(docs: dict, slot: str) -> dict | None:
    """Fields of a document, but only if it is the right document AND readable."""
    e = (docs or {}).get(slot)
    if not e or not e.get("extraction"):
        return None
    by_id = {r.rule_id: r for r in e["rules"]}
    if by_id.get("DOC-01") is None or by_id["DOC-01"].status != PASS:
        return None
    if by_id.get("DOC-02") is not None and by_id["DOC-02"].status == FAIL:
        return None
    return e["extraction"]["fields"]


def _skip(rid, name, why):
    return RuleResult(rid, name, SKIP, why)


def _fail(rid, name, detail, ask, **ev):
    return RuleResult(rid, name, FAIL, detail, {"ask": ask, **ev})


# -------------------------------------------------------------------- rules
def x01_gstin_matches_certificate(sub, docs):
    name = "GSTIN on form matches GST certificate"
    f = usable(docs, "gst_certificate")
    if not f or not f.get("gstin"):
        return _skip("XREF-01", name, "GST certificate not available or not readable yet.")
    form, doc = _id(sub["tax"].get("gstin")), _id(f["gstin"])
    if form == doc:
        return RuleResult("XREF-01", name, PASS, f"GSTIN {form} appears on both the form and the certificate.")
    return _fail("XREF-01", name,
                 f"Form says {form}, GST certificate says {doc}.",
                 f"The GSTIN on your GST certificate ({doc}) is different from the one you entered on the "
                 f"form ({form}). Please confirm which one is correct.", form=form, document=doc)


def x02_pan_inside_gstin(sub, docs):
    name = "PAN inside GSTIN matches declared PAN"
    g, pan = _id(sub["tax"].get("gstin")), _id(sub["tax"].get("pan"))
    if not GSTIN_RE.match(g) or not PAN_RE.match(pan):
        return _skip("XREF-02", name, "GSTIN or PAN has an invalid format (already reported).")
    inside = g[2:12]
    if inside == pan:
        return RuleResult("XREF-02", name, PASS, f"Characters 3-12 of the GSTIN are the declared PAN ({pan}).")
    return _fail("XREF-02", name, f"GSTIN contains PAN {inside} but the declared PAN is {pan}.",
                 f"The PAN inside your GSTIN ({inside}) does not match the PAN you entered ({pan}). "
                 "Please check both numbers.", inside=inside, declared=pan)


def x03_pan_matches_card(sub, docs):
    name = "PAN on form matches PAN card"
    f = usable(docs, "pan_card")
    if not f or not f.get("pan"):
        return _skip("XREF-03", name, "PAN card not available or not readable yet.")
    form, doc = _id(sub["tax"].get("pan")), _id(f["pan"])
    if form == doc:
        return RuleResult("XREF-03", name, PASS, f"PAN {form} appears on both the form and the card.")
    return _fail("XREF-03", name, f"Form says {form}, PAN card says {doc}.",
                 f"The PAN on your PAN card ({doc}) is different from the PAN you entered ({form}). "
                 "Please confirm the correct PAN.", form=form, document=doc)


def x04_pan_type_vs_entity(sub, docs):
    name = "PAN holder type fits business type"
    pan, entity = _id(sub["tax"].get("pan")), sub["company"].get("entity_type")
    if not PAN_RE.match(pan) or entity not in ENTITY_TYPE_TO_PAN_CODES:
        return _skip("XREF-04", name, "PAN format or business type unavailable.")
    code = pan[3]
    if code in ENTITY_TYPE_TO_PAN_CODES[entity]:
        return RuleResult("XREF-04", name, PASS,
                          f"4th PAN character '{code}' ({PAN_HOLDER_TYPES.get(code)}) fits a {ENTITY_LABEL[entity]}.")
    return _fail("XREF-04", name,
                 f"PAN type '{code}' ({PAN_HOLDER_TYPES.get(code)}) does not fit entity type {entity}.",
                 f"The PAN you entered belongs to a {PAN_HOLDER_TYPES.get(code, 'different kind of holder').lower()}, "
                 f"but you selected '{ENTITY_LABEL[entity]}'. Please confirm your business type and PAN.",
                 pan_type=code, entity=entity)


def x05_gstin_state_vs_address(sub, docs):
    name = "GSTIN state matches business address"
    g = _id(sub["tax"].get("gstin"))
    if not GSTIN_RE.match(g) or g[:2] not in GST_STATE_CODES:
        return _skip("XREF-05", name, "GSTIN invalid (already reported).")
    gst_state = GST_STATE_CODES[g[:2]]
    addr_state = sub["company"]["address"].get("state")
    if _state_key(gst_state) == _state_key(addr_state):
        return RuleResult("XREF-05", name, PASS, f"GSTIN state code {g[:2]} = {gst_state}, same as the address.")
    return _fail("XREF-05", name,
                 f"GSTIN is registered in {gst_state} but the address is in {addr_state}. "
                 "Not necessarily a problem: businesses hold one GSTIN per state.",
                 f"Your GSTIN is registered in {gst_state}, but the business address you gave is in {addr_state}. "
                 f"Please send your {addr_state} GSTIN, or confirm that your billing address is in {gst_state}.",
                 gstin_state=gst_state, address_state=addr_state)


def x06_name_matches_gst(sub, docs):
    name = "Business name matches GST certificate"
    f = usable(docs, "gst_certificate")
    if not f or not (f.get("legal_name") or f.get("trade_name")):
        return _skip("XREF-06", name, "GST certificate not available or not readable yet.")
    form = sub["company"]["vendor_name"]
    kind, who = best_match(form, [f.get("legal_name"), f.get("trade_name")])
    if kind == EXACT:
        return RuleResult("XREF-06", name, PASS, f"'{form}' matches '{who}' on the GST certificate.")
    if kind == CLOSE:
        return RuleResult("XREF-06", name, WARN, f"'{form}' is very close to '{who}' on the GST certificate "
                          "(small spelling difference).")
    return _fail("XREF-06", name,
                 f"Form name '{form}' matches neither legal name '{f.get('legal_name')}' nor trade name "
                 f"'{f.get('trade_name')}' on the GST certificate.",
                 f"The business name on your GST certificate ('{f.get('legal_name') or f.get('trade_name')}') "
                 f"does not match the name you entered ('{form}'). Please confirm the correct legal name.",
                 form=form, legal=f.get("legal_name"), trade=f.get("trade_name"))


def x07_pan_card_name(sub, docs):
    name = "PAN card name belongs to this vendor"
    f = usable(docs, "pan_card")
    if not f or not f.get("legal_name"):
        return _skip("XREF-07", name, "PAN card not available or not readable yet.")
    gst = usable(docs, "gst_certificate") or {}
    entity = sub["company"].get("entity_type")
    if entity == "sole_proprietorship":
        cands = [gst.get("legal_name"), gst.get("proprietor_name"), sub.get("proprietor_name")]
        if not any(cands):
            return _skip("XREF-07", name, "Need the GST certificate to link the proprietor's name.")
    else:
        cands = [sub["company"]["vendor_name"], gst.get("legal_name")]
    kind, who = best_match(f["legal_name"], cands)
    if kind == EXACT:
        return RuleResult("XREF-07", name, PASS, f"PAN card name '{f['legal_name']}' matches '{who}'.")
    if kind == CLOSE:
        return RuleResult("XREF-07", name, WARN, f"PAN card name '{f['legal_name']}' is very close to '{who}'.")
    return _fail("XREF-07", name,
                 f"PAN card name '{f['legal_name']}' does not match {[c for c in cands if c]}.",
                 f"The name on the PAN card ('{f['legal_name']}') does not match your business details. "
                 "Please upload the PAN card of the business (or of the proprietor, for a sole proprietorship).",
                 pan_name=f["legal_name"])


def x08_bank_holder(sub, docs):
    name = "Bank account holder belongs to this vendor"
    cheque = usable(docs, "cancelled_cheque") or {}
    holder = cheque.get("account_holder_name") or sub["bank"].get("account_holder_name")
    vendor = sub["company"]["vendor_name"]
    gst = usable(docs, "gst_certificate") or {}
    entity = sub["company"].get("entity_type")

    kind = classify(holder, vendor)
    if kind == EXACT:
        return RuleResult("XREF-08", name, PASS, f"Account holder '{holder}' matches the vendor name.")
    if kind == CLOSE:
        return RuleResult("XREF-08", name, WARN,
                          f"Account holder '{holder}' is very close to vendor name '{vendor}'.")

    legal, trade, prop = gst.get("legal_name"), gst.get("trade_name"), gst.get("proprietor_name")
    # Sole proprietor: the account is legitimately in the owner's own name, but ONLY if the
    # GST certificate ties the trade name on the form to that person, and the PAN is an individual's.
    if entity == "sole_proprietorship" and _id(sub["tax"].get("pan"))[3:4] == "P":
        trade_ok = classify(vendor, trade) in (EXACT, CLOSE) or classify(vendor, legal) in (EXACT, CLOSE)
        person_ok = best_match(holder, [legal, prop, sub.get("proprietor_name")])[0] in (EXACT, CLOSE)
        if trade_ok and person_ok:
            return RuleResult(
                "XREF-08", name, PASS,
                f"Account holder '{holder}' differs from the trade name '{vendor}', but the GST certificate "
                f"links them (legal name '{legal}', trade name '{trade}') and the PAN is an individual's. "
                "Valid for a sole proprietorship.",
                {"linked_via": "gst_certificate", "legal_name": legal, "trade_name": trade})
    # Company: holder may match the GST legal name even if the form name has a typo.
    if entity != "sole_proprietorship" and classify(holder, legal) == EXACT:
        return RuleResult("XREF-08", name, PASS, f"Account holder '{holder}' matches the GST legal name.")

    return _fail("XREF-08", name,
                 f"Account holder '{holder}' does not match vendor '{vendor}'"
                 + (f" or the GST legal name '{legal}'." if legal else "."),
                 f"The bank account holder ('{holder}') does not match your business name ('{vendor}'). "
                 "Please provide a cancelled cheque or bank letter in the business's name, "
                 "or explain the relationship.", holder=holder, vendor=vendor)


def x09_bank_details_vs_cheque(sub, docs):
    name = "Bank details on form match cancelled cheque"
    f = usable(docs, "cancelled_cheque")
    if not f:
        return _skip("XREF-09", name, "Cancelled cheque not available or not readable yet.")
    bank, bad = sub["bank"], []
    if _digits(bank.get("account_number")) != _digits(f.get("account_number")):
        bad.append("account number")
    if _id(bank.get("ifsc")) != _id(f.get("ifsc")):
        bad.append("IFSC code")
    if classify(bank.get("account_holder_name"), f.get("account_holder_name")) == DIFFERENT:
        bad.append("account holder name")
    if not bad:
        return RuleResult("XREF-09", name, PASS, "Account number, IFSC and holder name agree with the cheque.")
    return _fail("XREF-09", name, "Mismatch with the cheque: " + ", ".join(bad) + ".",
                 "The bank details on your form do not match your cancelled cheque (" + ", ".join(bad) +
                 "). Please correct the form or upload the right cheque.", fields=bad)


def x10_incorporation(sub, docs):
    name = "Incorporation certificate agrees with form"
    if sub["company"].get("entity_type") not in COMPANY_TYPES:
        return _skip("XREF-10", name, "Not required for this business type.")
    f = usable(docs, "certificate_of_incorporation")
    if not f:
        return _skip("XREF-10", name, "Incorporation certificate not available or not readable yet.")
    bad = []
    if classify(sub["company"]["vendor_name"], f.get("legal_name")) == DIFFERENT:
        bad.append("company name")
    if f.get("cin_or_llpin") and _id(sub.get("cin")) != _id(f["cin_or_llpin"]):
        bad.append("CIN / LLPIN")
    if f.get("pan") and _id(sub["tax"].get("pan")) != _id(f["pan"]):
        bad.append("PAN")
    if not bad:
        return RuleResult("XREF-10", name, PASS, "Company name, CIN/LLPIN and PAN agree with the form.")
    return _fail("XREF-10", name, "Certificate disagrees with the form on: " + ", ".join(bad) + ".",
                 "Your certificate of incorporation does not match the details on your form ("
                 + ", ".join(bad) + "). Please check and correct.", fields=bad)


def x11_cheque_cancelled(sub, docs):
    name = "Cheque is marked cancelled"
    f = usable(docs, "cancelled_cheque")
    if not f:
        return _skip("XREF-11", name, "Cancelled cheque not available or not readable yet.")
    mc = f.get("marked_cancelled")
    if mc is True:
        return RuleResult("XREF-11", name, PASS, "The cheque is crossed out / stamped CANCELLED.")
    if mc is False:
        return _fail("XREF-11", name, "The cheque does not appear to be cancelled.",
                     "Your cheque does not look cancelled. Please upload a cheque that is crossed out or "
                     "stamped 'CANCELLED', so it can't be used for payments.")
    return RuleResult("XREF-11", name, WARN, "Could not tell whether the cheque is marked cancelled.")


ALL_RULES = [x01_gstin_matches_certificate, x02_pan_inside_gstin, x03_pan_matches_card,
             x04_pan_type_vs_entity, x05_gstin_state_vs_address, x06_name_matches_gst,
             x07_pan_card_name, x08_bank_holder, x09_bank_details_vs_cheque,
             x10_incorporation, x11_cheque_cancelled]


def run_crossref(sub: dict, docs: dict, log=None) -> list[RuleResult]:
    if log:
        log.emit("crossref", "started", "Cross-checking the form against the documents")
    results = [rule(sub, docs) for rule in ALL_RULES]
    if log:
        bad = [r for r in results if r.status == FAIL]
        log.emit("crossref", "warn" if bad else "done",
                 f"{len(bad)} inconsistencies found" if bad else "Form and documents are consistent",
                 rules=[r.to_dict() for r in results])
    return results
