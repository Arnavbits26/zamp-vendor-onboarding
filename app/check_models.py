"""
Test which Gemini Flash models are answering RIGHT NOW and (optionally) set .env for you.

    python -m app.check_models            # just show which models work
    python -m app.check_models --apply    # also write GEMINI_MODEL / GEMINI_FALLBACK_MODEL to .env

Sends one tiny text message per model (a handful of free requests).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from app.config import ROOT  # loads .env

SKIP = ("image", "tts", "live", "audio", "embedding", "robotics", "computer", "veo", "imagen", "native")


def candidates(client) -> list[str]:
    names = []
    for m in client.models.list():
        n = m.name.replace("models/", "")
        if ("generateContent" in (getattr(m, "supported_actions", None) or [])
                and "flash" in n and not any(x in n for x in SKIP)):
            names.append(n)
    return sorted(set(names))


def probe(client, name: str) -> tuple[bool, str]:
    try:
        client.models.generate_content(model=name, contents="Reply with the single word OK")
        return True, ""
    except Exception as e:
        return False, " ".join(str(e).split())[:80]


def rank(working: list[str]) -> list[str]:
    """Stable releases before previews, then flash-lite (bigger free quota) before flash."""
    return sorted(working, key=lambda n: ("preview" in n, "lite" not in n, n))


def apply_env(path: Path, primary: str, fallback: str | None) -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    lines = [l for l in lines if not l.startswith(("GEMINI_MODEL=", "GEMINI_FALLBACK_MODEL="))]
    lines.append(f"GEMINI_MODEL={primary}")
    if fallback:
        lines.append(f"GEMINI_FALLBACK_MODEL={fallback}")
    path.write_text("\n".join(lines) + "\n")


def main():
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY is not set in .env")
    from google import genai
    client = genai.Client(api_key=key)

    working = []
    print("Testing models (one tiny request each)...\n")
    for name in candidates(client):
        ok, why = probe(client, name)
        print(f"  {'WORKS' if ok else 'FAILS'}  {name}" + ("" if ok else f"   <- {why}"))
        if ok:
            working.append(name)

    if not working:
        print("\nNo model is answering right now. Google is having a wider problem. "
              "Wait 10-15 minutes and run this again.")
        return
    best = rank(working)
    primary, fallback = best[0], (best[1] if len(best) > 1 else None)
    print(f"\nRecommended:\n  GEMINI_MODEL={primary}\n  GEMINI_FALLBACK_MODEL={fallback or '(only one model works right now)'}")
    if "--apply" in sys.argv:
        apply_env(ROOT / ".env", primary, fallback)
        print("\nWritten to .env. Re-run your test case now.")
    else:
        print("\nRun again with --apply to write these into .env automatically.")


if __name__ == "__main__":
    main()
