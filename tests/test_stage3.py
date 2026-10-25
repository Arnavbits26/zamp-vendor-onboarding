import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from app.completeness import check_completeness
from app.documents import (UnreadableFile, normalise_extraction, read_documents,
                           render_for_vision, triage, verify_document)
from app.events import RunLog
from app.intake import load_submission
from app.llm import ClaudeReader
from app.validators import FAIL, PASS, SKIP, WARN

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "test_cases"


def st(results):
    return {r.rule_id: r.status for r in results}


# ------------------------------------------------------------ completeness
def test_happy_path_is_complete():
    sub = load_submission(CASES / "01_happy_path")
    assert st(check_completeness(sub)) == {"CMP-01": PASS, "CMP-02": PASS, "CMP-03": PASS}


def test_sole_proprietor_does_not_need_incorporation_certificate():
    sub = load_submission(CASES / "02_trade_name_vs_legal_name")
    assert "certificate_of_incorporation" not in sub["_doc_paths"]
    assert st(check_completeness(sub))["CMP-03"] == PASS


def test_company_missing_incorporation_certificate_is_flagged():
    sub = load_submission(CASES / "01_happy_path")
    del sub["_doc_paths"]["certificate_of_incorporation"]
    r = {x.rule_id: x for x in check_completeness(sub)}
    assert r["CMP-03"].status == FAIL
    assert "Certificate of incorporation" in r["CMP-03"].detail


def test_missing_and_blank_fields_are_listed():
    sub = load_submission(CASES / "01_happy_path")
    sub["bank"]["ifsc"] = "  "
    del sub["company"]["contact"]["phone"]
    r = {x.rule_id: x for x in check_completeness(sub)}
    assert r["CMP-02"].status == FAIL
    assert set(r["CMP-02"].evidence["missing"]) == {"IFSC code", "Contact phone"}


def test_file_listed_but_not_on_disk_counts_as_missing():
    sub = load_submission(CASES / "01_happy_path")
    sub["_doc_paths"]["pan_card"] = str(CASES / "nope.jpg")
    assert st(check_completeness(sub))["CMP-03"] == FAIL


def test_unknown_entity_type_stops_early():
    sub = load_submission(CASES / "01_happy_path")
    sub["company"]["entity_type"] = "spaceship"
    assert st(check_completeness(sub)) == {"CMP-01": FAIL}


# ------------------------------------------------------------------ triage
def test_text_pdf_routes_to_text():
    t = triage(CASES / "01_happy_path/docs/gst_certificate.pdf")
    assert t["route"] == "text" and t["chars"] > 500 and "GSTIN" in t["text"]


def test_scanned_pdf_routes_to_vision():
    t = triage(CASES / "02_trade_name_vs_legal_name/docs/gst_certificate.pdf")
    assert t["route"] == "vision" and t["chars"] == 0


def test_photos_route_to_vision():
    assert triage(CASES / "01_happy_path/docs/pan_card.jpg")["route"] == "vision"


def test_corrupt_and_unsupported_files(tmp_path):
    bad = tmp_path / "x.pdf"; bad.write_bytes(b"not a pdf")
    with pytest.raises(UnreadableFile):
        triage(bad)
    doc = tmp_path / "x.docx"; doc.write_bytes(b"x")
    with pytest.raises(UnreadableFile, match="Unsupported"):
        triage(doc)


def test_render_for_vision_caps_size_and_returns_jpeg():
    for rel in ["01_happy_path/docs/gst_certificate.pdf", "01_happy_path/docs/cancelled_cheque.jpg",
                "02_trade_name_vs_legal_name/docs/gst_certificate.pdf"]:
        data, mt = render_for_vision(CASES / rel)
        img = Image.open(io.BytesIO(data))
        assert mt == "image/jpeg" and img.format == "JPEG" and max(img.size) <= 1600


# ----------------------------------------------------------- normalisation
def test_normalisation_cleans_identifiers_but_not_meaning():
    ex = {"fields": {"gstin": " 29aakck4821m1z0 ", "pan": "aakck 4821 m", "ifsc": "hdfc 0004521",
                     "account_number": "5020-0078 4532 19", "legal_name": "  Kavya   Packaging  ",
                     "trade_name": "", "account_holder_name": None}}
    f = normalise_extraction(ex)["fields"]
    assert f["gstin"] == "29AAKCK4821M1Z0" and f["pan"] == "AAKCK4821M"
    assert f["ifsc"] == "HDFC0004521" and f["account_number"] == "50200078453219"
    assert f["legal_name"] == "Kavya Packaging" and f["trade_name"] is None


# ------------------------------------------------------------ verification
def ex(doc_type, legibility="good", **fields):
    return {"doc_type": doc_type, "classification_confidence": "high",
            "legibility": legibility, "fields": fields, "notes": ""}


def test_wrong_document_fails_doc01_and_skips_doc02():
    r = verify_document("gst_certificate", ex("pan_card", pan="AAJCG5508H", legal_name="X"))
    assert st(r) == {"DOC-01": FAIL, "DOC-02": SKIP}
    assert r[0].detail == "The file uploaded as 'GST registration certificate' appears to be a PAN card."


def test_unidentifiable_document_message():
    r = verify_document("pan_card", ex("other"))
    assert r[0].status == FAIL and "could not identify" in r[0].detail


def test_correct_document_with_all_fields_passes():
    r = verify_document("pan_card", ex("pan_card", pan="AAJCG5508H", legal_name="GREENLEAF"))
    assert st(r) == {"DOC-01": PASS, "DOC-02": PASS}


def test_unreadable_field_fails_doc02_and_names_it():
    r = verify_document("cancelled_cheque", ex("cancelled_cheque", account_number="123456789",
                                               ifsc=None, account_holder_name="A"))
    assert r[1].status == FAIL and "IFSC" in r[1].detail


def test_poor_legibility_fails_and_partial_warns():
    full = dict(gstin="29AAKCK4821M1Z0", legal_name="K")
    assert verify_document("gst_certificate", ex("gst_certificate", "poor", **full))[1].status == FAIL
    assert verify_document("gst_certificate", ex("gst_certificate", "partial", **full))[1].status == WARN


# ------------------------------------------------- orchestration (fake model)
class FakeReader:
    """Returns canned extractions in the order the pipeline reads documents."""
    def __init__(self, queue):
        self.queue, self.calls = list(queue), []

    def read(self, **kw):
        self.calls.append(kw["route"])
        return self.queue.pop(0)


def test_read_documents_routes_and_collects_rules():
    sub = load_submission(CASES / "02_trade_name_vs_legal_name")
    reader = FakeReader([
        ex("gst_certificate", gstin="20BQRPS7712D1ZT", legal_name="RAJESH SHARMA", trade_name="SHARMA ELECTRICALS"),
        ex("pan_card", pan="BQRPS7712D", legal_name="RAJESH SHARMA"),
        ex("cancelled_cheque", account_number="33872910456", ifsc="SBIN0000167",
           account_holder_name="RAJESH SHARMA"),
    ])
    log = RunLog()
    out = read_documents(sub, reader, log)
    assert list(out) == ["gst_certificate", "pan_card", "cancelled_cheque"]
    assert reader.calls == ["vision", "vision", "vision"]            # scan + two photos
    assert all(r.status == PASS for e in out.values() for r in e["rules"])
    assert any("no text layer" in e.message for e in log.events)


def test_text_route_used_for_text_pdf_and_wrong_doc_is_reported():
    sub = load_submission(CASES / "05a_wrong_document")
    reader = FakeReader([
        ex("pan_card", pan="AAJCG5508H", legal_name="GREENLEAF"),        # in the GST slot
        ex("pan_card", pan="AAJCG5508H", legal_name="GREENLEAF"),
        ex("cancelled_cheque", account_number="1834002100056712", ifsc="PUNB0183400", account_holder_name="G"),
        ex("certificate_of_incorporation", cin_or_llpin="U74999MH2018PTC312090", legal_name="G"),
    ])
    out = read_documents(sub, reader, RunLog())
    assert out["gst_certificate"]["rules"][0].status == FAIL
    assert out["pan_card"]["rules"][0].status == PASS
    assert reader.calls == ["vision", "vision", "vision", "text"]


def test_corrupt_upload_becomes_a_rule_failure_not_a_crash(tmp_path):
    bad = tmp_path / "gst.pdf"; bad.write_bytes(b"garbage")
    sub = {"_doc_paths": {"gst_certificate": str(bad)}}
    out = read_documents(sub, FakeReader([]), RunLog())
    assert out["gst_certificate"]["rules"][0].rule_id == "DOC-03"
    assert out["gst_certificate"]["rules"][0].status == FAIL


# ----------------------------------------- request shape sent to the API
class RecordingClient:
    def __init__(self):
        self.kwargs = None
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.kwargs = kw
        block = SimpleNamespace(type="tool_use", input={"doc_type": "other", "fields": {}})
        return SimpleNamespace(content=[block])


def test_vision_request_shape_and_no_leakage_of_slot_or_filename():
    c = RecordingClient()
    ClaudeReader(client=c, model="m").read(route="vision", image=b"\xff\xd8abc")
    kw = c.kwargs
    assert kw["tool_choice"] == {"type": "tool", "name": "record_document"}
    assert kw["tools"][0]["name"] == "record_document"
    blocks = kw["messages"][0]["content"]
    assert blocks[0]["type"] == "image" and blocks[0]["source"]["media_type"] == "image/jpeg"
    blob = json.dumps(kw, default=str).lower()
    assert "gst_certificate.jpg" not in blob and "slot" not in blob


def test_text_request_shape():
    c = RecordingClient()
    ClaudeReader(client=c, model="m").read(route="text", text="GSTIN 29AAKCK4821M1Z0")
    first = c.kwargs["messages"][0]["content"][0]
    assert first["type"] == "text" and "<document_text>" in first["text"]


def test_missing_api_key_gives_clear_error(monkeypatch):
    from app.llm import ExtractionError
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ExtractionError, match="ANTHROPIC_API_KEY"):
        ClaudeReader().read(route="text", text="x")
