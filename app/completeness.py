"""
Stage 2 - Completeness: is everything we need actually there?

Pure code. Requirements depend on entity type (see ASSUMPTIONS.md).
A completeness failure means PENDING (the vendor can fix it), never REJECTED.
"""
from __future__ import annotations

from pathlib import Path

from app.events import RunLog
from app.validators import FAIL, PASS, RuleResult

ALL_DOCS = ["gst_certificate", "pan_card", "cancelled_cheque", "certificate_of_incorporation"]
DOC_LABELS = {
    "gst_certificate": "GST registration certificate",
    "pan_card": "PAN card",
    "cancelled_cheque": "Cancelled cheque",
    "certificate_of_incorporation": "Certificate of incorporation",
}

COMPANY_TYPES = {"private_limited", "public_limited", "llp"}
NON_COMPANY_TYPES = {"sole_proprietorship", "partnership", "trust"}

REQUIRED_DOCS = {t: list(ALL_DOCS) for t in COMPANY_TYPES}
REQUIRED_DOCS.update({t: ["gst_certificate", "pan_card", "cancelled_cheque"]
                      for t in NON_COMPANY_TYPES})

BASE_FIELDS = [
    ("company.vendor_name", "Vendor / business name"),
    ("company.address.line1", "Address line 1"),
    ("company.address.city", "City"),
    ("company.address.state", "State"),
    ("company.address.pincode", "Pincode"),
    ("company.contact.name", "Contact person name"),
    ("company.contact.email", "Contact email"),
    ("company.contact.phone", "Contact phone"),
    ("tax.pan", "PAN"),
    ("tax.gstin", "GSTIN"),
    ("bank.account_holder_name", "Bank account holder name"),
    ("bank.account_number", "Bank account number"),
    ("bank.ifsc", "IFSC code"),
]
COMPANY_FIELDS = [("cin", "CIN / LLPIN")]


def _get(d: dict, dotted: str):
    cur = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _blank(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def check_completeness(sub: dict, log: RunLog | None = None) -> list[RuleResult]:
    if log:
        log.emit("completeness", "started", "Checking required fields and documents")
    results: list[RuleResult] = []

    entity = _get(sub, "company.entity_type")
    if entity not in REQUIRED_DOCS:
        results.append(RuleResult(
            "CMP-01", "Entity type", FAIL,
            f"Entity type '{entity}' is missing or not recognised. Expected one of: "
            + ", ".join(sorted(REQUIRED_DOCS)) + ".", {"entity_type": entity}))
        if log:
            log.emit("completeness", "done", "Entity type not recognised", rules=[r.to_dict() for r in results])
        return results
    results.append(RuleResult("CMP-01", "Entity type", PASS,
                              f"Entity type is '{entity}'.", {"entity_type": entity}))

    # --- fields
    needed = BASE_FIELDS + (COMPANY_FIELDS if entity in COMPANY_TYPES else [])
    missing_fields = [label for path, label in needed if _blank(_get(sub, path))]
    if missing_fields:
        results.append(RuleResult(
            "CMP-02", "Required form fields", FAIL,
            "Missing form fields: " + ", ".join(missing_fields) + ".",
            {"missing": missing_fields}))
    else:
        results.append(RuleResult("CMP-02", "Required form fields", PASS,
                                  f"All {len(needed)} required form fields are filled in."))

    # --- documents
    required = REQUIRED_DOCS[entity]
    paths = sub.get("_doc_paths", {})
    missing_docs = [DOC_LABELS[s] for s in required
                    if s not in paths or not Path(paths[s]).is_file()]
    if missing_docs:
        results.append(RuleResult(
            "CMP-03", "Required documents", FAIL,
            "Missing documents: " + ", ".join(missing_docs) + ".",
            {"missing": missing_docs, "required": required}))
    else:
        results.append(RuleResult(
            "CMP-03", "Required documents", PASS,
            f"All {len(required)} required documents were uploaded for a {entity.replace('_', ' ')}.",
            {"required": required}))

    if log:
        failed = [r.rule_id for r in results if r.status == FAIL]
        log.emit("completeness", "warn" if failed else "done",
                 "Incomplete: " + ", ".join(failed) if failed else "Submission is complete",
                 rules=[r.to_dict() for r in results])
    return results
