"""
The whole process, end to end, for one submission.

  intake -> completeness -> format -> documents -> cross-reference -> risk -> decision -> messages

Every stage emits events (the live run view will stream these) and every rule result
is kept (the dashboard will show them).
"""
from __future__ import annotations

from pathlib import Path

from app.changes import compare_versions, form_snapshot
from app.completeness import check_completeness
from app.crossref import run_crossref
from app.decision import decide
from app.documents import read_documents
from app.events import RunLog
from app.intake import load_submission
from app.messages import internal_note, vendor_message
from app.risk import load_vendor_master, run_risk
from app.store import Store
from app.validators import FAIL, run_format_checks


def stage_of(rule_id: str) -> str:
    return {"CMP": "completeness", "FMT": "format", "DOC": "documents",
            "XREF": "crossref", "RISK": "risk"}[rule_id.split("-")[0]]


def run_submission(case_dir: str | Path, reader, master: list[dict] | None = None,
                   log: RunLog | None = None, use_llm_email: bool = True,
                   store: Store | None = None) -> dict:
    log = log or RunLog()
    master = list(master if master is not None else load_vendor_master())

    sub = load_submission(case_dir, log)
    version = sub.get("version", 1)

    # Earlier version of this same submission? Then this is a resubmission.
    prev = store.previous_run(sub["submission_id"], version) if store else None
    if prev:
        log.emit("resubmission", "info",
                 f"This is version {version} of {sub['submission_id']}; comparing with version "
                 f"{prev.get('version', 1)} ({prev['status']})")
    # Vendors this process has already approved count as existing vendors too.
    if store:
        master += store.approved_vendors(exclude_submission=sub["submission_id"])

    completeness = check_completeness(sub, log)

    log.emit("format", "started", "Validating PAN, GSTIN (with checksum), IFSC and account number")
    fmt = run_format_checks(sub)
    bad = [r for r in fmt if r.status == FAIL]
    log.emit("format", "warn" if bad else "done",
             f"{len(bad)} format problems" if bad else "All identifiers are well-formed",
             rules=[r.to_dict() for r in fmt])

    reuse = None
    if prev:
        reuse = {slot: {"sha256": prev.get("doc_hashes", {}).get(slot), "route": d["route"],
                        "extraction": d["extraction"]}
                 for slot, d in prev["documents"].items()
                 if not any(r["rule_id"] == "DOC-02" and r["status"] == "FAIL" for r in d["rules"])}
    docs = read_documents(sub, reader, log, reuse=reuse)
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

    result = {
        "submission_id": sub.get("submission_id"), "version": version,
        "vendor_name": sub["company"]["vendor_name"], "entity_type": sub["company"].get("entity_type"),
        "status": decision.status, "headline": decision.headline, "decision": d,
        "rules": [{**r.to_dict(), "stage": stage_of(r.rule_id)} for r in rules],
        "documents": {s: {"route": e["route"], "extraction": e["extraction"], "reused": e.get("reused", False),
                          "rules": [r.to_dict() for r in e["rules"]]} for s, e in docs.items()},
        "doc_hashes": {s: e["sha256"] for s, e in docs.items()},
        "form": form_snapshot(sub),
        "messages": {"vendor": vendor, "internal": internal},
    }
    if prev:
        result["changes"] = compare_versions(prev, result)
        c = result["changes"]
        log.emit("resubmission", "done",
                 f"{prev['status']} -> {result['status']}: {len(c['resolved'])} problem(s) resolved, "
                 f"{len(c['newly_checkable'])} check(s) now possible, {len(c['regressed'])} new problem(s). "
                 f"Files changed: {', '.join(c['documents_changed']) or 'none'}")
    if store:
        if decision.status == "APPROVED":
            log.emit("commit", "done", f"Added to vendor records as {store.add_vendor(sub)}")
        result["events"] = [e.to_dict() for e in log.events]
        result["run_id"] = store.save_run(result)
    else:
        result["events"] = [e.to_dict() for e in log.events]
    return result
