"""
Stage 3 - Document reading.

  triage   : does the file have a machine-readable text layer, or is it an image?
  read     : send text (cheap) or an image (scans/photos) to the model
  verify   : deterministic checks on what came back
               DOC-01  is it the document type the slot asked for?
               DOC-02  could the fields we need actually be read?
               DOC-03  could the file be opened at all?
"""
from __future__ import annotations

import io
import re
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageOps

from app.completeness import ALL_DOCS, DOC_LABELS
from app.config import MAX_IMAGE_SIDE, TEXT_LAYER_MIN_CHARS
from app.events import RunLog
from app.validators import FAIL, PASS, SKIP, WARN, RuleResult

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

# Fields we must be able to read from each document type
REQUIRED_READABLE = {
    "gst_certificate": ["gstin", "legal_name"],
    "pan_card": ["pan", "legal_name"],
    "cancelled_cheque": ["account_number", "ifsc", "account_holder_name"],
    "certificate_of_incorporation": ["cin_or_llpin", "legal_name"],
}
FIELD_LABELS = {
    "gstin": "GSTIN", "legal_name": "name", "pan": "PAN number",
    "account_number": "account number", "ifsc": "IFSC", "account_holder_name": "account holder name",
    "cin_or_llpin": "CIN / LLPIN",
}


# Wording used when a slot receives the wrong kind of document
DETECTED_PHRASE = {
    "gst_certificate": "a GST registration certificate",
    "pan_card": "a PAN card",
    "cancelled_cheque": "a cancelled cheque",
    "certificate_of_incorporation": "a certificate of incorporation",
}


class UnreadableFile(Exception):
    pass


# ------------------------------------------------------------------ triage
def triage(path: str | Path) -> dict:
    """Decide how a file should be read. Text layer present -> 'text', otherwise 'vision'."""
    p = Path(path)
    ext = p.suffix.lower()
    try:
        if ext == ".pdf":
            pdf = pdfium.PdfDocument(str(p))
            pages = len(pdf)
            text = "".join(pdf[i].get_textpage().get_text_range() for i in range(min(pages, 3))).strip()
            pdf.close()
            if len(text) >= TEXT_LAYER_MIN_CHARS:
                return {"kind": "pdf", "route": "text", "chars": len(text), "text": text, "pages": pages}
            return {"kind": "pdf", "route": "vision", "chars": len(text), "text": None, "pages": pages}
        if ext in IMAGE_EXTS:
            Image.open(p).verify()
            return {"kind": "image", "route": "vision", "chars": 0, "text": None, "pages": 1}
    except Exception as e:  # corrupt / password-protected / not really a PDF
        raise UnreadableFile(f"Could not open the file ({type(e).__name__}).") from e
    raise UnreadableFile(f"Unsupported file type '{ext}'. Please upload a PDF, JPG or PNG.")


def render_for_vision(path: str | Path) -> tuple[bytes, str]:
    """First page of a PDF, or an image, as a JPEG capped at MAX_IMAGE_SIDE."""
    p = Path(path)
    try:
        if p.suffix.lower() == ".pdf":
            pdf = pdfium.PdfDocument(str(p))
            img = pdf[0].render(scale=2).to_pil()
            pdf.close()
        else:
            img = ImageOps.exif_transpose(Image.open(p))
        img = img.convert("RGB")
        img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue(), "image/jpeg"
    except Exception as e:
        raise UnreadableFile(f"Could not render the file ({type(e).__name__}).") from e


# --------------------------------------------------------- normalisation
def normalise_extraction(ex: dict) -> dict:
    """Deterministic clean-up of identifiers. The model copies; code normalises."""
    f = dict(ex.get("fields") or {})

    def squash(v):
        return re.sub(r"[\s\-]", "", v).upper() if isinstance(v, str) and v.strip() else None

    for key in ("gstin", "pan", "ifsc"):
        f[key] = squash(f.get(key))
    acct = f.get("account_number")
    f["account_number"] = re.sub(r"\D", "", acct) if isinstance(acct, str) and re.search(r"\d", acct) else None
    for key in ("legal_name", "trade_name", "proprietor_name", "account_holder_name", "bank_name"):
        v = f.get(key)
        f[key] = re.sub(r"\s+", " ", v).strip() if isinstance(v, str) and v.strip() else None
    return {**ex, "fields": f}


# ----------------------------------------------------------- verification
def verify_document(slot: str, ex: dict) -> list[RuleResult]:
    label = DOC_LABELS[slot]
    detected = ex.get("doc_type", "other")
    results: list[RuleResult] = []

    if detected == slot:
        results.append(RuleResult(
            "DOC-01", f"{label}: correct document", PASS,
            f"The file uploaded as '{label}' is a {label.lower()}.",
            {"slot": slot, "detected": detected, "confidence": ex.get("classification_confidence")}))
    else:
        found = DETECTED_PHRASE.get(detected, "a document we could not identify")
        results.append(RuleResult(
            "DOC-01", f"{label}: correct document", FAIL,
            f"The file uploaded as '{label}' appears to be {found}.",
            {"slot": slot, "detected": detected, "confidence": ex.get("classification_confidence")}))
        results.append(RuleResult(
            "DOC-02", f"{label}: fields readable", SKIP,
            "Skipped because this is not the requested document.", {"slot": slot}))
        return results

    fields = ex.get("fields") or {}
    missing = [FIELD_LABELS[k] for k in REQUIRED_READABLE[slot] if not fields.get(k)]
    legibility = ex.get("legibility", "good")
    if legibility == "poor" or missing:
        why = ("The image is too unclear to read reliably. " if legibility == "poor" else "")
        if missing:
            why += "Could not read: " + ", ".join(missing) + "."
        results.append(RuleResult("DOC-02", f"{label}: fields readable", FAIL, why.strip(),
                                  {"slot": slot, "missing": missing, "legibility": legibility}))
    elif legibility == "partial":
        results.append(RuleResult(
            "DOC-02", f"{label}: fields readable", WARN,
            "All required fields were read, but parts of the document are hard to read.",
            {"slot": slot, "legibility": legibility}))
    else:
        results.append(RuleResult(
            "DOC-02", f"{label}: fields readable", PASS,
            "All required fields were read clearly.", {"slot": slot, "legibility": legibility}))
    return results


# ------------------------------------------------------------ orchestrator
def read_documents(sub: dict, reader, log: RunLog | None = None) -> dict:
    """Read every uploaded document. Returns {slot: {route, chars, extraction, rules}}."""
    out: dict = {}
    paths = sub.get("_doc_paths", {})
    slots = [s for s in ALL_DOCS if s in paths and Path(paths[s]).is_file()]
    if log:
        log.emit("documents", "started", f"Reading {len(slots)} documents", slots=slots)

    for slot in slots:
        label = DOC_LABELS[slot]
        entry = {"slot": slot, "route": None, "chars": 0, "extraction": None, "rules": []}
        try:
            t = triage(paths[slot])
            entry["route"], entry["chars"] = t["route"], t["chars"]
            if log:
                if t["route"] == "text":
                    log.emit("documents", "info",
                             f"{label}: text layer found ({t['chars']} characters) - reading as text",
                             slot=slot, route="text")
                else:
                    log.emit("documents", "info",
                             f"{label}: no text layer ({t['kind']}) - reading as image",
                             slot=slot, route="vision")
            if t["route"] == "text":
                raw = reader.read(route="text", text=t["text"])
            else:
                img, mt = render_for_vision(paths[slot])
                raw = reader.read(route="vision", image=img, media_type=mt)
            ex = normalise_extraction(raw)
            entry["extraction"] = ex
            entry["rules"] = verify_document(slot, ex)
        except UnreadableFile as e:
            entry["rules"] = [RuleResult("DOC-03", f"{label}: file opens", FAIL, str(e), {"slot": slot})]

        if log:
            bad = [r for r in entry["rules"] if r.status == FAIL]
            log.emit("documents", "warn" if bad else "info",
                     f"{label}: " + ("; ".join(r.detail for r in bad) if bad else "read and verified"),
                     slot=slot, rules=[r.to_dict() for r in entry["rules"]],
                     fields=(entry["extraction"] or {}).get("fields"))
        out[slot] = entry

    if log:
        n_bad = sum(1 for e in out.values() if any(r.status == FAIL for r in e["rules"]))
        log.emit("documents", "warn" if n_bad else "done",
                 f"{len(out) - n_bad} of {len(out)} documents passed" if out else "No documents to read")
    return out
