import io
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

import app.server as server
from app.llm import ExtractionError
from app.store import Store
from tests.support import CASES, OracleReader


class Proxy:
    """One reader for the whole server; each test says which 'perfect model' to use for the next run."""
    def __init__(self):
        self.current = None
        self.notify = lambda m: None

    def load(self, case=None, **kw):
        self.current = OracleReader(case or "x", **kw)
        return self.current

    def read(self, **kw):
        return self.current.read(**kw)

    def draft(self, s, p):
        return self.current.draft(s, p)


@pytest.fixture
def env(tmp_path):
    proxy, store = Proxy(), Store(":memory:")
    app = server.create_app(reader=proxy, store=store, cases_dir=CASES, uploads_dir=tmp_path / "uploads")
    return TestClient(app), proxy, store, tmp_path


def sse(client, source):
    """Run and collect [(event, data)] from the stream."""
    out, ev = [], None
    with client.stream("POST", "/api/run", json={"source": source}) as r:
        assert r.status_code == 200, r.read()
        for line in r.iter_lines():
            if line.startswith("event:"):
                ev = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                out.append((ev, json.loads(line.split(":", 1)[1])))
    return out


def upload(client, form, base=None, files=None):
    data = {"submission": json.dumps(form), "base_source": base or ""}
    return client.post("/api/uploads", data=data, files=files or {})


# ------------------------------------------------------------ basics
def test_config_cases_and_page(env):
    c, *_ = env
    cfg = c.get("/api/config").json()
    assert cfg["ready"] is True and "key" not in json.dumps(cfg).lower()
    cases = c.get("/api/cases").json()
    assert len(cases) == 6 and cases[0]["source"] == "case:01_happy_path"
    assert {x["designed_outcome"] for x in cases} == {"APPROVED", "REJECTED", "PENDING"}
    assert c.get("/health").json() == {"ok": True}
    assert c.get("/").status_code == 200


def test_form_and_document_preview(env):
    c, *_ = env
    f = c.get("/api/source/form", params={"source": "case:02_trade_name_vs_legal_name"}).json()
    assert f["form"]["company"]["vendor_name"] == "Sharma Electricals"
    assert [d["slot"] for d in f["documents"]] == ["gst_certificate", "pan_card", "cancelled_cheque"]
    r = c.get("/api/source/doc", params={"source": "case:01_happy_path", "slot": "pan_card"})
    assert r.status_code == 200 and r.content[:3] == b"\xff\xd8\xff"


@pytest.mark.parametrize("source", ["case:../etc", "case:nope", "upload:../../x", "upload:" + "0" * 32,
                                    "file:/etc/passwd", "", "case:"])
def test_unknown_or_malicious_sources_are_refused(env, source):
    c, *_ = env
    assert c.get("/api/source/doc", params={"source": source, "slot": "pan_card"}).status_code == 404
    assert c.get("/api/source/form", params={"source": source}).status_code == 404
    assert c.post("/api/run", json={"source": source}).status_code == 404


@pytest.mark.parametrize("slot", ["../../etc/passwd", "certificate_of_incorporation_x", "", "pan_card/../../x"])
def test_unknown_document_slots_are_refused(env, slot):
    c, *_ = env
    assert c.get("/api/source/doc", params={"source": "case:01_happy_path", "slot": slot}).status_code == 404


def test_loading_a_case_into_the_form_does_not_carry_its_submission_id(env):
    c, *_ = env
    f = c.get("/api/source/form", params={"source": "case:01_happy_path"}).json()
    assert f["submission_id"] == "SUB-1001" and "submission_id" not in f["form"]


# ------------------------------------------------------------ streaming runs
def test_run_streams_every_stage_then_the_result_and_saves_history(env):
    c, proxy, store, _ = env
    proxy.load("04_gst_state_mismatch")
    out = sse(c, "case:04_gst_state_mismatch")
    kinds = [k for k, _ in out]
    assert kinds[0] == "start" and kinds[-1] == "result" and "error" not in kinds
    stages = [d["stage"] for k, d in out if k == "event"]
    for s in ("intake", "completeness", "format", "documents", "crossref", "risk", "decision", "messages"):
        assert s in stages
    assert stages.index("intake") < stages.index("documents") < stages.index("crossref") < stages.index("decision")
    result = out[-1][1]
    assert result["status"] == "PENDING" and "events" not in result and result["source"] == "case:04_gst_state_mismatch"
    rows = c.get("/api/runs").json()
    assert rows[0]["status"] == "PENDING"
    full = c.get(f"/api/runs/{rows[0]['run_id']}").json()
    assert full["events"] and full["rules"] and full["messages"]["vendor"]["subject"]
    assert c.get("/api/runs/999").status_code == 404


def test_model_failure_is_reported_as_an_error_event_not_a_hang(env):
    c, proxy, *_ = env

    class Down:
        def read(self, **kw): raise ExtractionError("Google's servers are overloaded right now (503).")
        def draft(self, s, p): raise ExtractionError("x")
    proxy.current = Down()
    out = sse(c, "case:01_happy_path")
    assert out[-1][0] == "error" and "overloaded" in out[-1][1]["message"]
    assert c.get("/api/runs").json() == []                      # nothing half-saved


def test_unexpected_exception_is_also_reported(env):
    c, proxy, *_ = env

    class Bug:
        def read(self, **kw): raise KeyError("oops")
        def draft(self, s, p): return ""
    proxy.current = Bug()
    out = sse(c, "case:01_happy_path")
    assert out[-1][0] == "error" and "KeyError" in out[-1][1]["message"]


def test_model_retry_notices_appear_in_the_stream(env):
    c, proxy, *_ = env

    class Chatty(OracleReader):
        def read(self, **kw):
            server._tls.emit("gemini-x: servers busy (503) - retrying in 4s (try 1/4)")
            return super().read(**kw)
    proxy.current = Chatty("01_happy_path")
    out = sse(c, "case:01_happy_path")
    notes = [d for k, d in out if k == "event" and d["stage"] == "model"]
    assert notes and "retrying in 4s" in notes[0]["message"] and notes[0]["status"] == "warn"


# ------------------------------------------------------------ uploads
def form_of(c, source):
    return c.get("/api/source/form", params={"source": source}).json()["form"]


def test_upload_from_a_base_case_then_run(env):
    c, proxy, *_ = env
    form = form_of(c, "case:01_happy_path")
    r = upload(c, form, base="case:01_happy_path")
    assert r.status_code == 200
    j = r.json()
    assert j["source"].startswith("upload:") and j["version"] == 1 and j["submission_id"].startswith("SUB-W")
    proxy.load(case_dir=server_dir(env, j["source"]))
    assert sse(c, j["source"])[-1][1]["status"] == "APPROVED"


def server_dir(env, source):
    return env[3] / "uploads" / source.split(":")[1]


def test_blank_form_with_no_files_does_not_crash_and_lists_what_is_missing(env):
    c, proxy, *_ = env
    j = upload(c, {}).json()
    proxy.load(case_dir=server_dir(env, j["source"]))
    res = sse(c, j["source"])[-1][1]
    assert res["status"] == "PENDING"
    assert any(r["rule_id"] == "CMP-01" and r["status"] == "FAIL" for r in res["rules"])


def test_edited_field_changes_the_outcome(env):
    c, proxy, *_ = env
    form = form_of(c, "case:01_happy_path")
    form["bank"].update(account_number="045601503321", ifsc="ICIC0000456")        # Orbit Office Solutions' account
    j = upload(c, form, base="case:01_happy_path").json()
    # a perfect model reads the cheque from the (unchanged) base file: it still shows Kavya's account
    proxy.load(case_dir=server_dir(env, j["source"]),
               tweak={"cancelled_cheque": lambda e: e["fields"].update(account_number="045601503321", ifsc="ICIC0000456")})
    res = sse(c, j["source"])[-1][1]
    assert res["status"] == "REJECTED"
    assert any(r["rule_id"] == "RISK-01" and r["status"] == "FAIL" for r in res["rules"])


def test_resubmission_through_the_api_bumps_version_and_reuses_files(env):
    c, proxy, store, _ = env
    form = form_of(c, "case:05a_wrong_document")
    form["submission_id"] = "SUB-T1"
    v1 = upload(c, form, base="case:05a_wrong_document").json()
    assert v1["version"] == 1
    proxy.load(case_dir=server_dir(env, v1["source"]), wrong={"gst_certificate": "pan_card"})
    assert sse(c, v1["source"])[-1][1]["status"] == "PENDING"

    good = (CASES / "05b_wrong_document_resubmitted" / "docs" / "gst_certificate.pdf").read_bytes()
    v2 = upload(c, form, base=v1["source"],
                files={"gst_certificate": ("whatever.pdf", io.BytesIO(good), "application/pdf")}).json()
    assert v2["version"] == 2 and v2["submission_id"] == "SUB-T1"
    reader = proxy.load(case_dir=server_dir(env, v2["source"]))
    res = sse(c, v2["source"])[-1][1]
    assert res["status"] == "APPROVED" and res["version"] == 2
    assert res["changes"]["documents_changed"] == ["gst_certificate"]
    assert reader.reads == 1                                        # only the replaced file went to the model
    versions = c.get("/api/submissions/SUB-T1").json()
    assert [v["version"] for v in versions] == [1, 2] and versions[1]["changes"]["status_after"] == "APPROVED"
    assert c.get("/api/submissions/SUB-NOPE").status_code == 404


def test_upload_rejects_files_that_are_not_documents(env):
    c, *_ = env
    r = upload(c, {}, files={"pan_card": ("x.jpg", io.BytesIO(b"MZ\x90\x00 this is an exe"), "image/jpeg")})
    assert r.status_code == 400 and "PDF, JPG, PNG or WEBP" in r.json()["error"]
    assert list((env[3] / "uploads").glob("*")) == []               # nothing left behind


def test_file_type_comes_from_content_not_from_the_name(env):
    c, *_ = env
    jpg = (CASES / "01_happy_path" / "docs" / "pan_card.jpg").read_bytes()
    r = upload(c, {}, files={"pan_card": ("evil.exe", io.BytesIO(jpg), "application/octet-stream")})
    assert r.status_code == 200
    d = env[3] / "uploads" / r.json()["source"].split(":")[1] / "docs"
    assert [p.name for p in d.iterdir()] == ["pan_card.jpg"]


def test_oversized_upload_and_bad_json_are_refused(env, monkeypatch):
    c, *_ = env
    monkeypatch.setattr(server, "MAX_UPLOAD_BYTES", 1000)
    big = b"%PDF-1.4 " + b"x" * 5000
    assert upload(c, {}, files={"pan_card": ("a.pdf", io.BytesIO(big), "application/pdf")}).status_code == 413
    assert c.post("/api/uploads", data={"submission": "{not json"}).status_code == 400


def test_form_values_are_sanitised(env):
    c, *_ = env
    j = upload(c, {"company": {"vendor_name": "A" * 5000, "entity_type": ["x"], "evil": "y"}, "__proto__": {"a": 1},
                   "submission_id": "../../etc"}).json()
    sub = json.loads((server_dir(env, j["source"]) / "submission.json").read_text())
    assert len(sub["company"]["vendor_name"]) == 200 and "evil" not in sub["company"]
    assert "__proto__" not in sub and sub["submission_id"].startswith("SUB-W")


# ------------------------------------------------------------ limits & admin
def test_rate_limit(env, monkeypatch):
    c, proxy, *_ = env
    monkeypatch.setattr(server, "RUNS_PER_HOUR", 2)
    proxy.load("01_happy_path")
    assert c.post("/api/uploads", data={"submission": "{}"}).status_code == 200
    assert c.post("/api/uploads", data={"submission": "{}"}).status_code == 200
    r = c.post("/api/uploads", data={"submission": "{}"})
    assert r.status_code == 429 and "limit" in r.json()["error"].lower()


def test_concurrency_limit_returns_429_while_a_run_is_in_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "MAX_CONCURRENT", 1)
    gate, started = threading.Event(), threading.Event()

    class Slow:
        notify = None
        def read(self, **kw):
            started.set(); gate.wait(5)
            raise ExtractionError("stop")
        def draft(self, s, p): return ""
    c = TestClient(server.create_app(reader=Slow(), store=Store(":memory:"), cases_dir=CASES, uploads_dir=tmp_path))
    t = threading.Thread(target=lambda: sse(c, "case:01_happy_path"))
    t.start()
    assert started.wait(5)
    r = c.post("/api/run", json={"source": "case:01_happy_path"})
    gate.set(); t.join(5)
    assert r.status_code == 429 and "busy" in r.json()["error"]


def test_no_model_key_gives_a_clear_503(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_key_present", lambda: False)
    c = TestClient(server.create_app(reader=None, store=Store(":memory:"), cases_dir=CASES, uploads_dir=tmp_path))
    assert c.get("/api/config").json()["ready"] is False
    r = c.post("/api/run", json={"source": "case:01_happy_path"})
    assert r.status_code == 503 and "GEMINI_API_KEY" in r.json()["error"]


def test_admin_reset_is_hidden_unless_a_token_is_configured(env, monkeypatch):
    c, proxy, store, _ = env
    proxy.load("01_happy_path"); sse(c, "case:01_happy_path")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    assert c.post("/api/admin/reset").status_code == 404
    monkeypatch.setenv("ADMIN_TOKEN", "s3cret")
    assert c.post("/api/admin/reset", headers={"x-admin-token": "wrong"}).status_code == 403
    assert store.list_runs() != []
    assert c.post("/api/admin/reset", headers={"x-admin-token": "s3cret"}).status_code == 200
    assert store.list_runs() == []
