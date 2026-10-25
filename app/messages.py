"""
Stage 8 - Messages.

  vendor  : APPROVED -> welcome template.
            PENDING  -> the asks are inserted VERBATIM by code; the model (if available) only
                        writes a friendly opening and closing line. If the model fails or says
                        anything it shouldn't, a plain template is used instead.
            REJECTED -> a FIXED neutral template. No model, no reasons. You don't explain your
                        fraud checks to a possible fraudster.
  internal: full reasoning with rule IDs, for the procurement team. Always deterministic.
"""
from __future__ import annotations

import re

from app.decision import APPROVED, PENDING, REJECTED
from app.llm import ExtractionError

FORBIDDEN = re.compile(r"fraud|suspicio|risk|duplicate|reject|rule\b|XREF|RISK|FMT|CMP|DOC-|blacklist|"
                       r"other vendor|existing vendor", re.I)

DRAFT_SYSTEM = ("You write short, warm, professional emails from a company's procurement team to a "
                "vendor who is being onboarded. Plain text only.")


def _contact(sub):
    return (sub["company"].get("contact", {}).get("name") or "there").split()[0]


def _company(sub):
    return sub["company"]["vendor_name"]


def _ref(sub):
    v = sub.get("version", 1)
    return f"{sub['submission_id']}" + (f", v{v}" if v and v > 1 else "")


def _numbered(asks):
    return "\n".join(f"  {i}. {a}" for i, a in enumerate(asks, 1))


def _llm_framing(sub, asks, reader):
    """Opening + closing sentences from the model, or None if unavailable/unsafe."""
    if reader is None or not hasattr(reader, "draft"):
        return None
    prompt = (f"Vendor contact first name: {_contact(sub)}\nCompany: {_company(sub)}\n"
              f"Situation: their onboarding submission needs {len(asks)} correction(s) before approval. "
              "The list of corrections will be inserted between your two parts by the system.\n\n"
              "Write exactly two lines and nothing else:\n"
              "Line 1: a friendly 1-2 sentence opening (greet them by first name, thank them, say a few "
              "things need fixing).\n"
              "Line 2: a friendly 1-2 sentence closing (say what to do next: reply with the items, and "
              "that you will re-check straight away).\n"
              "Do not list the corrections, do not mention reasons, checks, systems or other companies.")
    try:
        text = reader.draft(DRAFT_SYSTEM, prompt)
    except ExtractionError:
        return None
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if len(lines) != 2 or any(FORBIDDEN.search(l) or len(l) > 400 for l in lines):
        return None
    return lines


def vendor_message(decision: dict, sub: dict, reader=None) -> dict:
    status, name, company, ref = decision["status"], _contact(sub), _company(sub), _ref(sub)
    if status == APPROVED:
        return {"source": "template", "subject": f"Approved: {company} is now a registered vendor ({ref})",
                "body": f"Dear {name},\n\n" + ("Thank you for sending the corrected information. " if sub.get("version", 1) > 1 else "") +
                        f"Good news: your vendor onboarding for {company} has been approved. "
                        "Your details are now on our vendor records and you can start working with us.\n\n"
                        "Regards,\nProcurement Team"}
    if status == REJECTED:
        return {"source": "template", "subject": f"Update on your vendor onboarding ({ref})",
                "body": f"Dear {name},\n\nThank you for submitting the details for {company}. After review, we are "
                        "unable to complete your onboarding with the information provided. If we need anything "
                        "further, a member of our team will contact you directly.\n\nRegards,\nProcurement Team"}
    asks = decision["asks"]
    framing = _llm_framing(sub, asks, reader)
    opening = framing[0] if framing else (
        f"Dear {name},\n\nThank you for submitting the details for {company}. We have reviewed them and "
        "need a few things from you before we can approve your onboarding:")
    closing = framing[1] if framing else (
        "Once you send these (reply to this email or update your submission), we will re-check "
        "straight away.")
    return {"source": "llm" if framing else "template",
            "subject": f"Action needed: your vendor onboarding ({ref})",
            "body": f"{opening}\n\n{_numbered(asks)}\n\n{closing}\n\nRegards,\nProcurement Team"}


NEXT_STEP = {APPROVED: "Vendor approved and recorded. Notify the requester.",
             PENDING: "Waiting for the vendor. Re-run this submission when they respond.",
             REJECTED: "Do NOT onboard. Review manually. Do not share the reasons with the vendor."}


def internal_note(decision: dict, sub: dict) -> str:
    c = decision["counts"]
    lines = [f"VENDOR ONBOARDING REVIEW - {_ref(sub)}",
             f"Vendor: {_company(sub)}  |  Business type: {sub['company'].get('entity_type')}",
             f"DECISION: {decision['status']} - {decision['headline']}", ""]
    if decision["blocking"]:
        lines.append("Why:")
        lines += [f"  - [{r['rule_id']}] {r['detail']}" for r in decision["blocking"]]
        lines.append("")
    if decision["warnings"]:
        lines.append("Noted (not blocking):")
        lines += [f"  - [{r['rule_id']}] {r['detail']}" for r in decision["warnings"]]
        lines.append("")
    lines.append(f"Checks: {c['total']} run | {c['passed']} passed | {c['failed']} failed | "
                 f"{c['warnings']} warnings | {c['skipped']} skipped")
    lines.append(f"Next step: {NEXT_STEP[decision['status']]}")
    return "\n".join(lines)
