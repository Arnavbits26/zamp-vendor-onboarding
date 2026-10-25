from types import SimpleNamespace

from app.check_models import apply_env, candidates, probe, rank


def test_rank_prefers_stable_then_lite():
    got = rank(["x-flash-preview", "x-flash", "x-flash-lite", "x-flash-lite-preview"])
    assert got == ["x-flash-lite", "x-flash", "x-flash-lite-preview", "x-flash-preview"]


def test_apply_env_replaces_old_lines_and_keeps_the_rest(tmp_path):
    f = tmp_path / ".env"
    f.write_text("LLM_PROVIDER=gemini\nGEMINI_API_KEY=secret\nGEMINI_MODEL=the-name-you-picked\n"
                 "GEMINI_FALLBACK_MODEL=old\nEXTRACTION_CACHE=1\n")
    apply_env(f, "good-flash-lite", "good-flash")
    text = f.read_text().splitlines()
    assert "GEMINI_API_KEY=secret" in text and "EXTRACTION_CACHE=1" in text
    assert text.count("GEMINI_MODEL=good-flash-lite") == 1 and "GEMINI_FALLBACK_MODEL=good-flash" in text
    assert not any("the-name-you-picked" in l or l.endswith("=old") for l in text)


def test_apply_env_without_fallback_and_without_file(tmp_path):
    f = tmp_path / ".env"
    apply_env(f, "only-one", None)
    assert f.read_text() == "GEMINI_MODEL=only-one\n"


def test_probe_and_candidates_with_fake_client():
    class C:
        def __init__(self):
            self.models = SimpleNamespace(list=self._list, generate_content=self._gen)
        def _list(self):
            act = ["generateContent"]
            return [SimpleNamespace(name="models/a-flash-lite", supported_actions=act),
                    SimpleNamespace(name="models/a-flash-image", supported_actions=act),
                    SimpleNamespace(name="models/a-pro", supported_actions=act),
                    SimpleNamespace(name="models/b-flash", supported_actions=["embedContent"])]
        def _gen(self, model, contents):
            if model == "a-flash-lite":
                raise RuntimeError("503 UNAVAILABLE   busy")
    c = C()
    assert candidates(c) == ["a-flash-lite"]
    ok, why = probe(c, "a-flash-lite")
    assert not ok and "503 UNAVAILABLE busy" in why
    assert probe(c, "other") == (True, "")
