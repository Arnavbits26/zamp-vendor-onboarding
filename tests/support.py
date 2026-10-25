"""Test helpers: an 'oracle' reader that returns what a PERFECT model would read from each specimen."""
import json
from pathlib import Path

from app.completeness import ALL_DOCS

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "test_cases"


def doc(doc_type, **fields):
    return {"doc_type": doc_type, "classification_confidence": "high", "legibility": "good",
            "fields": fields, "notes": ""}


def perfect_read(doc_type, sub):
    co, tax, bank = sub["company"], sub["tax"], sub["bank"]
    prop = co["entity_type"] == "sole_proprietorship"
    person = (sub.get("proprietor_name") if prop else co["vendor_name"]).upper()
    if doc_type == "gst_certificate":
        addr = sub.get("gst_registered_address", co["address"])
        return doc(doc_type, legal_name=person, trade_name=co["vendor_name"].upper(),
                   proprietor_name=sub.get("proprietor_name"), gstin=tax["gstin"],
                   address=addr["line1"], state=addr["state"])
    if doc_type == "pan_card":
        return doc(doc_type, legal_name=person, pan=tax["pan"])
    if doc_type == "cancelled_cheque":
        return doc(doc_type, account_holder_name=bank["account_holder_name"].upper(),
                   account_number=bank["account_number"], ifsc=bank["ifsc"],
                   bank_name=bank["bank_name"].upper(), marked_cancelled=True)
    if doc_type == "certificate_of_incorporation":
        return doc(doc_type, legal_name=co["vendor_name"].upper(), pan=tax["pan"],
                   cin_or_llpin=sub.get("cin"))
    raise ValueError(doc_type)


class OracleReader:
    """Answers document reads in the order the pipeline asks (ALL_DOCS order, present slots only)."""

    def __init__(self, case, wrong=None, tweak=None):
        sub = json.loads((CASES / case / "submission.json").read_text())
        self.queue = []
        for slot in ALL_DOCS:
            if slot in sub["documents"]:
                kind = (wrong or {}).get(slot, slot)
                ex = perfect_read(kind, sub)
                if tweak and slot in tweak:
                    tweak[slot](ex)
                self.queue.append(ex)
        self.drafts = []

    def read(self, **kw):
        return self.queue.pop(0)

    def draft(self, system, prompt):
        self.drafts.append(prompt)
        return ("Hi there, thank you for sending your details; a few things need fixing.\n"
                "Please reply with the items above and we will re-check right away.")


def oracle_for(case):
    return OracleReader(case, wrong={"gst_certificate": "pan_card"} if case == "05a_wrong_document" else None)
