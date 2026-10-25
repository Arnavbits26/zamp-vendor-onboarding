"""
Deterministic name matching. No LLM: the same two names always give the same answer.

  exact     same words once case, punctuation and company suffixes are ignored
            ("Kavya Packaging Pvt Ltd" = "KAVYA PACKAGING PRIVATE LIMITED")
  close     tiny spelling difference ("Facilty" vs "Facility")  -> noted, not blocking
  different anything else
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

_SUFFIXES = {"pvt", "private", "ltd", "limited", "llp", "company", "co", "inc", "opc",
             "corp", "corporation", "llc"}
_NOISE = {"the"}

EXACT, CLOSE, DIFFERENT = "exact", "close", "different"


def tokens(name: str | None) -> list[str]:
    s = (name or "").lower()
    s = re.sub(r"\bm\s*/\s*s\b", " ", s)          # "M/s Sharma Traders"
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return [t for t in s.split() if t not in _NOISE]


def core(name: str | None) -> str:
    return " ".join(t for t in tokens(name) if t not in _SUFFIXES)


def similarity(a: str | None, b: str | None) -> float:
    ca, cb = core(a), core(b)
    if not ca or not cb:
        return 0.0
    if ca == cb:
        return 1.0
    if sorted(ca.split()) == sorted(cb.split()):   # same words, different order
        return 1.0
    return SequenceMatcher(None, ca, cb).ratio()


def classify(a: str | None, b: str | None) -> str:
    s = similarity(a, b)
    return EXACT if s >= 0.999 else CLOSE if s >= 0.88 else DIFFERENT


def best_match(name: str | None, candidates: list[str | None]) -> tuple[str, str | None]:
    """Best classification of `name` against any candidate. Returns (kind, candidate)."""
    best, who = DIFFERENT, None
    order = {EXACT: 2, CLOSE: 1, DIFFERENT: 0}
    for c in candidates:
        if not c:
            continue
        k = classify(name, c)
        if order[k] > order[best]:
            best, who = k, c
    return best, who
