"""
The whole process, end to end, for one submission.

  intake -> completeness -> format -> documents -> cross-reference -> risk -> decision -> messages

Every stage emits events (the live run view will stream these) and every rule result
is kept (the dashboard will show them).
"""
from __future__ import annotations

from pathlib import Path

from app.completeness import check_completeness
from app.crossref import run_crossref
from app.decision import decide
from app.documents import read_documents
from app.events import RunLog
from app.intake import load_submission
from app.messages import internal_note, vendor_message
from app.risk import load_vendor_master, run_risk
from app.validators import FAIL, run_format_checks


def stage_of(rule_id: str) -> str:
    return {"CMP": "completeness", "FMT": "format", "DOC": "documents",
            "XREF": "crossref", "RISK": "risk"}[rule_id.split("-")[0]]


def run_submission(case_dir: str | Path, reader, master: list[dict] | None = None,
                   log: RunLog | None = None, use_llm_email: bool = True) -> dict:
    log = log or RunLog()
    master = master if master is not None else load_vendor_master()

    sub = load_submission(case_dir, log)
    completeness = check_completeness(sub, log)

    log.emit("format", "started", "Validating PAN, GSTIN (with checksum), IFSC and account number")
    fmt = run_format_checks(sub)
    bad = [r for r in fmt if r.status == FAIL]
    log.emit("format", "warn" if bad else "done",
             f"{len(bad)} format problems" if bad else "All identifiers are well-formed",
             rules=[r.to_dict() for r in fmt])

    docs = read_documents(sub, reader, log)
    doc_rules = [r for e in docs.values() for r in e["rules"]]

    xref = run_crossref(sub, docs, log)
    risk = run_risk(sub, docs, master, xref, log)

    rules = completeness + fmt + doc_rules + xref + risk
    log.emit("decision", "started", f"Applying decision rules to {len(rules)} check results")
    decision = decide(rules)
    log.emit("decision", "done", decision.headline, outcome=decision.status)
    d = decision.to_dict()

    log.emit("messages", "started", "Drafting messages")
    vendor = vendor_message(d, sub, reader if use_llm_email else None)
    internal = internal_note(d, sub)
    log.emit("messages", "done", f"Vendor email drafted ({vendor['source']}) and internal note written")

    return {
        "submission_id": sub.get("submission_id"), "version": sub.get("version", 1),
        "vendor_name": sub["company"]["vendor_name"], "entity_type": sub["company"].get("entity_type"),
        "status": decision.status, "headline": decision.headline, "decision": d,
        "rules": [{**r.to_dict(), "stage": stage_of(r.rule_id)} for r in rules],
        "documents": {s: {"route": e["route"], "extraction": e["extraction"],
                          "rules": [r.to_dict() for r in e["rules"]]} for s, e in docs.items()},
        "messages": {"vendor": vendor, "internal": internal},
        "events": [e.to_dict() for e in log.events],
    }
