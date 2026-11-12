"""The opt-in anonymous-user instruction reaches the extractor and the reader only when set."""


def test_off_by_default(monkeypatch):
    monkeypatch.delenv("ENGRAPHY_BENCH_ANONYMOUS_USER", raising=False)
    from bench.core import run as harness
    from bench.core.answer import build_reader_system
    from bench.core.extract import LLMExtractor
    pack, _ = harness.load_named_pack("conversational")
    ext = LLMExtractor(object(), pack, prompt_name="extract-wide.md")
    system, manifest = build_reader_system("grounded")
    assert "anonymous person" not in ext.system and "anonymous person" not in system
    assert manifest["anonymous_user_instruction"] is False
    assert harness._anonymous_user_manifest() is None


def test_on_reaches_extractor_and_reader(monkeypatch):
    monkeypatch.setenv("ENGRAPHY_BENCH_ANONYMOUS_USER", "1")
    from bench.core import run as harness
    from bench.core.answer import build_reader_system
    from bench.core.extract import LLMExtractor
    pack, _ = harness.load_named_pack("conversational")
    ext = LLMExtractor(object(), pack, prompt_name="extract-wide.md")
    system, manifest = build_reader_system("grounded")
    assert "anonymous person" in ext.system and "anonymous person" in system
    assert manifest["anonymous_user_instruction"] is True
    m = harness._anonymous_user_manifest()
    assert m["prompt"] == "anonymous-user.md" and m["sha256"].startswith("sha256:")
