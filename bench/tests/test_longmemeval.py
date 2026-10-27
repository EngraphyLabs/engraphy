"""LongMemEval wiring: session order, the question date, the official prompts, the sampler."""
from __future__ import annotations

import json

from bench.adapters.longmemeval import LongMemEvalLoader
from bench.core.answer import Reader
from bench.core.corpus import Question
from bench.core.longmemeval_official import OfficialJudge, answer_prompt, judge_prompt
from bench.core.retrieve import Retrieval
from bench.longmemeval.sample import sample_questions_stratified


def _item(qid, qtype="knowledge-update", dates=("2023/05/02 (Tue) 10:00", "2023/05/01 (Mon) 09:00")):
    return {
        "question_id": qid, "question_type": qtype, "question": "Where do I live now?",
        "answer": "Perth", "question_date": "2023/05/20 (Sat) 02:33",
        "haystack_session_ids": [f"s{i}" for i in range(len(dates))],
        "haystack_dates": list(dates),
        "haystack_sessions": [[{"role": "user", "content": f"session {i}"}]
                              for i in range(len(dates))],
    }


def test_sessions_are_ingested_in_date_order_not_file_order(tmp_path):
    path = tmp_path / "lme.json"
    path.write_text(json.dumps([_item("q1")]), encoding="utf-8")
    corpus = LongMemEvalLoader().load(path)
    assert [s.session_id for s in corpus.haystacks[0].sessions] == ["s1", "s0"]
    assert corpus.questions[0].question_date == "2023/05/20 (Sat) 02:33"


class _Echo:
    def __init__(self):
        self.user = None

    def complete(self, system, user, **kw):
        self.user = user

        class R:
            text, seconds, model, input_tokens, output_tokens = "ANSWER: x", 0.0, "m", 0, 0
        return R()


def _read(question):
    client = _Echo()
    Reader(client).read(question, Retrieval(envelope={"results": []}, strategy="search_only"))
    return client.user


def test_the_reader_sees_the_question_date_only_when_the_suite_states_one():
    dated = Question("q", "q", "When?", "temporal-reasoning", "x",
                     question_date="2023/05/20 (Sat) 02:33")
    undated = Question("q", "q", "When?", "temporal-reasoning", "x")
    assert "Current date: 2023/05/20 (Sat) 02:33\nQuestion: When?" in _read(dated)
    assert _read(undated).endswith("\n\nQuestion: When?")
    assert "Current date" not in _read(undated)


def test_official_prompts_come_from_upstream_per_type():
    q = Question("q_abs", "q_abs", "Q?", "temporal-reasoning", "unanswerable",
                 abstain_expected=True)
    assert judge_prompt(q, "r").startswith("I will give you an unanswerable question")
    t = Question("t", "t", "Q?", "temporal-reasoning", "18 days")
    assert "off-by-one" in judge_prompt(t, "r")
    p = Question("p", "p", "Q?", "single-session-preference", "rubric")
    assert "rubric for desired personalized response" in judge_prompt(p, "r")
    assert answer_prompt(t, {"results": []}).endswith("Question: Q?\nAnswer (step by step):")


def test_the_official_judge_reads_yes_anywhere_as_upstream_does():
    class C:
        def __init__(self, text):
            self.text = text

        def complete(self, system, user, **kw):
            text = self.text

            class R:
                pass
            r = R()
            r.text, r.seconds, r.model = text, 0.0, "m"
            return r
    q = Question("q", "q", "Q?", "multi-session", "3")
    assert OfficialJudge(C("Yes.")).grade(q, "3").correct
    assert not OfficialJudge(C("No")).grade(q, "4").correct


def test_the_sampler_is_upstreams_seeded_stratified_draw():
    data = [{"question_id": f"{t[:2]}{i:03d}", "question_type": t}
            for t in ("temporal-reasoning", "multi-session") for i in range(20)]
    first = sample_questions_stratified(data, per_type=5, seed=42)
    assert first == sample_questions_stratified(list(reversed(data)), per_type=5, seed=42)
    assert len(first) == 10


def test_the_oracle_fix_arms_are_opt_in_and_leave_the_defaults_alone():
    from bench.core import run as harness
    from bench.core.answer import READER_DEFAULT_STANCE, build_reader_system
    assert READER_DEFAULT_STANCE == "grounded"
    facts, manifest = build_reader_system("facts")
    grounded, _ = build_reader_system("grounded")
    assert manifest["inference_stance"] == "facts" and "`facts`" in facts
    assert "`facts`" not in grounded
    arm = harness.parse_arm("llm_wide_asst-conversational:search_only:k=25")
    assert arm.extractor == "llm_wide_asst"
    assert harness.EXTRACT_PROMPTS["llm_wide"] == "extract-wide.md"
    assert harness.EXTRACT_PROMPTS["llm_wide_asst"] == "extract-wide-assistant.md"
    wide = (harness.REPO / "bench/prompts/extract-wide.md").read_text(encoding="utf-8")
    asst = (harness.REPO / "bench/prompts/extract-wide-assistant.md").read_text(encoding="utf-8")
    assert "What an AI assistant told the user" in asst and "AI assistant" not in wide


def test_a_window_over_the_prompt_budget_trims_titles_then_splits_and_merges():
    from bench.core import run as harness
    from bench.core.corpus import Session, Turn
    from bench.core.extract import ExtractWindow, LLMExtractor

    seen = []

    class Client:
        def complete(self, system, user, **kw):
            seen.append(len(user))

            class R:
                data = {"nodes": [], "edges": []}
                input_tokens, output_tokens = 1, 2
            return R()

    pack, _ = harness.load_named_pack("conversational")
    turns = tuple(Turn(speaker="user", text="word " * 3000, turn_id=f"s:{i}") for i in range(4))
    window = ExtractWindow(haystack_id="h", session=Session(session_id="s", turns=turns),
                           turns=turns, window_index=0,
                           prior_titles=tuple(f"an earlier memory title {i}" for i in range(200)))
    ext = LLMExtractor(Client(), pack, prompt_name="extract-wide.md", max_prompt_chars=20000)
    result = ext.extract(window)
    assert all(n <= 20000 for n in seen)
    assert ext.titles_trimmed > 0 and ext.windows_split > 0
    assert result.output_tokens == 2 * len(seen)

    whole = LLMExtractor(Client(), pack, prompt_name="extract-wide.md")
    calls = len(seen)
    whole.extract(window)
    assert len(seen) == calls + 1 and whole.windows_split == 0
