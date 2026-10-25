import os

os.environ["LLM_PROVIDER"] = "gemini"
os.environ["GEMINI_MODEL"] = "test-primary-model"
os.environ["GEMINI_FALLBACK_MODEL"] = ""
os.environ["EXTRACTION_CACHE"] = "0"
os.environ["GEMINI_API_KEY"] = "test-key-never-used"
os.environ.pop("GOOGLE_API_KEY", None)
os.environ.pop("ANTHROPIC_API_KEY", None)
