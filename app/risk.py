"""
Stage 6 - Risk checks against the existing vendor master.

These are the "subtle inconsistencies that only show up when you cross-reference":
everything on the submission can be valid and still be fraud.
Details here are INTERNAL. They never go to the vendor.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from app.completeness import COMPANY_TYPES
from app.config import ROOT
from app.names import DIFFERENT, classify
from app.validators import FAIL, PASS, SKIP, WARN, RuleResult

FREE_EMAIL_DOMAINS = {"gmail.com", "yahoo.com", "yahoo.in", "yahoo.co.in", "outlook.com",
                      "hotmail.com", "rediffmail.com", "proton.me", "protonmail.com", "icloud.com"}


def load_vendor_master(path: Path | None = None) -> list[dict]:
    return json.loads((path or ROOT / "data" / "vendors_master.json").read_text())


def _id(v): return re.sub(r"[\s\-]", "", str(v or "")).upper()
def _digits(v): return re.sub(r"\D", "", str(v or ""))


def run_risk(sub: dict, docs: dict, master: list[dict], xref_results: list[RuleResult], log=None) -> list[RuleResult]:
    from app.crossref import usable
    if log:
        log.emit("risk", "started", f"Checking against {len(master)} existing vendors")
    out: list[RuleResult] = []

    # ---- RISK-01 bank account already belongs to someone else
    cheque = usable(docs, "cancelled_cheque") or {}
    accounts = {(_id(sub["bank"].get("ifsc")), _digits(sub["bank"].get("account_number")))}
    if cheque.get("account_number"):
        accounts.add((_id(cheque.get("ifsc")), _digits(cheque["account_number"])))
    exact = [v for v in master if (_id(v["ifsc"]), _digits(v["account_number"])) in accounts]
    same_no = [v for v in master if _digits(v["account_number"]) in {a for _, a in accounts} and v not in exact]
    if exact:
        v = exact[0]
        out.append(RuleResult("RISK-01", "Bank account not used by another vendor", FAIL,
                              f"This exact bank account (IFSC + account number) already belongs to existing vendor "
                              f"{v['vendor_id']} '{v['legal_name']}'. Possible payment-redirection fraud.",
                              {"matched_vendor": v["vendor_id"], "matched_name": v["legal_name"]}))
    elif same_no:
        v = same_no[0]
        out.append(RuleResult("RISK-01", "Bank account not used by another vendor", WARN,
                              f"Same account number as {v['vendor_id']} '{v['legal_name']}' but a different bank "
                              "(IFSC). Probably a coincidence across banks; worth a glance.",
                              {"matched_vendor": v["vendor_id"]}))
    else:
        out.append(RuleResult("RISK-01", "Bank account not used by another vendor", PASS,
                              "No existing vendor uses this bank account."))

    # ---- RISK-02 GSTIN already onboarded
    g = _id(sub["tax"].get("gstin"))
    dup = next((v for v in master if _id(v["gstin"]) == g), None)
    if dup:
        out.append(RuleResult("RISK-02", "GSTIN not already onboarded", FAIL,
                              f"GSTIN {g} is already registered to {dup['vendor_id']} '{dup['legal_name']}'.",
                              {"matched_vendor": dup["vendor_id"]}))
    else:
        out.append(RuleResult("RISK-02", "GSTIN not already onboarded", PASS, "GSTIN is new to the vendor master."))

    # ---- RISK-03 PAN already onboarded (legit if it's an additional state registration of the SAME entity)
    pan = _id(sub["tax"].get("pan"))
    if out[-1].status == FAIL:
        out.append(RuleResult("RISK-03", "PAN not used by an unrelated vendor", SKIP, "Covered by RISK-02."))
    else:
        same_pan = next((v for v in master if _id(v["pan"]) == pan), None)
        if not same_pan:
            out.append(RuleResult("RISK-03", "PAN not used by an unrelated vendor", PASS, "PAN is new to the vendor master."))
        elif classify(sub["company"]["vendor_name"], same_pan["legal_name"]) != DIFFERENT:
            out.append(RuleResult("RISK-03", "PAN not used by an unrelated vendor", WARN,
                                  f"PAN already belongs to {same_pan['vendor_id']} '{same_pan['legal_name']}' - same "
                                  "name, so likely an additional state registration of the same business.",
                                  {"matched_vendor": same_pan["vendor_id"]}))
        else:
            out.append(RuleResult("RISK-03", "PAN not used by an unrelated vendor", FAIL,
                                  f"PAN already belongs to {same_pan['vendor_id']} '{same_pan['legal_name']}', "
                                  f"but this submission is for '{sub['company']['vendor_name']}'. Identity conflict.",
                                  {"matched_vendor": same_pan["vendor_id"]}))

    # ---- RISK-04 pattern: individually fixable, collectively suspicious
    n_bad = sum(1 for r in xref_results if r.status == FAIL)
    if n_bad >= 3:
        out.append(RuleResult("RISK-04", "Not many unrelated inconsistencies", FAIL,
                              f"{n_bad} separate cross-checks failed. Each could be a typo, but together they "
                              "suggest the documents may not belong to the same business.", {"count": n_bad}))
    else:
        out.append(RuleResult("RISK-04", "Not many unrelated inconsistencies", PASS,
                              f"{n_bad} cross-check failure(s): within the range of ordinary mistakes."))

    # ---- RISK-05 free e-mail on a company
    domain = (sub["company"].get("contact", {}).get("email") or "").rsplit("@", 1)[-1].lower()
    if sub["company"].get("entity_type") in COMPANY_TYPES and domain in FREE_EMAIL_DOMAINS:
        out.append(RuleResult("RISK-05", "Business email domain", WARN,
                              f"A {sub['company']['entity_type'].replace('_', ' ')} is using a free email address "
                              f"({domain}). Not blocking, but worth a look."))
    else:
        out.append(RuleResult("RISK-05", "Business email domain", PASS, "Email domain is acceptable for this business type."))

    if log:
        bad = [r for r in out if r.status == FAIL]
        log.emit("risk", "warn" if bad else "done",
                 "Risk flags: " + ", ".join(r.rule_id for r in bad) if bad else "No risk flags",
                 rules=[r.to_dict() for r in out])
    return out
