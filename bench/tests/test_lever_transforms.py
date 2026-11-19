"""Session-date transforms: dates come from the memory's own verbatim source turns."""
import json
from types import SimpleNamespace

from bench.core.answer import render_envelope
from bench.lever_transforms import SessionDates, date_annotate, date_sort


def _dates(tmp_path):
    data = [{"question_id": "q1",
             "haystack_dates": ["2023/05/01 (Mon) 10:00", "2023/04/01 (Sat) 09:00"],
             "haystack_sessions": [[{"role": "user", "content": "I moved to Leeds."}],
                                   [{"role": "user", "content": "I live in York."}]]}]
    p = tmp_path / "d.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return SessionDates(p)


def _env():
    def node(title, src):
        return {"node": {"title": title, "body": f"{title}\n\nSource (verbatim):\nuser: {src}",
                         "created_at": "2026-10-15T00:00:00+00:00"}}
    return {"results": [node("Leeds", "I moved to Leeds."), node("York", "I live in York."),
                        {"node": {"title": "no source", "body": "x"}}]}


def test_annotate_replaces_the_ingest_date(tmp_path):
    q = SimpleNamespace(question_id="q1")
    env = date_annotate(_dates(tmp_path))(q, _env())
    nodes = [r["node"] for r in env["results"]]
    assert nodes[0]["attrs"]["session_date"] == "2023/05/01 (Mon) 10:00"
    assert all("created_at" not in n for n in nodes)
    assert "2026" not in render_envelope(env)
    assert "session_date" not in (nodes[2].get("attrs") or {})


def test_sort_orders_oldest_first_and_says_so(tmp_path):
    q = SimpleNamespace(question_id="q1")
    env = date_sort(_dates(tmp_path))(q, _env())
    assert [r["node"]["title"] for r in env["results"]] == ["York", "Leeds", "no source"]
    assert "ordered by session date, oldest first" in render_envelope(env)


def test_the_saved_envelope_renders_as_before():
    assert render_envelope(_env()).startswith(
        "The following memories were retrieved, most relevant first:")
