"""
Stage 7 - The decision. Deterministic: same rule results in, same verdict out.

    any REJECT-policy rule FAILED  -> REJECTED  (escalate; vendor gets a neutral message)
    else any other rule FAILED     -> PENDING   (vendor can fix it; vendor gets exact asks)
    else                           -> APPROVED  (warnings are listed but don't block)

SKIP never blocks by itself: the reason a rule was skipped (a missing or wrong document)
is already a FAIL elsewhere. Missing information is recoverable; contradictory
identity information is a red flag. That distinction is the whole design.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from app.documents import DETECTED_PHRASE
from app.validators import FAIL, PASS, SKIP, WARN, RuleResult

APPROVED, PENDING, REJECTED = "APPROVED", "PENDING", "REJECTED"
REJECT_RULES = {"RISK-01", "RISK-02", "RISK-03", "RISK-04"}

ASK_LABEL = {"gst_certificate": "GST registration certificate", "pan_card": "PAN card",
             "cancelled_cheque": "cancelled cheque", "certificate_of_incorporation": "certificate of incorporation"}


def vendor_ask(r: RuleResult) -> str:
    """Vendor-safe wording of what to fix. Never mentions risk checks or other vendors."""
    ev, rid = r.evidence or {}, r.rule_id
    slot = ev.get("slot")
    if ev.get("ask"):
        return ev["ask"]
    if rid == "CMP-01":
        return "Please select your business type (for example Private Limited, LLP or Proprietorship)."
    if rid == "CMP-02":
        return "Please fill in the missing details: " + ", ".join(ev.get("missing", [])) + "."
    if rid == "CMP-03":
        return "Please upload the missing documents: " + ", ".join(ev.get("missing", [])) + "."
    if rid.startswith("FMT-"):
        return r.detail
    if rid == "DOC-01":
        found = DETECTED_PHRASE.get(ev.get("detected"), "a document we could not identify")
        return (f"The file you uploaded as your {ASK_LABEL[slot]} looks like {found}. "
                f"Please upload your {ASK_LABEL[slot]}.")
    if rid == "DOC-02":
        return f"Please upload a clearer copy of your {ASK_LABEL[slot]}. {r.detail}"
    if rid == "DOC-03":
        return f"Please re-upload your {ASK_LABEL[slot]}. {r.detail}"
    return r.detail


@dataclass
class Decision:
    status: str
    headline: str
    blocking: list = field(default_factory=list)     # rules that decided the outcome
    warnings: list = field(default_factory=list)
    asks: list = field(default_factory=list)         # vendor-safe, for PENDING
    counts: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def decide(rules: list[RuleResult]) -> Decision:
    fails = [r for r in rules if r.status == FAIL]
    warns = [r for r in rules if r.status == WARN]
    counts = {"total": len(rules), "passed": sum(r.status == PASS for r in rules), "failed": len(fails),
              "warnings": len(warns), "skipped": sum(r.status == SKIP for r in rules)}

    reject = [r for r in fails if r.rule_id in REJECT_RULES]
    if reject:
        return Decision(REJECTED, "Possible fraud or identity conflict. Escalate to procurement; "
                        "the vendor gets a neutral message.",
                        [r.to_dict() for r in reject], [r.to_dict() for r in warns], [], counts)
    if fails:
        asks, seen = [], set()
        for r in fails:
            a = vendor_ask(r)
            if a not in seen:
                seen.add(a); asks.append(a)
        n = len(asks)
        return Decision(PENDING, f"{n} thing{'s' if n != 1 else ''} to fix before approval.",
                        [r.to_dict() for r in fails], [r.to_dict() for r in warns], asks, counts)
    note = f" ({len(warns)} warning{'s' if len(warns) != 1 else ''} noted)" if warns else ""
    return Decision(APPROVED, f"All {counts['passed']} checks passed{note}.",
                    [], [r.to_dict() for r in warns], [], counts)
