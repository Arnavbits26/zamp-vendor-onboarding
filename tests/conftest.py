"""
Tests must behave the same on every machine, whatever is in the developer's .env
(API key, backup model, cache switch, provider). This file runs before any test
module imports the app, and python-dotenv never overrides variables that are already set.
"""
import os

os.environ["LLM_PROVIDER"] = "gemini"
os.environ["GEMINI_MODEL"] = "test-primary-model"
os.environ["GEMINI_FALLBACK_MODEL"] = ""        # empty -> no backup model unless a test asks for one
os.environ["EXTRACTION_CACHE"] = "0"
os.environ["GEMINI_API_KEY"] = "test-key-never-used"
os.environ.pop("GOOGLE_API_KEY", None)
os.environ.pop("ANTHROPIC_API_KEY", None)
