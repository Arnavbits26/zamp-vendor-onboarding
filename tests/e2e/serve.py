"""Serve the app for browser-level tests, with a perfect-model stand-in (no API key, no network)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import os
os.environ.update(LLM_PROVIDER="gemini", GEMINI_API_KEY="test", EXTRACTION_CACHE="0", GEMINI_FALLBACK_MODEL="")

import uvicorn
import app.server as server
from app.store import Store
from tests.support import OracleReader


class Smart:
    notify = None
    current = None
    def read(self, **kw): return self.current.read(**kw)
    def draft(self, s, p): return self.current.draft(s, p)


reader = Smart()
real_run = server.run_submission


def run_with_oracle(case_dir, rdr, **kw):
    wrong = {"gst_certificate": "pan_card"} if Path(case_dir).name == "05a_wrong_document" else None
    reader.current = OracleReader("x", case_dir=Path(case_dir), wrong=wrong)
    return real_run(case_dir, rdr, **kw)


server.run_submission = run_with_oracle
app = server.create_app(reader=reader, store=Store(":memory:"), uploads_dir=Path("/tmp/e2e_uploads"))

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8765, log_level="warning")
