import json
from types import SimpleNamespace

import pytest

import app.llm as llm
from app.llm import (CachedReader, ExtractionError, GeminiReader, _parse_json,
                     coerce_extraction)

GOOD = {"doc_type": "pan_card", "classification_confidence": "high", "legibility": "good",
        "fields": {"pan": "AAJCG5508H", "legal_name": "GREENLEAF"}, "notes": ""}


class FakeGemini:
    """Mimics client.models.generate_content; replies are consumed in order."""
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.models = SimpleNamespace(generate_content=self._gen)

    def _gen(self, **kw):
        self.calls.append(kw)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return SimpleNamespace(text=r if isinstance(r, str) else json.dumps(r))


class RateLimit(Exception):
    code = 429
    def __str__(self): return "429 RESOURCE_EXHAUSTED"


def reader(replies, sleeps=None):
    return GeminiReader(client=FakeGemini(replies), model="test-model", fallback_model=None,
                        sleep=(sleeps.append if sleeps is not None else lambda s: None))


# ------------------------------------------------------------ request shape
def test_vision_request_has_image_part_json_mode_and_no_slot_leak():
    r = reader([GOOD])
    r.read(route="vision", image=b"\xff\xd8fake")
    kw = r.client.calls[0]
    assert kw["model"] == "test-model"
    assert kw["config"].response_mime_type == "application/json"
    assert kw["config"].temperature == 0
    assert "untrusted" in kw["config"].system_instruction
    first = kw["contents"][0]
    assert first.inline_data.mime_type == "image/jpeg" and first.inline_data.data == b"\xff\xd8fake"
    blob = json.dumps([str(c) for c in kw["contents"][1:]]).lower()
    assert "gst_certificate.jpg" not in blob and "submission" not in blob


def test_text_request_wraps_document_text():
    r = reader([GOOD])
    r.read(route="text", text="GSTIN 29AAKCK4821M1Z0")
    assert r.client.calls[0]["contents"][0].startswith("<document_text>")


def test_vision_without_image_is_an_error():
    with pytest.raises(ExtractionError):
        reader([GOOD]).read(route="vision")


# ------------------------------------------------------------ reply parsing
def test_fenced_and_chatty_json_is_parsed():
    assert _parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _parse_json('Sure! Here it is: {"a": 1} hope that helps') == {"a": 1}


def test_invalid_json_gives_clear_error():
    with pytest.raises(ExtractionError, match="not valid JSON"):
        reader(["I cannot read this"]).read(route="text", text="x")


def test_coerce_forces_exact_shape():
    out = coerce_extraction({"doc_type": "passport", "legibility": "blurry",
                             "fields": {"account_number": 123456789012, "pan": "  ", "gstin": None,
                                        "marked_cancelled": "yes"}})
    assert out["doc_type"] == "other"                       # unknown type -> other
    assert out["legibility"] == "partial"                   # junk -> cautious default
    assert out["classification_confidence"] == "low"
    f = out["fields"]
    assert f["account_number"] == "123456789012"            # int -> str
    assert f["pan"] is None and f["gstin"] is None          # blank -> null
    assert f["marked_cancelled"] is None                    # non-bool -> null
    assert set(f) >= set(llm.FIELD_KEYS)                    # every key present


def test_non_object_reply_rejected():
    with pytest.raises(ExtractionError):
        coerce_extraction(["not", "a", "dict"])


# ------------------------------------------------------------- rate limits
def test_retries_on_429_then_succeeds():
    sleeps = []
    r = reader([RateLimit(), RateLimit(), GOOD], sleeps)
    out = r.read(route="text", text="x")
    assert out["doc_type"] == "pan_card" and sleeps == [4, 12]
    assert len(r.client.calls) == 3


def test_gives_up_with_friendly_message_after_retries():
    sleeps = []
    r = reader([RateLimit()] * 5, sleeps)
    with pytest.raises(ExtractionError, match="quota"):
        r.read(route="text", text="x")
    assert sleeps == [4, 12, 30, 60]


def test_unknown_model_points_to_list_models():
    class NF(Exception):
        code = 404
    with pytest.raises(ExtractionError, match="list_models"):
        reader([NF("404 NOT_FOUND")]).read(route="text", text="x")


def test_other_errors_are_wrapped_not_retried():
    r = reader([ValueError("boom")])
    with pytest.raises(ExtractionError, match="ValueError"):
        r.read(route="text", text="x")
    assert len(r.client.calls) == 1


def test_missing_key_message(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(ExtractionError, match="aistudio.google.com"):
        GeminiReader().read(route="text", text="x")


# ------------------------------------------------------------------- cache
def test_cache_sends_each_input_once(tmp_path):
    inner = reader([GOOD, {**GOOD, "doc_type": "other"}])
    c = CachedReader(inner, cache_dir=tmp_path)
    a = c.read(route="vision", image=b"img1")
    b = c.read(route="vision", image=b"img1")      # same bytes -> cached
    d = c.read(route="vision", image=b"img2")      # different bytes -> new call
    assert a == b and a["doc_type"] == "pan_card" and d["doc_type"] == "other"
    assert len(inner.client.calls) == 2 and c.hits == 1


def test_cache_key_includes_model(tmp_path):
    a = CachedReader(reader([GOOD]), cache_dir=tmp_path)
    a.read(route="text", text="same")
    other = GeminiReader(client=FakeGemini([GOOD]), model="other-model", fallback_model=None)
    b = CachedReader(other, cache_dir=tmp_path)
    b.read(route="text", text="same")
    assert len(other.client.calls) == 1             # different model -> not a cache hit


# ----------------------------------------------------------------- factory
def test_factory_selects_provider(monkeypatch):
    monkeypatch.setattr(llm, "EXTRACTION_CACHE", False)
    monkeypatch.setattr(llm, "LLM_PROVIDER", "gemini")
    assert isinstance(llm.get_reader(), GeminiReader)
    monkeypatch.setattr(llm, "LLM_PROVIDER", "anthropic")
    assert isinstance(llm.get_reader(), llm.ClaudeReader)
    monkeypatch.setattr(llm, "LLM_PROVIDER", "nope")
    with pytest.raises(ExtractionError, match="LLM_PROVIDER"):
        llm.get_reader()


# ------------------------------------------------- 503 overload + fallback model
class Overloaded(Exception):
    code = 503
    def __str__(self): return "503 UNAVAILABLE. This model is currently experiencing high demand."


def test_503_is_retried_then_succeeds():
    sleeps = []
    r = reader([Overloaded(), Overloaded(), GOOD], sleeps)
    assert r.read(route="text", text="x")["doc_type"] == "pan_card"
    assert sleeps == [4, 12]


def test_persistent_503_gives_overload_message_not_quota_message():
    r = reader([Overloaded()] * 5)
    with pytest.raises(ExtractionError, match="overloaded"):
        r.read(route="text", text="x")


def test_fallback_model_used_when_primary_stays_overloaded():
    client = FakeGemini([Overloaded()] * 5 + [GOOD])
    r = GeminiReader(client=client, model="primary", fallback_model="backup", sleep=lambda s: None)
    assert r.read(route="text", text="x")["doc_type"] == "pan_card"
    assert [c["model"] for c in client.calls] == ["primary"] * 5 + ["backup"]


def test_fallback_not_used_for_non_transient_errors():
    client = FakeGemini([ValueError("boom"), GOOD])
    r = GeminiReader(client=client, model="primary", fallback_model="backup", sleep=lambda s: None)
    with pytest.raises(ExtractionError, match="ValueError"):
        r.read(route="text", text="x")
    assert len(client.calls) == 1


def test_both_models_down_reports_overload():
    client = FakeGemini([Overloaded()] * 10)
    r = GeminiReader(client=client, model="a", fallback_model="b", sleep=lambda s: None)
    with pytest.raises(ExtractionError, match="overloaded"):
        r.read(route="text", text="x")
    assert len(client.calls) == 10


def test_progress_messages_are_reported_for_retries_and_backup():
    msgs = []
    client = FakeGemini([Overloaded()] * 5 + [GOOD])
    r = GeminiReader(client=client, model="primary", fallback_model="backup", sleep=lambda s: None)
    r.notify = msgs.append
    r.read(route="text", text="x")
    assert msgs[0] == "primary: servers busy (503) - retrying in 4s (try 1/4)"
    assert any("switching to backup model backup" in m for m in msgs)
    assert msgs[-1] == "read OK using backup model backup"
