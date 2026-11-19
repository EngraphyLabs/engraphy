"""The lever A/B's selection is deterministic and stratified, and its reader variants
leave the shipped reader unchanged."""
from types import SimpleNamespace

from bench.core.answer import build_reader_system, render_envelope_json, split_reply
from bench.lever_ab import select


def _qs(n_per_type=12):
    return {f"{t}-{i}": SimpleNamespace(category=t)
            for t in ("a", "b", "c") for i in range(n_per_type)}


def test_per_type_and_slices():
    qs = _qs()
    picked = select(qs, per_type=4)
    assert len(picked) == 12 and select(qs, per_type=4) == picked
    dev, test = set(select(qs, slice_="dev")), set(select(qs, slice_="test"))
    assert dev and test and not dev & test and dev | test == set(qs)


def test_default_reader_is_unchanged_by_the_variants():
    base, _ = build_reader_system()
    assert base == build_reader_system(contract="verify", render="prose")[0]
    con, _ = build_reader_system(contract="con")
    assert con.startswith(base.split("Lay out your reply")[0])
    assert "NOTES:" in con and "NOTES:" not in base


def test_con_reply_parses_to_the_answer_and_the_bare_decline():
    assert split_reply("NOTES: [2] moved in May\nSo May.\nANSWER: May 2023", "con") == (
        "NOTES: [2] moved in May\nSo May.", "May 2023")
    assert split_reply("NOTES: none\nANSWER: INSUFFICIENT", "con")[1] == "INSUFFICIENT"


def test_json_rendering_keeps_every_content_field():
    env = {"results": [{"node": {"id": "x", "type": "fact", "title": "T", "body": "B",
                                 "attrs": {"occurred_on": "2023-05-01", "e": ""},
                                 "created_at": "2023-05-02T00:00:00+00:00"}, "score": 0.1}]}
    text = render_envelope_json(env)
    assert '"title": "T"' in text and '"body": "B"' in text and "occurred_on" in text
    assert '"id"' not in text and "score" not in text and '"e"' not in text
