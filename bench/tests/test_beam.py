"""BEAM wiring: the loader, the vendored upstream evaluation, the rubric judge,
Kendall tau-b, and the aggregation. No model call and no database."""
from __future__ import annotations

import json
import math
import pathlib
import re

import pytest

from bench.adapters.beam import CATEGORIES, GOLD_FIELD, BeamLoader
from bench.core import beam_official as bo
from bench.core.corpus import CorpusError, Question

REPO = pathlib.Path(__file__).resolve().parents[2]
BEAM = REPO / "datasets" / "beam"
REAL = pytest.mark.skipif(not (BEAM / "rows_0.json").exists(),
                          reason="datasets/beam is not acquired (gitignored)")


# ---------------------------------------------------------------- the real files
@pytest.fixture(scope="module")
def corpus():
    return BeamLoader().load(BEAM / "100K.parquet")


@REAL
def test_the_100k_split_lowers_to_20_conversations_and_400_questions(corpus):
    stats = corpus.stats()
    assert stats["haystacks"] == 20 and stats["questions"] == 400
    assert stats["sessions"] == 90 and stats["turns"] == 5732
    assert set(corpus.categories) == CATEGORIES and len(CATEGORIES) == 10
    assert all(n == 40 for n in stats["questions_per_category"].values())
    assert stats["abstain_questions"] == 40
    assert [h.haystack_id for h in corpus.haystacks] == [str(i) for i in range(1, 21)]
    assert len(corpus.notes["rows_files"]) == 4
    assert corpus.notes["evidence_ids_not_in_conversation"] == []


@REAL
def test_sessions_are_the_chat_batches_in_date_order_with_their_anchor(corpus):
    from datetime import datetime

    first = corpus.haystacks[0]
    assert [s.session_id for s in first.sessions] == ["session_1", "session_2", "session_3"]
    assert [s.timestamp for s in first.sessions] == ["March-15-2024", "April-05-2024",
                                                     "April-25-2024"]
    for h in corpus.haystacks:
        dates = [datetime.strptime(s.timestamp, "%B-%d-%Y")  # noqa: DTZ007 -- a date, no zone
                 for s in h.sessions]
        assert dates == sorted(dates)
        ids = [int(t.turn_id) for s in h.sessions for t in s.turns]
        assert ids == list(range(len(ids)))
        assert all(t.timestamp == s.timestamp for s in h.sessions for t in s.turns)


@REAL
def test_turn_text_keeps_upstreams_marker_and_questions_carry_their_rubric(corpus):
    opening = corpus.haystacks[0].sessions[0].turns[0]
    assert opening.speaker == "user" and opening.text.endswith("->-> 1,1")
    by_id = {q.question_id: q for q in corpus.questions}
    q = by_id["1:information_extraction:0"]
    assert q.text == "When does my first sprint end?"
    assert q.rubric == ("LLM response should state: March 29",)
    assert q.evidence == ("28",) and q.question_date is None and not q.abstain_expected
    eo = by_id["1:event_ordering:0"]
    assert eo.rubric == ("Core functionality", "Transaction error handling",
                         "Security and deployment")
    assert by_id["1:abstention:0"].abstain_expected
    assert all(q.rubric for q in corpus.questions)
    # upstream's tau-b is defined for every event_ordering question: at least 3 items
    assert min(len(q.rubric) for q in corpus.questions if q.category == "event_ordering") >= 3


@REAL
def test_the_prompt_budget_splitter_handles_beams_longest_turns(corpus):
    """Two assistant turns run to 348,853 and 53,108 characters. At a 20,000
    character budget every call stays within it and no split raises."""
    from bench.core import run as harness
    from bench.core.extract import LLMExtractor, window_sessions

    seen = []

    class Client:
        def complete(self, system, user, **kw):
            seen.append(len(user))

            class R:
                data = {"nodes": [], "edges": []}  # noqa: RUF012 -- a read-only stub
                input_tokens, output_tokens = 0, 0
            return R()

    pack, _ = harness.load_named_pack("conversational")
    ext = LLMExtractor(Client(), pack, prompt_name="extract-wide.md", max_prompt_chars=20000)
    for hid, turn_id in (("4", "97"), ("5", "123")):
        h = next(h for h in corpus.haystacks if h.haystack_id == hid)
        session = next(s for s in h.sessions if any(t.turn_id == turn_id for t in s.turns))
        assert max(len(t.text) for t in session.turns) > 50000
        for window in window_sessions(hid, session,
                                      prior_titles=tuple("t" * 80 for _ in range(200))):
            if any(t.turn_id == turn_id for t in window.turns):
                ext.extract(window)
    assert seen and max(seen) <= 20000 and ext.windows_split > 20


# ---------------------------------------------------------------- the loader on a fixture
def _row(anchors=("March-15-2024", "April-05-2024"), rubric="['LLM response should state: 3']"):
    chat, n = [], 0
    for b, anchor in enumerate(anchors, start=1):
        chat.append([
            {"content": f"opening {b} ->-> {b},1", "id": n, "index": f"{b},1",
             "question_type": "main_question", "role": "user", "time_anchor": anchor},
            {"content": f"reply {b}", "id": n + 1, "index": None, "question_type": None,
             "role": "assistant", "time_anchor": None},
        ])
        n += 2
    probing = {c: [{"question": f"q {c}?", GOLD_FIELD[c]: f"gold {c}", "rubric": rubric,
                    "source_chat_ids": {"first": [0], "second": [2]}}] for c in CATEGORIES}
    probing["abstention"][0]["source_chat_ids"] = None
    return {"conversation_id": "7", "chat": chat, "probing_questions": repr(probing)}


def _write(tmp_path, row, total=1):
    page = {"rows": [{"row_idx": 0, "row": row}], "num_rows_total": total}
    (tmp_path / "rows_0.json").write_text(json.dumps(page), encoding="utf-8")
    return tmp_path


def test_the_loader_parses_the_rubric_repr_and_the_evidence_ids(tmp_path):
    corpus = BeamLoader().load(_write(tmp_path, _row()))
    assert len(corpus.questions) == 10 and corpus.haystacks[0].haystack_id == "7"
    q = next(q for q in corpus.questions if q.category == "temporal_reasoning")
    assert q.question_id == "7:temporal_reasoning:0"
    assert q.rubric == ("LLM response should state: 3",) and q.evidence == ("0", "2")
    assert q.gold_answer == "gold temporal_reasoning"
    assert corpus.notes["turns_with_marker"] == {"user": 2, "assistant": 0}


def test_the_loader_refuses_backwards_dates_and_missing_pages(tmp_path):
    with pytest.raises(CorpusError, match="runs backwards"):
        BeamLoader().load(_write(tmp_path, _row(anchors=("May-01-2024", "April-05-2024"))))
    with pytest.raises(CorpusError, match="missing or repeated"):
        BeamLoader().load(_write(tmp_path, _row(), total=2))
    with pytest.raises(CorpusError, match="rubric"):
        BeamLoader().load(_write(tmp_path, _row(rubric="[]")))


def test_beam_is_registered_with_its_licence():
    from bench.core import run as harness

    assert harness.LOADERS["beam"] is BeamLoader
    licence = harness.DATASET_LICENCES["beam"]
    assert "CC BY-SA 4.0" in licence and "Tavakoli" in licence
    assert chr(0x2014) not in licence


# ---------------------------------------------------------------- vendored upstream
def test_the_vendored_files_match_their_pins(tmp_path):
    pins = bo.verify_vendored()
    assert set(pins) == {"prompts.upstream.py", "compute_metrics.upstream.py",
                         "run_evaluation.upstream.py", "report_results.upstream.py",
                         "llm.upstream.py", "LICENSE.upstream"}
    assert bo.OFFICIAL_SOURCE["commit"] == "b2da22eac88bb0874c64665f13457eb99835774a"
    assert "MIT License" in (bo.PROMPT_DIR / "LICENSE.upstream").read_text(encoding="utf-8")
    tampered = tmp_path / "compute_metrics.upstream.py"
    tampered.write_bytes((bo.PROMPT_DIR / tampered.name).read_bytes() + b"\n")
    with pytest.raises(RuntimeError, match="not the pinned upstream file"):
        bo._source(tampered)


def test_upstream_names_its_judge_model():
    src = (bo.PROMPT_DIR / "llm.upstream.py").read_text(encoding="utf-8")
    assert 'model_name="gpt-4.1-mini"' in src
    assert "model=gpt_llm" in (bo.PROMPT_DIR / "run_evaluation.upstream.py").read_text(
        encoding="utf-8")


def test_the_judge_prompt_is_upstreams_template_with_both_slots_filled():
    raw = (bo.PROMPT_DIR / "prompts.upstream.py").read_text(encoding="utf-8")
    assert bo.JUDGE_TEMPLATE in raw and bo.ANSWER_TEMPLATE in raw
    prompt = bo.judge_prompt("LLM response should state: March 29", "It ends March 29.")
    assert "- RUBRIC CRITERION (what to check): LLM response should state: March 29" in prompt
    assert "- RESPONSE TO EVALUATE: It ends March 29." in prompt
    assert "<rubric_item>" not in prompt and "<llm_response>" not in prompt
    assert '"score": [your score: 1.0, 0.5, or 0.0]' in prompt


def test_the_answer_prompt_fills_upstreams_rag_template():
    q = Question("1:x:0", "1", "When?", "information_extraction", "g")
    prompt = bo.answer_prompt(q, {"results": []})
    assert "QUESTION:\nWhen?" in prompt and "<context>" not in prompt


# ---------------------------------------------------------------- score parsing
def test_judge_replies_parse_as_upstream_reads_them():
    assert bo.parse_json_response('{"score": 1.0, "reason": "ok"}')["score"] == 1.0
    fenced = '```json\n{"score": 0.5, "reason": "partly"}\n```'
    assert bo.parse_json_response(fenced) == {"score": 0.5, "reason": "partly"}
    assert bo.parse_json_response('Here: {"score": 0.0, "reason": "no"} done')["score"] == 0.0
    with pytest.raises(ValueError):
        bo.parse_json_response("no json at all")
    assert json.loads(bo.repair_json('{"score": 0.5, "reason": "cut off'))["score"] == 0.5
    with pytest.raises(ValueError):
        bo.repair_json("I cannot grade this")


class _Fake:
    """A judge client: rubric-judge calls (no system prompt) answer from `scores`
    in order; equivalence calls (a system prompt) answer YES when the snippets
    are equal ignoring case and a leading list number."""

    def __init__(self, scores=()):
        self.scores = list(scores)
        self.calls = []

    def complete(self, system, user, **kw):
        self.calls.append((system, user))
        if system:
            m = re.search(r"First snippet: (.*?) \n\s*Second snippet: (.*?)\s*$", user, re.DOTALL)
            a, b = (re.sub(r"^\s*\d+[.)]\s*", "", s).strip().lower() for s in m.groups())
            text = "YES" if a == b else "NO"
        else:
            text = json.dumps({"score": self.scores.pop(0), "reason": "r"})

        class R:
            pass
        r = R()
        r.text, r.seconds, r.model = text, 0.01, "fake-judge"
        return r


def _q(category, rubric, qid="1:c:0"):
    return Question(qid, "1", "q?", category, "gold", rubric=tuple(rubric),
                    abstain_expected=category == "abstention")


def test_a_question_scores_the_mean_over_its_rubric_items():
    client = _Fake(scores=[1.0, 0.5])
    v = bo.OfficialJudge(client).grade(_q("multi_session_reasoning", ["A", "B"]), "resp")
    assert v.score == 0.75 and v.graded_by == "beam_official_judge" and not v.correct
    assert [c[0] for c in client.calls] == ["", ""]
    assert client.calls[0][1] == bo.judge_prompt("A", "resp")
    row = v.as_dict()
    assert row["score"] == 0.75 and row["llm_judge_score"] == 0.75 and row["judge_calls"] == 2
    assert row["judge_model"] == "fake-judge"


def test_an_unparseable_reply_is_a_judge_error_not_a_zero():
    class Bad(_Fake):
        def complete(self, system, user, **kw):
            r = super().complete(system, user)
            r.text = "I would rather not say"
            return r

    v = bo.OfficialJudge(Bad(scores=[1.0])).grade(_q("abstention", ["A"]), "resp")
    assert v.graded_by == "judge_error" and "no score" in v.error


def test_event_ordering_reports_tau_norm_from_the_aligned_lines():
    rubric = ["Core functionality", "Error handling", "Security"]
    right = bo.OfficialJudge(_Fake(scores=[1.0] * 3)).grade(
        _q("event_ordering", rubric), "1. Core functionality\n2. Error handling\n3. Security")
    assert right.score == 1.0 and right.detail["f1"] == 1.0
    reverse = bo.OfficialJudge(_Fake(scores=[1.0] * 3)).grade(
        _q("event_ordering", rubric), "Security\nError handling\nCore functionality")
    # every item matched, order fully reversed: tau -1, normalised 0, while the
    # rubric judge (not reported for this category) gave full marks
    assert reverse.score == 0.0 and reverse.detail["llm_judge_score"] == 1.0


# ---------------------------------------------------------------- Kendall tau-b
def test_tau_b_against_hand_computed_cases():
    assert bo.kendall_tau_b([1, 2, 3, 4], [1, 2, 3, 4])[0] == 1.0
    assert bo.kendall_tau_b([1, 2, 3, 4], [4, 3, 2, 1])[0] == -1.0
    # one discordant pair of six
    assert bo.kendall_tau_b([1, 2, 3, 4], [1, 3, 2, 4])[0] == pytest.approx(4 / 6)
    # one pair tied in y: 5 concordant, sqrt(6 * 5)
    assert bo.kendall_tau_b([1, 2, 3, 4], [1, 2, 5, 5])[0] == pytest.approx(5 / math.sqrt(30))
    # one tie on each side, one concordant pair left: 1 / sqrt(2 * 2)
    assert bo.kendall_tau_b([1, 1, 2], [1, 2, 2])[0] == pytest.approx(0.5)
    assert math.isnan(bo.kendall_tau_b([1, 1, 1], [1, 2, 3])[0])


def test_event_ordering_score_uses_upstreams_tie_rank_construction():
    """Reference [A, B, C], system [B, A, X]. Union A, B, C, X; tie_rank 5.
    Reference ranks 1, 2, 3, 5; system ranks 2, 1, 5, 3. Pairs: AB discordant,
    AC, AX, BC, BX concordant, CX discordant: tau = 2 / 6, normalised 2 / 3.
    Two of three matched each way: precision, recall and F1 are 2 / 3."""
    s = bo.event_ordering_score(["A", "B", "C"], ["B", "A", "X"], align_type="llm",
                                llm=bo.UpstreamModel(_Fake()))
    assert s["tau_norm"] == pytest.approx(2 / 3)
    assert s["precision"] == s["recall"] == s["f1"] == pytest.approx(2 / 3)
    assert s["final_score"] == pytest.approx(4 / 9)


# ---------------------------------------------------------------- aggregation
def _rows():
    rows = []
    for conv, base in (("1", 1.0), ("2", 0.0)):
        for cat in bo.CATEGORY_ORDER:
            rows.append({"haystack_id": conv, "category": cat, "score": base})
            rows.append({"haystack_id": conv, "category": cat, "score": 0.5})
    # an unequal count: conversation 1 has a third summarization question
    rows.append({"haystack_id": "1", "category": "summarization", "score": 0.0})
    return rows


def test_aggregation_is_the_mean_over_conversations_of_each_conversation_mean():
    agg = bo.aggregate(_rows())
    cats = agg["categories"]
    assert cats["abstention"]["score"] == pytest.approx((0.75 + 0.25) / 2)
    assert cats["summarization"]["score"] == pytest.approx((0.5 + 0.25) / 2)
    assert cats["summarization"]["questions"] == 5
    expected = sum(c["score"] for c in cats.values()) / 10
    assert agg["overall"]["score"] == pytest.approx(expected)
    assert "upstream reports" in agg["overall"]["note"]


def test_aggregation_matches_upstreams_report_results(tmp_path):
    """Run upstream's own report_llms_accuracies over the same scores, written in
    its evaluation-file layout, with pandas stubbed to capture the table."""
    captured = {}

    class DataFrame:
        def __init__(self, data, columns):
            captured["rows"], captured["columns"] = data, columns

        def insert(self, *a):
            pass

        def to_excel(self, *a, **k):
            pass

    class Pd:
        pass
    Pd.DataFrame = DataFrame

    rows = _rows()
    for conv in ("1", "2"):
        d = tmp_path / conv
        d.mkdir()
        data = {}
        for cat in bo.CATEGORY_ORDER:
            key = "tau_norm" if cat == "event_ordering" else "llm_judge_score"
            data[cat] = [{key: r["score"]} for r in rows
                         if r["haystack_id"] == conv and r["category"] == cat]
        (d / "evaluation-results-x.json").write_text(json.dumps(data), encoding="utf-8")

    bo.compile_upstream_report(Pd)(str(tmp_path), ["evaluation-results-x.json"], "out.xlsx")
    assert tuple(captured["columns"]) == bo.CATEGORY_ORDER
    ours = bo.aggregate(rows)["categories"]
    assert captured["rows"][0] == pytest.approx([ours[c]["score"] for c in bo.CATEGORY_ORDER])


def test_the_conventions_manifest_states_the_judge_difference():
    m = bo.conventions_manifest(reader="engraphy")
    assert m["source"]["commit"].startswith("b2da22e")
    assert any("gpt-4.1-mini" in d for d in m["differences"])
    assert m["answer_prompt_sha256"] is None
    assert bo.conventions_manifest(reader="official")["answer_prompt_sha256"].startswith("sha256:")


@REAL
def test_the_pass_grades_a_saved_run_offline_end_to_end(tmp_path, monkeypatch, capsys):
    """A two-conversation run on disk, graded by `bench.beam_pass` with the judge
    client stubbed: every question is graded once and the report is written."""
    import sys

    from bench import beam_pass

    run = tmp_path / "beam-run"
    run.mkdir()
    corpus = BeamLoader().load(BEAM / "100K.parquet")
    wanted = ["1", "2"]
    qs = [q for q in corpus.questions if q.haystack_id in wanted]
    (run / "manifest.json").write_text(json.dumps({
        "run_id": "beam-run", "benchmark": "beam", "haystacks": wanted,
        "dataset": {"path": "beam/100K.parquet"}}), encoding="utf-8")
    arm = "llm_wide-conversational/search_only/always_distinct/k25"
    (run / "results.jsonl").write_text("".join(
        json.dumps({"arm": arm, "question_id": q.question_id, "answer": "a\nb"}) + "\n"
        for q in qs), encoding="utf-8")
    (run / "envelopes.jsonl").write_text("".join(
        json.dumps({"arm": arm, "question_id": q.question_id, "envelope": {"results": []}})
        + "\n" for q in qs), encoding="utf-8")

    class Judge(_Fake):
        def complete(self, system, user, **kw):
            self.scores.append(0.5)
            return super().complete(system, user, **kw)

    monkeypatch.setattr(beam_pass, "ClaudeCLIClient", lambda model: Judge())
    monkeypatch.setattr(sys, "argv", ["bench.beam_pass", "--run-dir", str(run),
                                      "--reader", "engraphy", "--concurrency", "1",
                                      "--judge-concurrency", "1"])
    assert beam_pass.main() == 0
    out = run / "official"
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    agg = manifest["aggregate"][f"{arm}/official"]
    assert agg["questions"] == 40 and agg["conversations"] == 2
    assert agg["categories"]["summarization"]["score"] == pytest.approx(0.5)
    assert manifest["rubric_items"] == sum(len(q.rubric) for q in qs)
    assert manifest["dataset_licence"].startswith("CC BY-SA 4.0")
    assert "overall (unweighted mean of 10 categories" in (out / "report.md").read_text(
        encoding="utf-8")
