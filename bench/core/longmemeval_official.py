"""LongMemEval's official conventions: the judge prompts and the answer prompt, from upstream.

Engraphy's strict figure is the one `bench.core.run` produces: the shipped reader,
which may decline, and the strict judge, best of 3. Published LongMemEval figures
come from each vendor's own answerer graded by the official judge. This module
reproduces the official side from its source, so that a figure can be reported
under those conventions beside the strict one.

Source, pinned: `xiaowu0162/LongMemEval` at
`9e0b455f4ef0e2ab8f2e582289761153549043fc`, MIT. The two upstream files are
vendored byte for byte under `bench/prompts/longmemeval/` with their hashes:

- `evaluate_qa.upstream.py`: `get_anscheck_prompt` is compiled from that file and
  called as is, so the judge prompts are upstream's own code, not a retyping.
  One prompt per question type, the abstention prompt for `_abs` questions.
- `run_generation.upstream.py`: the answer template for a store of extracted
  facts, answering step by step, read out of that file's syntax tree.

Reproduced from the source:

- the judge prompt is the whole user turn, with no system prompt;
- the verdict is correct when `yes` appears anywhere in the reply, lower-cased,
  exactly as `evaluate_qa.py` reads it;
- one judge pass;
- every question counts, abstention included, in the overall figure.

What cannot be reproduced, recorded in every manifest as a difference:

- the judge is the run's Claude judge model, not `gpt-4o-2024-08-06`, and it is
  reached through the Claude Code CLI, where upstream sets temperature 0 and a
  10-token limit. The upstream reading rule ("yes" anywhere) is kept, and every
  raw reply is stored so a reader can apply a stricter one;
- the memories are Engraphy's, retrieved by Engraphy's search at the run's width,
  and they fill the template's history slot in Engraphy's own rendering.
"""

from __future__ import annotations

import ast
import hashlib
import pathlib
import time

from bench.core.answer import render_envelope
from bench.core.corpus import Question
from bench.core.judge import Verdict
from bench.core.llm import LLMError
from bench.core.providers import QuotaExhausted
from bench.core.reference import ReferenceAnswer

__all__ = [
    "OFFICIAL_SOURCE",
    "OfficialJudge",
    "OfficialReader",
    "answer_prompt",
    "conventions_manifest",
    "judge_prompt",
]

OFFICIAL_SOURCE = {
    "repository": "https://github.com/xiaowu0162/LongMemEval",
    "commit": "9e0b455f4ef0e2ab8f2e582289761153549043fc",
    "files": ["src/evaluation/evaluate_qa.py", "src/generation/run_generation.py"],
    "license": "MIT",
}

PROMPT_DIR = pathlib.Path(__file__).resolve().parents[1] / "prompts" / "longmemeval"
_EVALUATE = PROMPT_DIR / "evaluate_qa.upstream.py"
_GENERATE = PROMPT_DIR / "run_generation.upstream.py"
# Hashes of the files as fetched from the pinned commit. A mismatch means the
# vendored copy is not upstream's, and nothing should be graded with it.
_EXPECTED = {
    _EVALUATE.name: "ecce9c4c79dc89d99534ac17b383a5cbb5b9f0c69ee98adaf0684742e3d95251",
    _GENERATE.name: "4f1eb3c69d7ad40f04065b9c0bc86f6582441018fc6ff751d162d66c95baf672",
}


def _source(path: pathlib.Path) -> str:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != _EXPECTED[path.name]:
        raise RuntimeError(f"{path.name}: sha256 {digest} is not the pinned upstream file")
    return raw.decode("utf-8")


def _load_anscheck():
    """Compile upstream's `get_anscheck_prompt` alone. It uses no import, so it
    runs without upstream's OpenAI dependencies."""
    tree = ast.parse(_source(_EVALUATE))
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "get_anscheck_prompt")
    namespace: dict = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(_EVALUATE), "exec"), namespace)
    return namespace["get_anscheck_prompt"]


def _load_facts_template() -> str:
    """The `run_generation.py` template for extracted facts, answered step by step:
    the string literal beginning "I will give you several facts extracted"
    that asks to answer step by step."""
    tree = ast.parse(_source(_GENERATE))
    found = [n.value for n in ast.walk(tree)
             if isinstance(n, ast.Constant) and isinstance(n.value, str)
             and n.value.startswith("I will give you several facts extracted from history chats")
             and "step by step" in n.value]
    if len(found) != 1:
        raise RuntimeError(f"expected one facts step-by-step template, found {len(found)}")
    return found[0]


get_anscheck_prompt = _load_anscheck()
FACTS_TEMPLATE = _load_facts_template()


def judge_prompt(question: Question, response: str) -> str:
    return get_anscheck_prompt(question.category, question.text, question.gold_answer,
                               response, abstention=question.abstain_expected)


def answer_prompt(question: Question, envelope: dict) -> str:
    return FACTS_TEMPLATE.format(render_envelope(envelope), question.question_date or "",
                                 question.text)


class OfficialReader:
    """The official answer template as the user turn, an empty system prompt."""

    def __init__(self, client) -> None:
        self.client = client

    def read(self, question: Question, envelope: dict) -> ReferenceAnswer:
        started = time.perf_counter()
        try:
            resp = self.client.complete("", answer_prompt(question, envelope))
        except QuotaExhausted:
            raise
        except LLMError as exc:
            return ReferenceAnswer(question.question_id, "", "", 0.0,
                                   error=f"{type(exc).__name__}: {exc}"[:400])
        raw = (resp.text or "").strip()
        # Upstream judges the whole generation, reasoning included.
        return ReferenceAnswer(question.question_id, raw, raw,
                               resp.seconds or (time.perf_counter() - started), resp.model)


class OfficialJudge:
    """One call, the upstream prompt as the user turn; correct iff `yes` is in the reply."""

    def __init__(self, client) -> None:
        self.client = client

    def grade(self, question: Question, response: str) -> Verdict:
        try:
            resp = self.client.complete("", judge_prompt(question, response))
        except QuotaExhausted:
            raise
        except LLMError as exc:
            return Verdict(correct=False, graded_by="judge_error",
                           error=f"{type(exc).__name__}: {exc}"[:300])
        reply = (resp.text or "").strip()
        return Verdict(correct="yes" in reply.lower(), reason=reply[:400],
                       graded_by="longmemeval_official_judge", model=resp.model,
                       seconds=resp.seconds)


def _sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def conventions_manifest(*, reader: str) -> dict:
    """Everything a reader of an official-convention figure needs to check it."""
    return {
        "label": "graded under LongMemEval's official judge prompts",
        "source": OFFICIAL_SOURCE,
        "vendored": {name: f"sha256:{h}" for name, h in _EXPECTED.items()},
        "reader": reader,
        "answer_template_sha256": _sha(FACTS_TEMPLATE) if reader == "official" else None,
        "reproduced": [
            "judge prompts: upstream get_anscheck_prompt, compiled from the pinned file "
            "and called as is, one prompt per question type, abstention prompt for _abs",
            "judge prompt is the whole user turn, no system prompt",
            "correct iff 'yes' appears in the lower-cased reply, as evaluate_qa.py reads it",
            "one judge pass; every question counts, abstention included",
        ],
        "differences": [
            "judge is the run's Claude judge model through the Claude Code CLI, not "
            "gpt-4o-2024-08-06 at temperature 0 with a 10-token limit; raw replies stored",
            "memories are Engraphy's, retrieved by Engraphy's search at the run's width",
            ("answers are Engraphy's shipped reader, unchanged, as vendors report with "
             "their own answerer" if reader == "engraphy" else
             "answers use the official facts template, step by step, with Engraphy's "
             "rendered memories in the history slot"),
        ],
    }
