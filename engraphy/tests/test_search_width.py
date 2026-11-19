"""ENGRAPHY_SEARCH_WIDTH: off by default, and a measurement-only widening when set."""
import pytest

from engraphy.core import search as search_mod


def test_unset_keeps_the_shipped_caps(monkeypatch):
    monkeypatch.delenv(search_mod.SEARCH_WIDTH_ENV, raising=False)
    assert search_mod.search_caps() == (30, 25)


def test_set_widens_the_result_and_leg_caps(monkeypatch):
    monkeypatch.setenv(search_mod.SEARCH_WIDTH_ENV, "50")
    assert search_mod.search_caps() == (50, 50)
    monkeypatch.setenv(search_mod.SEARCH_WIDTH_ENV, "10")
    assert search_mod.search_caps() == (30, 25)


@pytest.mark.parametrize("bad", ["0", "201", "wide"])
def test_out_of_range_is_refused(monkeypatch, bad):
    monkeypatch.setenv(search_mod.SEARCH_WIDTH_ENV, bad)
    with pytest.raises(ValueError):
        search_mod.search_caps()
