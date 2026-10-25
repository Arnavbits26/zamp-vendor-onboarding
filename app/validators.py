"""
Deterministic format validators for Indian vendor identifiers.

No LLM involved here on purpose: format checks must be exact, repeatable
and explainable. Every check returns a RuleResult with a stable rule ID
so the decision engine (and the UI) can cite exactly what passed or failed.

Identifiers covered
-------------------
PAN   : AAAAA9999A  - 5 letters, 4 digits, 1 letter
        4th char = holder type (P individual, C company, F firm/LLP, ...)
        5th char = first letter of surname (individual) or of entity name
GSTIN : 2-digit state code + 10-char PAN + entity no. + 'Z' + check char
        Check char uses the official mod-36 Luhn-style checksum.
IFSC  : 4-letter bank code + '0' + 6-char branch code
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Optional

# --------------------------------------------------------------------------
# Result structure
# --------------------------------------------------------------------------

PASS, FAIL, WARN, SKIP = "PASS", "FAIL", "WARN", "SKIP"


@dataclass
class RuleResult:
    rule_id: str          # stable ID, e.g. "FMT-GSTIN-02"
    name: str             # short human label
    status: str           # PASS / FAIL / WARN / SKIP
    detail: str           # plain-English explanation shown in the UI
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# Reference data
# --------------------------------------------------------------------------

GST_STATE_CODES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab",
    "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana", "07": "Delhi",
    "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim",
    "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur",
    "15": "Mizoram", "16": "Tripura", "17": "Meghalaya", "18": "Assam",
    "19": "West Bengal", "20": "Jharkhand", "21": "Odisha",
    "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "26": "Dadra and Nagar Haveli and Daman and Diu", "27": "Maharashtra",
    "29": "Karnataka", "30": "Goa", "31": "Lakshadweep", "32": "Kerala",
    "33": "Tamil Nadu", "34": "Puducherry", "35": "Andaman and Nicobar Islands",
    "36": "Telangana", "37": "Andhra Pradesh", "38": "Ladakh",
}

PAN_HOLDER_TYPES = {
    "P": "Individual / Sole proprietor",
    "C": "Company",
    "F": "Firm / LLP",
    "H": "Hindu Undivided Family",
    "A": "Association of Persons",
    "T": "Trust",
    "B": "Body of Individuals",
    "L": "Local Authority",
    "J": "Artificial Juridical Person",
    "G": "Government",
}

# Declared entity type on the form -> PAN 4th characters that are consistent
ENTITY_TYPE_TO_PAN_CODES = {
    "private_limited": {"C"},
    "public_limited": {"C"},
    "llp": {"F"},
    "partnership": {"F"},
    "sole_proprietorship": {"P"},
    "trust": {"T"},
}

IFSC_BANK_CODES = {
    "SBIN": "State Bank of India", "HDFC": "HDFC Bank", "ICIC": "ICICI Bank",
    "UTIB": "Axis Bank", "KKBK": "Kotak Mahindra Bank", "PUNB": "Punjab National Bank",
    "BARB": "Bank of Baroda", "CNRB": "Canara Bank", "YESB": "Yes Bank",
    "IDFB": "IDFC FIRST Bank", "INDB": "IndusInd Bank", "UBIN": "Union Bank of India",
}

PAN_RE = re.compile(r"^[A-Z]{3}[ABCFGHLJPT][A-Z][0-9]{4}[A-Z]$")
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")

_GST_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _clean(value: Optional[str]) -> str:
    return (value or "").strip().upper().replace(" ", "")


# --------------------------------------------------------------------------
# GSTIN checksum
# --------------------------------------------------------------------------

def gstin_check_char(first14: str) -> str:
    """Official GSTIN check character (mod-36, alternating weights 1/2)."""
    total = 0
    for i, ch in enumerate(first14):
        weight = 1 if i % 2 == 0 else 2
        product = _GST_CHARS.index(ch) * weight
        total += product // 36 + product % 36
    return _GST_CHARS[(36 - total % 36) % 36]


# --------------------------------------------------------------------------
# PAN
# --------------------------------------------------------------------------

def validate_pan(pan: Optional[str]) -> list[RuleResult]:
    pan = _clean(pan)
    if not pan:
        return [RuleResult("FMT-PAN-01", "PAN format", FAIL, "PAN was not provided.")]
    if not PAN_RE.match(pan):
        return [RuleResult(
            "FMT-PAN-01", "PAN format", FAIL,
            f"'{pan}' is not a valid PAN. Expected 5 letters, 4 digits, 1 letter "
            "(e.g. ABCPS1234K).", {"pan": pan})]
    holder = PAN_HOLDER_TYPES.get(pan[3], "Unknown")
    return [RuleResult(
        "FMT-PAN-01", "PAN format", PASS,
        f"PAN is well-formed. Holder type from 4th character '{pan[3]}': {holder}.",
        {"pan": pan, "holder_type_code": pan[3], "holder_type": holder})]


def pan_holder_type(pan: str) -> Optional[str]:
    pan = _clean(pan)
    return pan[3] if PAN_RE.match(pan) else None


# --------------------------------------------------------------------------
# GSTIN
# --------------------------------------------------------------------------

def parse_gstin(gstin: str) -> dict:
    g = _clean(gstin)
    return {
        "state_code": g[:2],
        "state": GST_STATE_CODES.get(g[:2]),
        "pan": g[2:12],
        "entity_number": g[12],
        "check_char": g[14],
    }


def validate_gstin(gstin: Optional[str]) -> list[RuleResult]:
    """Format, state code and checksum. Returns one result per check."""
    g = _clean(gstin)
    if not g:
        return [RuleResult("FMT-GSTIN-01", "GSTIN format", FAIL, "GSTIN was not provided.")]
    if not GSTIN_RE.match(g):
        return [RuleResult(
            "FMT-GSTIN-01", "GSTIN format", FAIL,
            f"'{g}' does not follow the 15-character GSTIN structure "
            "(2-digit state code, 10-char PAN, entity number, 'Z', check character).",
            {"gstin": g})]

    results = [RuleResult("FMT-GSTIN-01", "GSTIN format", PASS,
                          "GSTIN has the correct 15-character structure.", {"gstin": g})]
    parts = parse_gstin(g)

    if parts["state"] is None:
        results.append(RuleResult(
            "FMT-GSTIN-02", "GSTIN state code", FAIL,
            f"State code '{parts['state_code']}' is not a valid GST state code.",
            {"state_code": parts["state_code"]}))
    else:
        results.append(RuleResult(
            "FMT-GSTIN-02", "GSTIN state code", PASS,
            f"State code {parts['state_code']} = {parts['state']}.",
            {"state_code": parts["state_code"], "state": parts["state"]}))

    expected = gstin_check_char(g[:14])
    if expected != parts["check_char"]:
        results.append(RuleResult(
            "FMT-GSTIN-03", "GSTIN checksum", FAIL,
            f"Check character is '{parts['check_char']}' but should be '{expected}'. "
            "This usually means a typo in the GSTIN.",
            {"expected": expected, "actual": parts["check_char"]}))
    else:
        results.append(RuleResult(
            "FMT-GSTIN-03", "GSTIN checksum", PASS,
            "Checksum is valid.", {"check_char": expected}))

    # The PAN embedded in the GSTIN is compared to the declared PAN in the
    # cross-reference stage, not here.
    return results


# --------------------------------------------------------------------------
# IFSC
# --------------------------------------------------------------------------

def validate_ifsc(ifsc: Optional[str]) -> list[RuleResult]:
    code = _clean(ifsc)
    if not code:
        return [RuleResult("FMT-IFSC-01", "IFSC format", FAIL, "IFSC was not provided.")]
    if not IFSC_RE.match(code):
        return [RuleResult(
            "FMT-IFSC-01", "IFSC format", FAIL,
            f"'{code}' is not a valid IFSC. Expected 4 letters, then '0', then 6 characters "
            "(e.g. HDFC0001234).", {"ifsc": code})]
    bank = IFSC_BANK_CODES.get(code[:4])
    detail = f"IFSC is well-formed. Bank code {code[:4]}" + (f" = {bank}." if bank else " (not in local bank list).")
    return [RuleResult("FMT-IFSC-01", "IFSC format", PASS, detail,
                       {"ifsc": code, "bank_code": code[:4], "bank": bank})]


def validate_account_number(acct: Optional[str]) -> list[RuleResult]:
    a = (acct or "").strip().replace(" ", "")
    if not a:
        return [RuleResult("FMT-ACCT-01", "Bank account number", FAIL,
                           "Bank account number was not provided.")]
    if not a.isdigit() or not 9 <= len(a) <= 18:
        return [RuleResult("FMT-ACCT-01", "Bank account number", FAIL,
                           f"Account number should be 9-18 digits; got '{a}'.", {"account": a})]
    return [RuleResult("FMT-ACCT-01", "Bank account number", PASS,
                       f"Account number is {len(a)} digits.", {"account_last4": a[-4:]})]


# --------------------------------------------------------------------------
# Convenience: run every format check on a submission
# --------------------------------------------------------------------------

def run_format_checks(submission: dict) -> list[RuleResult]:
    tax = submission.get("tax", {})
    bank = submission.get("bank", {})
    results: list[RuleResult] = []
    results += validate_pan(tax.get("pan"))
    results += validate_gstin(tax.get("gstin"))
    results += validate_ifsc(bank.get("ifsc"))
    results += validate_account_number(bank.get("account_number"))
    return results
