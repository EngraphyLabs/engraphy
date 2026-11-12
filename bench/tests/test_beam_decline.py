"""The decline rendering is exact-token only and independent of the question."""
from bench.beam_pass import DECLINE_SENTENCE, render_decline


def test_only_the_bare_token_is_rendered():
    assert render_decline("INSUFFICIENT") == (DECLINE_SENTENCE, True)
    assert render_decline("  INSUFFICIENT\n") == (DECLINE_SENTENCE, True)
    assert render_decline("INSUFFICIENT: no record") == ("INSUFFICIENT: no record", False)
    assert render_decline("Flask 2.3.1") == ("Flask 2.3.1", False)
