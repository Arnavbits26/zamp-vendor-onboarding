"""List Gemini models your key can use:  python -m app.list_models"""
import os
import sys

from app.config import LLM_PROVIDER  # noqa: F401  (loads .env)

key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
if not key:
    sys.exit("GEMINI_API_KEY is not set in .env")

from google import genai

client = genai.Client(api_key=key)
rows = []
for m in client.models.list():
    if "generateContent" in (getattr(m, "supported_actions", None) or []):
        rows.append(m.name.replace("models/", ""))
print("Models usable with this key (put one in .env as GEMINI_MODEL=...):\n")
for name in sorted(rows):
    print("  ", name)
print("\nTip: names containing 'flash-lite' usually have the biggest free daily allowance.")
