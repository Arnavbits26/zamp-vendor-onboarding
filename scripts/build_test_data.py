"""
Builds all test inputs for the vendor onboarding process.

Outputs
-------
data/vendors_master.json      existing approved vendors (for duplicate checks)
test_cases/<case>/submission.json
test_cases/<case>/docs/*      specimen documents (PDFs and phone-photo JPGs)
test_cases/manifest.json      case list + expected outcome (NOT read by the pipeline)

All entities, numbers and documents are fictitious and watermarked SPECIMEN.
Run:  python scripts/build_test_data.py
"""
from __future__ import annotations

import io
import json
import random
import shutil
import sys
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.validators import gstin_check_char  # noqa: E402

DATA = ROOT / "data"
CASES = ROOT / "test_cases"
random.seed(42)


def gstin(state: str, pan: str, entity: str = "1") -> str:
    first14 = f"{state}{pan}{entity}Z"
    return first14 + gstin_check_char(first14)


# ==========================================================================
# 1. Vendor master (already-onboarded vendors)
# ==========================================================================

VENDOR_MASTER = [
    {"vendor_id": "V-0001", "legal_name": "Orbit Office Solutions Pvt Ltd", "pan": "AAHCO2219K",
     "state": "07", "ifsc": "ICIC0000456", "account_number": "045601503321"},
    {"vendor_id": "V-0002", "legal_name": "Bluepeak Cleaning Services LLP", "pan": "AAEFB4410R",
     "state": "29", "ifsc": "HDFC0001187", "account_number": "50200011872245"},
    {"vendor_id": "V-0003", "legal_name": "Sunrise Stationers", "pan": "CKLPM5521A",
     "state": "20", "ifsc": "SBIN0007712", "account_number": "38110045672"},
    {"vendor_id": "V-0004", "legal_name": "Metroline Courier Pvt Ltd", "pan": "AABCM8813P",
     "state": "27", "ifsc": "UTIB0000310", "account_number": "917020044518823"},
    {"vendor_id": "V-0005", "legal_name": "Coastal IT Hardware Pvt Ltd", "pan": "AADCC3348F",
     "state": "33", "ifsc": "KKBK0007801", "account_number": "7811230045"},
    {"vendor_id": "V-0006", "legal_name": "Vertex Security Agency", "pan": "BNVPK6630J",
     "state": "36", "ifsc": "BARB0BANJAR", "account_number": "26450100009934"},
    {"vendor_id": "V-0007", "legal_name": "Harbor Catering Co LLP", "pan": "AAIFH7720C",
     "state": "27", "ifsc": "YESB0000221", "account_number": "022163400001298"},
    {"vendor_id": "V-0008", "legal_name": "Pinnacle Print House Pvt Ltd", "pan": "AAGCP1904E",
     "state": "29", "ifsc": "CNRB0002651", "account_number": "2651101004418"},
]
for v in VENDOR_MASTER:
    v["gstin"] = gstin(v["state"], v["pan"])
    v["status"] = "approved"
    del v["state"]


# ==========================================================================
# 2. Submissions
# ==========================================================================

def company_docs():
    return ["gst_certificate", "pan_card", "cancelled_cheque", "certificate_of_incorporation"]


SUBMISSIONS = {
    # ---------------------------------------------------------------- happy
    "01_happy_path": {
        "expected": "APPROVED",
        "why": "Clean private limited company; every field and document agrees.",
        "sub": {
            "submission_id": "SUB-1001",
            "company": {
                "vendor_name": "Kavya Packaging Solutions Pvt Ltd",
                "entity_type": "private_limited",
                "address": {"line1": "Plot 42, Peenya Industrial Area, Phase 2",
                            "city": "Bengaluru", "state": "Karnataka", "pincode": "560058"},
                "contact": {"name": "Ananya Rao", "email": "accounts@kavyapackaging.in",
                            "phone": "+91 98450 21344"},
            },
            "tax": {"pan": "AAKCK4821M", "gstin": gstin("29", "AAKCK4821M")},
            "bank": {"account_holder_name": "Kavya Packaging Solutions Pvt Ltd",
                     "account_number": "50200078453219", "ifsc": "HDFC0004521",
                     "bank_name": "HDFC Bank"},
            "cin": "U25202KA2019PTC124587",
            "incorporated_on": "2019-03-14",
        },
        "docs": {"gst_certificate": "pdf", "pan_card": "photo",
                 "cancelled_cheque": "photo", "certificate_of_incorporation": "pdf"},
    },

    # ------------------------------------------- EC1 legitimate name mismatch
    "02_trade_name_vs_legal_name": {
        "expected": "APPROVED",
        "why": "Form says 'Sharma Electricals', bank says 'Rajesh Sharma'. Looks like a "
               "mismatch, but the GST certificate links the trade name to the proprietor "
               "and the PAN is an individual's (4th char P).",
        "sub": {
            "submission_id": "SUB-1002",
            "company": {
                "vendor_name": "Sharma Electricals",
                "entity_type": "sole_proprietorship",
                "address": {"line1": "Shop 7, Main Road, near Albert Ekka Chowk",
                            "city": "Ranchi", "state": "Jharkhand", "pincode": "834001"},
                "contact": {"name": "Rajesh Sharma", "email": "sharmaelectricals.rnc@gmail.com",
                            "phone": "+91 94311 56720"},
            },
            "tax": {"pan": "BQRPS7712D", "gstin": gstin("20", "BQRPS7712D")},
            "bank": {"account_holder_name": "Rajesh Sharma",
                     "account_number": "33872910456", "ifsc": "SBIN0000167",
                     "bank_name": "State Bank of India"},
            "proprietor_name": "Rajesh Sharma",
        },
        "docs": {"gst_certificate": "scan", "pan_card": "photo", "cancelled_cheque": "photo"},
    },

    # ------------------------------------------------ EC2 bank account reuse
    "03_bank_account_reused": {
        "expected": "REJECTED",
        "why": "Every field and document is valid on its own, but the bank account "
               "already belongs to existing vendor V-0001 (Orbit Office Solutions). "
               "Classic payment-fraud pattern: escalate internally, neutral message to vendor.",
        "sub": {
            "submission_id": "SUB-1003",
            "company": {
                "vendor_name": "Nexora Tech Supplies Pvt Ltd",
                "entity_type": "private_limited",
                "address": {"line1": "B-118, Okhla Industrial Estate, Phase 1",
                            "city": "New Delhi", "state": "Delhi", "pincode": "110020"},
                "contact": {"name": "Vikram Batra", "email": "procure@nexoratech.co.in",
                            "phone": "+91 98110 44129"},
            },
            "tax": {"pan": "AAFCN3390Q", "gstin": gstin("07", "AAFCN3390Q")},
            "bank": {"account_holder_name": "Nexora Tech Supplies Pvt Ltd",
                     "account_number": "045601503321", "ifsc": "ICIC0000456",
                     "bank_name": "ICICI Bank"},
            "cin": "U51909DL2022PTC398112",
            "incorporated_on": "2022-06-02",
        },
        "docs": {"gst_certificate": "pdf", "pan_card": "photo",
                 "cancelled_cheque": "photo", "certificate_of_incorporation": "pdf"},
    },

    # ------------------------------------------------- EC3 GST state mismatch
    "04_gst_state_mismatch": {
        "expected": "PENDING",
        "why": "GSTIN is registered in Karnataka (29) but the billing address is in Pune, "
               "Maharashtra. Not fraud by default: businesses hold one GSTIN per state. "
               "Ask for the Maharashtra GSTIN or a confirmed Karnataka billing address.",
        "sub": {
            "submission_id": "SUB-1004",
            "company": {
                "vendor_name": "Deccan Logistics LLP",
                "entity_type": "llp",
                "address": {"line1": "Unit 5, Chakan MIDC, Phase 2",
                            "city": "Pune", "state": "Maharashtra", "pincode": "410501"},
                "contact": {"name": "Meera Kulkarni", "email": "vendor@deccanlogistics.in",
                            "phone": "+91 99230 77861"},
            },
            "tax": {"pan": "AAMFD6621L", "gstin": gstin("29", "AAMFD6621L")},
            "bank": {"account_holder_name": "Deccan Logistics LLP",
                     "account_number": "922020034417781", "ifsc": "UTIB0000789",
                     "bank_name": "Axis Bank"},
            "cin": "AAQ-4471",
            "incorporated_on": "2020-11-09",
            "gst_registered_address": {"line1": "No. 18, Hosur Road, Electronic City",
                                       "city": "Bengaluru", "state": "Karnataka",
                                       "pincode": "560100"},
        },
        "docs": {"gst_certificate": "pdf", "pan_card": "photo",
                 "cancelled_cheque": "photo", "certificate_of_incorporation": "pdf"},
    },

    # ------------------------------------- EC4 wrong doc, then resubmission
    "05a_wrong_document": {
        "expected": "PENDING",
        "why": "PAN card uploaded in the GST certificate slot. Document classification "
               "catches it; vendor is asked for exactly that one document.",
        "sub": None,  # filled below from the shared Greenleaf base
        "docs": {"gst_certificate": "WRONG:pan_card", "pan_card": "photo",
                 "cancelled_cheque": "photo", "certificate_of_incorporation": "pdf"},
    },
    "05b_wrong_document_resubmitted": {
        "expected": "APPROVED",
        "why": "Same vendor resubmits with the correct GST certificate. Only the failed "
               "check needs to re-run; outcome moves PENDING -> APPROVED.",
        "sub": None,
        "docs": {"gst_certificate": "pdf", "pan_card": "photo",
                 "cancelled_cheque": "photo", "certificate_of_incorporation": "pdf"},
    },
}

GREENLEAF = {
    "company": {
        "vendor_name": "Greenleaf Facility Services Pvt Ltd",
        "entity_type": "private_limited",
        "address": {"line1": "402, Raheja Plaza, LBS Marg, Ghatkopar West",
                    "city": "Mumbai", "state": "Maharashtra", "pincode": "400086"},
        "contact": {"name": "Farhan Qureshi", "email": "onboarding@greenleaffs.com",
                    "phone": "+91 98200 63318"},
    },
    "tax": {"pan": "AAJCG5508H", "gstin": gstin("27", "AAJCG5508H")},
    "bank": {"account_holder_name": "Greenleaf Facility Services Pvt Ltd",
             "account_number": "1834002100056712", "ifsc": "PUNB0183400",
             "bank_name": "Punjab National Bank"},
    "cin": "U74999MH2018PTC312090",
    "incorporated_on": "2018-08-21",
}
SUBMISSIONS["05a_wrong_document"]["sub"] = {"submission_id": "SUB-1005", **json.loads(json.dumps(GREENLEAF))}
SUBMISSIONS["05b_wrong_document_resubmitted"]["sub"] = {
    "submission_id": "SUB-1005", "version": 2, "resubmission_of": "SUB-1005",
    **json.loads(json.dumps(GREENLEAF))}


# ==========================================================================
# 3. Specimen document rendering
# ==========================================================================

STATE_NAMES = {"07": "Delhi", "20": "Jharkhand", "27": "Maharashtra", "29": "Karnataka"}
ENTITY_LABELS = {"private_limited": "Private Limited Company", "llp": "Limited Liability Partnership",
                 "sole_proprietorship": "Proprietorship"}


def _watermark(c, w, h):
    c.saveState()
    c.setFillColor(colors.Color(0.85, 0.1, 0.1, alpha=0.12))
    c.setFont("Helvetica-Bold", 64)
    c.translate(w / 2, h / 2)
    c.rotate(35)
    c.drawCentredString(0, 0, "SPECIMEN")
    c.restoreState()
    c.setFont("Helvetica", 7)
    c.setFillColor(colors.grey)
    c.drawCentredString(w / 2, 14, "Fictitious specimen generated for software testing only. Not a real document.")


def _kv(c, x, y, label, value, lw=170):
    c.setFont("Helvetica", 10); c.setFillColor(colors.HexColor("#555555"))
    c.drawString(x, y, label)
    c.setFont("Helvetica-Bold", 10); c.setFillColor(colors.black)
    c.drawString(x + lw, y, value)


def render_gst_certificate(sub) -> bytes:
    buf = io.BytesIO(); w, h = A4
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica-Bold", 15)
    c.drawCentredString(w / 2, h - 70, "GOODS AND SERVICES TAX - REGISTRATION CERTIFICATE")
    c.setFont("Helvetica", 10)
    c.drawCentredString(w / 2, h - 88, "Form GST REG-06  (Specimen layout)")
    c.line(50, h - 100, w - 50, h - 100)

    co, tax = sub["company"], sub["tax"]
    is_prop = co["entity_type"] == "sole_proprietorship"
    legal = sub.get("proprietor_name") if is_prop else co["vendor_name"]
    trade = co["vendor_name"]
    reg_addr = sub.get("gst_registered_address", co["address"])
    state_code = tax["gstin"][:2]

    y = h - 135
    rows = [
        ("Registration Number (GSTIN)", tax["gstin"]),
        ("1. Legal Name", legal.upper()),
        ("2. Trade Name, if any", trade.upper()),
        ("3. Constitution of Business", ENTITY_LABELS[co["entity_type"]]),
        ("4. Address of Principal Place", reg_addr["line1"]),
        ("", f'{reg_addr["city"]}, {reg_addr["state"]} - {reg_addr["pincode"]}'),
        ("5. State Jurisdiction", f'{STATE_NAMES.get(state_code, "")} ({state_code})'),
        ("6. Date of Liability", sub.get("incorporated_on", "2017-07-01")),
        ("7. Period of Validity", "From " + sub.get("incorporated_on", "2017-07-01") + "  To  Not Applicable"),
        ("8. Type of Registration", "Regular"),
    ]
    for label, val in rows:
        _kv(c, 60, y, label, val, lw=200); y -= 24

    if is_prop:
        y -= 10
        c.setFont("Helvetica-Bold", 11); c.drawString(60, y, "Annexure B - Details of Proprietor"); y -= 22
        _kv(c, 60, y, "Name", legal.upper(), lw=200); y -= 20
        _kv(c, 60, y, "Designation", "Proprietor", lw=200)

    c.setFont("Helvetica-Oblique", 9)
    c.drawString(60, 120, "This is a system generated digitally signed Registration Certificate (specimen).")
    _watermark(c, w, h)
    c.showPage(); c.save()
    return buf.getvalue()


def render_incorporation(sub) -> bytes:
    buf = io.BytesIO(); w, h = A4
    c = canvas.Canvas(buf, pagesize=A4)
    co = sub["company"]
    is_llp = co["entity_type"] == "llp"
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(w / 2, h - 90, "CERTIFICATE OF INCORPORATION")
    c.setFont("Helvetica", 10)
    c.drawCentredString(w / 2, h - 108, "(Specimen layout - Registrar of Companies)")
    c.line(70, h - 120, w - 70, h - 120)

    c.setFont("Helvetica", 11)
    text = c.beginText(70, h - 160); text.setLeading(20)
    kind = "limited liability partnership" if is_llp else "company limited by shares"
    text.textLine("I hereby certify that")
    text.setFont("Helvetica-Bold", 13); text.textLine(co["vendor_name"].upper()); text.setFont("Helvetica", 11)
    text.textLine(f"is incorporated on this day {sub['incorporated_on']} as a {kind}.")
    c.drawText(text)

    y = h - 270
    _kv(c, 70, y, "LLPIN" if is_llp else "Corporate Identity Number (CIN)", sub["cin"], lw=210); y -= 24
    _kv(c, 70, y, "Permanent Account Number (PAN)", sub["tax"]["pan"], lw=210); y -= 24
    _kv(c, 70, y, "Registered office state", co["address"]["state"] if not sub.get("gst_registered_address")
        else sub["gst_registered_address"]["state"], lw=210)
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(70, 140, "Issued by the Registrar of Companies (specimen). Digitally signed.")
    _watermark(c, w, h)
    c.showPage(); c.save()
    return buf.getvalue()


def _font(size, bold=False):
    candidates = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
                  else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    for p in candidates:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def render_pan_card(sub) -> Image.Image:
    co = sub["company"]
    is_prop = co["entity_type"] == "sole_proprietorship"
    name = sub.get("proprietor_name") if is_prop else co["vendor_name"]
    img = Image.new("RGB", (1012, 638), (214, 230, 240))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 1012, 90], fill=(40, 70, 120))
    d.text((30, 25), "INCOME TAX DEPARTMENT  (SPECIMEN)", fill="white", font=_font(32, True))
    d.text((40, 130), "Permanent Account Number Card", fill=(30, 30, 30), font=_font(26))
    d.text((40, 185), sub["tax"]["pan"], fill=(0, 0, 0), font=_font(48, True))
    d.text((40, 280), "Name", fill=(90, 90, 90), font=_font(20))
    d.text((40, 310), name.upper(), fill=(0, 0, 0), font=_font(30, True))
    label = "Date of Birth" if is_prop else "Date of Incorporation / Formation"
    d.text((40, 380), label, fill=(90, 90, 90), font=_font(20))
    d.text((40, 410), sub.get("incorporated_on", "1984-05-17"), fill=(0, 0, 0), font=_font(28, True))
    d.rectangle([780, 140, 960, 360], outline=(90, 90, 90), width=3)
    d.text((800, 240), "PHOTO" if is_prop else "SEAL", fill=(120, 120, 120), font=_font(26))
    d.text((40, 560), "Fictitious specimen for software testing only", fill=(160, 40, 40), font=_font(20))
    return img


def render_cheque(sub) -> Image.Image:
    b = sub["bank"]
    img = Image.new("RGB", (1400, 640), (238, 244, 236))
    d = ImageDraw.Draw(img)
    d.text((40, 30), b["bank_name"].upper(), fill=(20, 60, 110), font=_font(40, True))
    d.text((40, 85), f"IFSC: {b['ifsc']}", fill=(20, 20, 20), font=_font(26))
    d.text((1100, 40), "Date ________", fill=(60, 60, 60), font=_font(24))
    d.text((40, 170), "Pay ___________________________________________ or Bearer", fill=(60, 60, 60), font=_font(26))
    d.text((40, 240), "Rupees ________________________________________", fill=(60, 60, 60), font=_font(26))
    d.rectangle([1050, 230, 1350, 290], outline=(60, 60, 60), width=2)
    d.text((40, 330), f"A/c No. {b['account_number']}", fill=(0, 0, 0), font=_font(32, True))
    d.text((40, 390), f"A/c Holder: {b['account_holder_name'].upper()}", fill=(0, 0, 0), font=_font(28, True))
    d.text((40, 560), "SPECIMEN  000123  " + b["ifsc"] + "  " + b["account_number"],
           fill=(40, 40, 40), font=_font(26))
    # CANCELLED stamp
    stamp = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(stamp)
    sd.line([(150, 520), (1250, 120)], fill=(30, 30, 30, 200), width=5)
    sd.line([(150, 560), (1250, 160)], fill=(30, 30, 30, 200), width=5)
    sd.text((520, 300), "CANCELLED", fill=(180, 20, 20, 230), font=_font(80, True))
    img = Image.alpha_composite(img.convert("RGBA"), stamp).convert("RGB")
    return img


def photo_effect(img: Image.Image) -> Image.Image:
    """Make a clean render look like a phone photo: tilt, background, blur, noise."""
    angle = random.uniform(-4.5, 4.5)
    pad = 80
    bg = Image.new("RGB", (img.width + pad * 2, img.height + pad * 2), (96, 84, 72))
    bg.paste(img, (pad, pad))
    bg = bg.rotate(angle, expand=False, fillcolor=(96, 84, 72), resample=Image.BICUBIC)
    bg = bg.filter(ImageFilter.GaussianBlur(0.8))
    noise = Image.effect_noise(bg.size, 18).convert("RGB")
    bg = Image.blend(bg, noise, 0.06)
    return bg


def pdf_to_scan(pdf_bytes: bytes) -> bytes:
    """Rasterise a PDF and re-wrap it as an image-only PDF (no text layer), like a scanner would."""
    page = pdfium.PdfDocument(pdf_bytes)[0]
    img = page.render(scale=1.6).to_pil().convert("L")
    img = img.rotate(random.uniform(-1.5, 1.5), fillcolor=255, resample=Image.BICUBIC)
    img = img.filter(ImageFilter.GaussianBlur(0.5))
    out = io.BytesIO()
    img.convert("RGB").save(out, format="PDF", resolution=150)
    return out.getvalue()


def write_doc(slot: str, style: str, sub: dict, docs_dir: Path) -> str:
    """Render one document. style: pdf | scan | photo | WRONG:<other_slot>"""
    if style.startswith("WRONG:"):
        real = style.split(":", 1)[1]
        assert real == "pan_card"
        path = docs_dir / f"{slot}.jpg"   # file named as if it were the GST cert
        photo_effect(render_pan_card(sub)).save(path, quality=82)
        return path.name

    if slot == "gst_certificate":
        pdf = render_gst_certificate(sub)
        if style == "scan":
            pdf = pdf_to_scan(pdf)
        (docs_dir / f"{slot}.pdf").write_bytes(pdf)
        return f"{slot}.pdf"
    if slot == "certificate_of_incorporation":
        (docs_dir / f"{slot}.pdf").write_bytes(render_incorporation(sub))
        return f"{slot}.pdf"
    if slot == "pan_card":
        photo_effect(render_pan_card(sub)).save(docs_dir / f"{slot}.jpg", quality=82)
        return f"{slot}.jpg"
    if slot == "cancelled_cheque":
        photo_effect(render_cheque(sub)).save(docs_dir / f"{slot}.jpg", quality=82)
        return f"{slot}.jpg"
    raise ValueError(slot)


# ==========================================================================
# 4. Build everything
# ==========================================================================

def main():
    DATA.mkdir(exist_ok=True)
    (DATA / "vendors_master.json").write_text(json.dumps(VENDOR_MASTER, indent=2))

    if CASES.exists():
        shutil.rmtree(CASES)
    CASES.mkdir()

    manifest = []
    for case_id, spec in SUBMISSIONS.items():
        case_dir = CASES / case_id
        docs_dir = case_dir / "docs"
        docs_dir.mkdir(parents=True)
        sub = spec["sub"]
        sub["documents"] = {slot: f"docs/{write_doc(slot, style, sub, docs_dir)}"
                            for slot, style in spec["docs"].items()}
        (case_dir / "submission.json").write_text(json.dumps(sub, indent=2))
        manifest.append({"case": case_id, "submission_id": sub["submission_id"],
                         "vendor": sub["company"]["vendor_name"],
                         "expected_outcome": spec["expected"], "why": spec["why"]})

    (CASES / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"Vendor master: {len(VENDOR_MASTER)} vendors")
    for m in manifest:
        print(f"  {m['case']:<34} {m['expected_outcome']:<9} {m['vendor']}")


if __name__ == "__main__":
    main()
