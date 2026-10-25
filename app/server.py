"""
Web server: the live run view and the dashboard.

  POST /api/run              run a submission and STREAM every stage as it happens (server-sent events)
  POST /api/uploads          create a submission from a form + files (optionally starting from an earlier one)
  GET  /api/runs, /api/runs/{id}, /api/submissions/{sid}   history for the dashboard
  GET  /api/cases, /api/source/form, /api/source/doc       test cases and previews of uploaded documents

Run locally:   uvicorn app.server:app --reload
"""
from __future__ import annotations

import json
import os
import queue
import random
import re
import shutil
import threading
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from app.changes import form_snapshot
from app.completeness import ALL_DOCS, DOC_LABELS
from app.config import EXTRACTION_CACHE, GEMINI_FALLBACK_MODEL, GEMINI_MODEL, LLM_PROVIDER, ROOT
from app.events import RunLog
from app.llm import ExtractionError, get_reader
from app.pipeline import run_submission
from app.store import DEFAULT_DB, Store

STATIC = Path(__file__).parent / "static"
RUNS_PER_HOUR = int(os.getenv("RUNS_PER_HOUR", "40"))          # per visitor, protects the free-tier quota
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT_RUNS", "3"))
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "8")) * 1024 * 1024
SID_RE = re.compile(r"^SUB-[A-Za-z0-9-]{1,16}$")
_tls = threading.local()


# ------------------------------------------------------------------ helpers
def _sniff(data: bytes) -> str | None:
    """File type from the first bytes, never from the file name the visitor chose."""
    if data.startswith(b"%PDF"):
        return ".pdf"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def _s(v, n=200) -> str:
    return str(v).strip()[:n] if v is not None else ""


def clean_form(raw) -> dict:
    """Keep only the fields we know, as short strings. Blanks stay blank: the completeness check reports them."""
    raw = raw if isinstance(raw, dict) else {}
    d = lambda x: x if isinstance(x, dict) else {}
    co, tax, bank = d(raw.get("company")), d(raw.get("tax")), d(raw.get("bank"))
    addr, contact = d(co.get("address")), d(co.get("contact"))
    out = {
        "company": {"vendor_name": _s(co.get("vendor_name")), "entity_type": _s(co.get("entity_type")),
                    "address": {k: _s(addr.get(k)) for k in ("line1", "city", "state", "pincode")},
                    "contact": {k: _s(contact.get(k)) for k in ("name", "email", "phone")}},
        "tax": {k: _s(tax.get(k)) for k in ("pan", "gstin")},
        "bank": {k: _s(bank.get(k)) for k in ("account_holder_name", "account_number", "ifsc", "bank_name")},
    }
    for k in ("cin", "proprietor_name", "incorporated_on"):
        if _s(raw.get(k)):
            out[k] = _s(raw[k])
    return out


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?"))


def _key_present() -> bool:
    name = "ANTHROPIC_API_KEY" if LLM_PROVIDER == "anthropic" else "GEMINI_API_KEY"
    return bool(os.getenv(name) or os.getenv("GOOGLE_API_KEY"))


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


# --------------------------------------------------------------------- app
def create_app(reader=None, store: Store | None = None, cases_dir: Path | None = None,
               uploads_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="Vendor onboarding", docs_url=None, redoc_url=None)
    cases_dir = Path(cases_dir or ROOT / "test_cases")
    uploads_dir = Path(uploads_dir or ROOT / "data" / "uploads")
    store = store or Store(DEFAULT_DB)
    injected = reader is not None
    if reader is None:
        try:
            reader = get_reader()
        except ExtractionError:
            reader = None
    if reader is not None:
        inner = getattr(reader, "inner", reader)
        # the model client reports retries ("servers busy, retrying in 4s"); route them to whichever run is active on this thread
        inner.notify = lambda m: getattr(_tls, "emit", lambda _m: None)(m)

    slots = threading.BoundedSemaphore(MAX_CONCURRENT)
    hits: dict[str, deque] = defaultdict(deque)
    app.state.store, app.state.reader = store, reader

    def resolve(source: str) -> Path:
        kind, _, name = (source or "").partition(":")
        if kind == "case" and re.fullmatch(r"[A-Za-z0-9_\-]{1,60}", name) and (cases_dir / name / "submission.json").is_file():
            return cases_dir / name
        if kind == "upload" and re.fullmatch(r"[0-9a-f]{32}", name) and (uploads_dir / name / "submission.json").is_file():
            return uploads_dir / name
        raise HTTPException(404, "Unknown source")

    def rate_limit(request: Request):
        ip, now = _client_ip(request), time.time()
        q = hits[ip]
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= RUNS_PER_HOUR:
            raise HTTPException(429, f"Run limit reached ({RUNS_PER_HOUR} per hour). Please try again later.")
        q.append(now)

    # ---------------------------------------------------------- pages / config
    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/api/config")
    def config():
        return {"provider": LLM_PROVIDER, "model": GEMINI_MODEL if LLM_PROVIDER == "gemini" else "claude",
                "fallback": GEMINI_FALLBACK_MODEL, "cache": EXTRACTION_CACHE,
                "ready": reader is not None and (injected or _key_present()),
                "runs_per_hour": RUNS_PER_HOUR, "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024)}

    # ---------------------------------------------------------------- cases
    @app.get("/api/cases")
    def cases():
        manifest = {}
        mf = cases_dir / "manifest.json"
        if mf.is_file():
            manifest = {m["case"]: m for m in json.loads(mf.read_text())}
        out = []
        for sub_file in sorted(cases_dir.glob("*/submission.json")):
            name, sub = sub_file.parent.name, json.loads(sub_file.read_text())
            m = manifest.get(name, {})
            out.append({"source": f"case:{name}", "name": name, "vendor": sub["company"]["vendor_name"],
                        "entity_type": sub["company"]["entity_type"], "submission_id": sub.get("submission_id"),
                        "version": sub.get("version", 1), "designed_outcome": m.get("expected_outcome"),
                        "why": m.get("why")})
        return out

    @app.get("/api/source/form")
    def source_form(source: str):
        d = resolve(source)
        sub = json.loads((d / "submission.json").read_text())
        docs = [{"slot": s, "label": DOC_LABELS[s], "file": Path(sub["documents"][s]).name}
                for s in ALL_DOCS if s in sub.get("documents", {})]
        return {"source": source, "submission_id": sub.get("submission_id"), "version": sub.get("version", 1),
                "form": {k: v for k, v in form_snapshot(sub).items() if k != "submission_id"},
                "documents": docs}

    @app.get("/api/source/doc")
    def source_doc(source: str, slot: str):
        d = resolve(source)
        sub = json.loads((d / "submission.json").read_text())
        if slot not in ALL_DOCS or slot not in sub.get("documents", {}):
            raise HTTPException(404, "No such document")
        path = (d / sub["documents"][slot]).resolve()
        if d.resolve() not in path.parents or not path.is_file():
            raise HTTPException(404, "No such document")
        return FileResponse(path)

    # -------------------------------------------------------------- uploads
    @app.post("/api/uploads")
    async def create_upload(request: Request, submission: str = Form(...), base_source: str = Form(""),
                            gst_certificate: UploadFile | None = File(None),
                            pan_card: UploadFile | None = File(None),
                            cancelled_cheque: UploadFile | None = File(None),
                            certificate_of_incorporation: UploadFile | None = File(None)):
        rate_limit(request)
        try:
            raw = json.loads(submission)
        except json.JSONDecodeError:
            raise HTTPException(400, "Form data was not valid JSON")
        form = clean_form(raw)
        sid = _s(raw.get("submission_id"), 24) if isinstance(raw, dict) else ""
        if not SID_RE.match(sid):
            sid = f"SUB-W{random.randint(10000, 99999)}"
        earlier = store.versions(sid)
        version = (earlier[-1]["version"] + 1) if earlier else 1

        base = resolve(base_source) if base_source else None
        base_docs = json.loads((base / "submission.json").read_text()).get("documents", {}) if base else {}
        uid = uuid.uuid4().hex
        dest = uploads_dir / uid
        (dest / "docs").mkdir(parents=True)
        docs: dict[str, str] = {}
        try:
            files = {"gst_certificate": gst_certificate, "pan_card": pan_card,
                     "cancelled_cheque": cancelled_cheque, "certificate_of_incorporation": certificate_of_incorporation}
            for slot in ALL_DOCS:
                f = files[slot]
                if f is not None and f.filename:
                    data = await f.read(MAX_UPLOAD_BYTES + 1)
                    if len(data) > MAX_UPLOAD_BYTES:
                        raise HTTPException(413, f"{DOC_LABELS[slot]} is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
                    ext = _sniff(data)
                    if not ext:
                        raise HTTPException(400, f"{DOC_LABELS[slot]} must be a PDF, JPG, PNG or WEBP file")
                    (dest / "docs" / f"{slot}{ext}").write_bytes(data)
                    docs[slot] = f"docs/{slot}{ext}"
                elif slot in base_docs:
                    src = base / base_docs[slot]
                    if src.is_file():
                        shutil.copy(src, dest / "docs" / src.name)     # byte-identical -> reused on resubmission
                        docs[slot] = f"docs/{src.name}"
            sub = {"submission_id": sid, "version": version, **form, "documents": docs}
            if version > 1:
                sub["resubmission_of"] = sid
            (dest / "submission.json").write_text(json.dumps(sub, indent=2))
        except Exception:
            shutil.rmtree(dest, ignore_errors=True)
            raise
        return {"source": f"upload:{uid}", "submission_id": sid, "version": version}

    # ----------------------------------------------------------------- runs
    @app.post("/api/run")
    async def run(request: Request):
        body = await request.json()
        d = resolve(body.get("source", ""))
        source = body["source"]
        rate_limit(request)
        if reader is None or not (injected or _key_present()):
            raise HTTPException(503, "No model key is configured on the server (GEMINI_API_KEY).")
        if not slots.acquire(blocking=False):
            raise HTTPException(429, "The server is busy with other runs. Try again in a few seconds.")

        q: queue.Queue = queue.Queue()

        def worker():
            log = RunLog(on_event=lambda ev: q.put(("event", ev.to_dict())))
            _tls.emit = lambda m: log.emit("model", "warn", m)
            try:
                res = run_submission(d, reader, log=log, store=store, source=source)
                q.put(("result", {k: v for k, v in res.items() if k != "events"}))
            except ExtractionError as e:
                q.put(("error", {"message": str(e)}))
            except Exception as e:                      # never leave the browser hanging
                q.put(("error", {"message": f"Unexpected error: {type(e).__name__}: {e}"}))
            finally:
                slots.release()
                q.put(None)

        threading.Thread(target=worker, daemon=True).start()

        def gen():
            yield _sse("start", {"source": source})
            while True:
                try:
                    item = q.get(timeout=10)
                except queue.Empty:
                    yield ": keep-alive\n\n"
                    continue
                if item is None:
                    break
                yield _sse(*item)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # -------------------------------------------------------------- history
    @app.get("/api/runs")
    def runs():
        return store.list_runs(200)

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: int):
        res = store.get_run(run_id)
        if not res:
            raise HTTPException(404, "No such run")
        return {**res, "run_id": run_id}

    @app.get("/api/submissions/{sid}")
    def submission_versions(sid: str):
        vs = store.versions(sid)
        if not vs:
            raise HTTPException(404, "No such submission")
        return [{"version": v["version"], "status": v["status"], "headline": v["headline"],
                 "changes": v.get("changes")} for v in vs]

    @app.post("/api/admin/reset")
    def reset(request: Request):
        token = os.getenv("ADMIN_TOKEN")
        if not token:
            raise HTTPException(404, "Not found")
        if request.headers.get("x-admin-token") != token:
            raise HTTPException(403, "Forbidden")
        store.reset()
        return {"ok": True}

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    return app


app = create_app()
