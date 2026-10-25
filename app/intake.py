"""Stage 1 - Intake: load a submission and resolve its uploaded file paths."""
from __future__ import annotations

import json
from pathlib import Path

from app.events import RunLog


def load_submission(case_dir: str | Path, log: RunLog | None = None) -> dict:
    case_dir = Path(case_dir).resolve()
    sub_file = case_dir / "submission.json"
    if not sub_file.exists():
        raise FileNotFoundError(f"No submission.json in {case_dir}")

    sub = json.loads(sub_file.read_text())
    sub["_case_dir"] = str(case_dir)
    # slot -> absolute path (existence is checked in the completeness stage)
    sub["_doc_paths"] = {slot: str(case_dir / rel)
                         for slot, rel in (sub.get("documents") or {}).items()}

    if log:
        log.emit("intake", "done",
                 f"Received submission {sub.get('submission_id', '?')} from "
                 f"{sub.get('company', {}).get('vendor_name', 'unknown vendor')}",
                 submission_id=sub.get("submission_id"),
                 version=sub.get("version", 1),
                 documents=sorted(sub["_doc_paths"]))
    return sub
