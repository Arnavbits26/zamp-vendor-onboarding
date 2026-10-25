"""Central settings. Secrets come from .env (never committed)."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

# Which model reads the documents. Set in .env, no code changes needed:
#   LLM_PROVIDER=gemini      (default - free tier, key from Google AI Studio)
#   LLM_PROVIDER=anthropic   (optional - paid)
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").lower()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
# Optional second model, tried automatically if the first stays overloaded/rate-limited.
GEMINI_FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL") or None
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", os.getenv("EXTRACTION_MODEL", "claude-sonnet-5-5"))
MODEL = ANTHROPIC_MODEL  # kept for backwards compatibility

# Free tiers have small daily quotas. With EXTRACTION_CACHE=1 the same file is only
# ever sent to the model once (results saved in data/cache/). Great while developing;
# leave it OFF for the real demo so the interviewer sees a genuine live run.
EXTRACTION_CACHE = os.getenv("EXTRACTION_CACHE", "0") == "1"

# A PDF with fewer extractable characters than this is treated as a scan
# and sent to the model as an image instead of text.
TEXT_LAYER_MIN_CHARS = 80

# Longest side (pixels) of images sent to the model.
MAX_IMAGE_SIDE = 1600
