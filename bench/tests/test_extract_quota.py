"""A quota stop during extraction must stop the run, not empty the store."""
import pytest

from bench.core.corpus import Session, Turn
from bench.core.extract import ExtractWindow, LLMExtractor
from bench.core.llm import LLMError, LLMResponse
from bench.core.providers import QuotaExhausted

PACK = {"node_types": {"fact": {"attrs": {"optional": {}, "closed": True}}}, "edge_types": {}}


def _window():
    turns = (Turn(speaker="A", text="I moved to Leeds in May.", turn_id="D1:1"),)
    return ExtractWindow(haystack_id="hs", session=Session(session_id="s1", turns=turns),
                         turns=turns, window_index=0)


class _Raises:
    model = "test"

    def __init__(self, exc):
        self.exc = exc

    def complete(self, *a, **k):
        raise self.exc


class _Empty:
    model = "test"

    def complete(self, *a, **k):
        return LLMResponse(text="", data={"nodes": [], "edges": []})


def test_quota_exhausted_propagates_rather_than_yielding_an_empty_window():
    ex = LLMExtractor(_Raises(QuotaExhausted("usage limit")), PACK)
    with pytest.raises(QuotaExhausted):
        ex.extract(_window())


def test_an_ordinary_llm_error_still_yields_an_empty_window():
    ex = LLMExtractor(_Raises(LLMError("transient")), PACK)
    assert ex.extract(_window()).nodes == ()


def test_an_empty_model_answer_is_still_an_empty_window():
    assert LLMExtractor(_Empty(), PACK).extract(_window()).nodes == ()
