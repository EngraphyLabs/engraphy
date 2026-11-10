"""BEAM's official evaluation: the rubric judge, Kendall tau-b, and the aggregation, from upstream.

Source, pinned: `mohammadtavakoli78/BEAM` at
`b2da22eac88bb0874c64665f13457eb99835774a`, MIT. The upstream files are vendored
byte for byte under `bench/prompts/beam/` with their sha256 hashes:

- `prompts.upstream.py` (`src/prompts.py`): `unified_llm_judge_base_prompt`, the
  one judge prompt every category uses, and `answer_generation_for_rag`, the
  answer prompt of upstream's RAG baselines. Both are read out of the file's
  syntax tree, not retyped.
- `compute_metrics.upstream.py` (`src/evaluation/compute_metrics.py`): the ten
  `evaluate_<category>` functions, `parse_json_response`, `llm_equivalence`,
  `align_with_llm` and `event_ordering_score` are compiled from that file and
  called as is. Its module body is never executed (it imports nltk, scipy and
  sentence-transformers and downloads nltk data at import).
- `run_evaluation.upstream.py` (`src/evaluation/run_evaluation.py`): which
  evaluator grades which category, and the judge it is given.
- `report_results.upstream.py` (`src/evaluation/report_results.py`): how the
  per-question scores become the reported table. Reproduced in `aggregate` and
  checked against the compiled upstream function by test.
- `llm.upstream.py` (`src/llm.py`): the judge model, `gpt-4.1-mini` at
  temperature 0.
- `LICENSE.upstream`: the MIT licence.

The grading convention, as upstream runs it:

- **Per rubric item, one judge call.** The prompt is
  `unified_llm_judge_base_prompt` with `<rubric_item>` and `<llm_response>`
  replaced, sent as the whole user turn with no system prompt. The reply is JSON
  `{"score": 1.0 | 0.5 | 0.0, "reason": ...}`. The question's
  `llm_judge_score` is the mean over its rubric items.
- **event_ordering** additionally splits the response on newlines, aligns each
  line to a rubric item with a binary LLM equivalence call (`llm_equivalence`,
  a system and a user message, "yes" anywhere in the lower-cased reply means
  equivalent), and scores the order with Kendall tau-b, normalised to
  `(tau + 1) / 2`. The reported score for the category is that `tau_norm`; the
  rubric judge score, precision, recall, F1 and `final_score = tau_norm * f1`
  are computed and stored but not reported.
- **Aggregation** (`report_results.py`): per conversation, the mean score of
  each category; then, per category, the mean over conversations. Upstream
  reports the ten category figures and no overall. The `overall` here is the
  unweighted mean of the ten category figures, labelled as the harness's.

Differences from upstream, recorded in every manifest by `conventions_manifest`:

- the judge is the run's Claude judge model through the Claude Code CLI, not
  `gpt-4.1-mini` at temperature 0; every judge reply is stored;
- `scipy.stats.kendalltau(variant="b")` is replaced by `kendall_tau_b`, the same
  statistic in pure Python (tested against hand-computed cases), returning no
  p-value, which upstream does not use;
- `json_repair.repair_json`, upstream's fallback when a judge reply is not JSON,
  is replaced by `repair_json`, which recovers a `"score"` number from the reply
  or fails; a failure is a judge error, retried on resume, where upstream would
  abort the whole conversation;
- a question is graded alone and checkpointed, where upstream grades a whole
  conversation file in one pass.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import pathlib
import re
import time
from dataclasses import dataclass, field
from typing import List, Tuple  # noqa: UP035 -- upstream's annotations name these

from bench.core.answer import render_envelope
from bench.core.corpus import Question
from bench.core.judge import Verdict
from bench.core.llm import LLMError
from bench.core.providers import AuthExpired, QuotaExhausted
from bench.core.reference import ReferenceAnswer

__all__ = [
    "CATEGORY_ORDER",
    "EVALUATORS",
    "OFFICIAL_SOURCE",
    "BeamVerdict",
    "OfficialJudge",
    "OfficialReader",
    "aggregate",
    "answer_prompt",
    "conventions_manifest",
    "judge_prompt",
    "kendall_tau_b",
    "parse_json_response",
    "repair_json",
]

OFFICIAL_SOURCE = {
    "repository": "https://github.com/mohammadtavakoli78/BEAM",
    "commit": "b2da22eac88bb0874c64665f13457eb99835774a",
    "files": ["src/prompts.py", "src/evaluation/compute_metrics.py",
              "src/evaluation/run_evaluation.py", "src/evaluation/report_results.py",
              "src/llm.py", "LICENSE"],
    "license": "MIT",
    "upstream_judge": "gpt-4.1-mini, temperature 0 (src/llm.py, run_evaluation.py __main__)",
}

PROMPT_DIR = pathlib.Path(__file__).resolve().parents[1] / "prompts" / "beam"
_PROMPTS = PROMPT_DIR / "prompts.upstream.py"
_METRICS = PROMPT_DIR / "compute_metrics.upstream.py"
_REPORT = PROMPT_DIR / "report_results.upstream.py"
# Hashes of the files as fetched from the pinned commit (`git cat-file blob`, and
# `git hash-object` of each equals upstream's blob id). A mismatch means the
# vendored copy is not upstream's, and nothing should be graded with it.
_EXPECTED = {
    "prompts.upstream.py": "81fe51ab2bcd7e8ad218b3a9a969a340c38d31f90ac2d93fa78a3bb890ba157b",
    "compute_metrics.upstream.py":
        "e72bf25d36d521d166862063ec94f8e7875328193142d1edddb9b4677d47780d",
    "run_evaluation.upstream.py":
        "518894ab65860043a24259457ddf3b1ab8ae18427b6e01e5aa90cd29c4256a3b",
    "report_results.upstream.py":
        "1486a44fac721717c0a9373b32f4487c277d2bf28044cf28f649a6a74e692c96",
    "llm.upstream.py": "1ce32f8bf67935fc0fcf169963cce79b5f7ee4e63e25baf72b174c497d364c54",
    "LICENSE.upstream": "60c7b74ffaacaae383c3f06a48bc79d204b25bc9c1856df9711b3d290febd474",
}

# report_results.py's column order. Its per-file scores follow the evaluation
# file's key order, which is this order for every BEAM file.
CATEGORY_ORDER = ("abstention", "contradiction_resolution", "event_ordering",
                  "information_extraction", "instruction_following", "knowledge_update",
                  "multi_session_reasoning", "preference_following", "summarization",
                  "temporal_reasoning")
EVENT_ORDERING = "event_ordering"


def _source(path: pathlib.Path) -> str:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != _EXPECTED[path.name]:
        raise RuntimeError(f"{path.name}: sha256 {digest} is not the pinned upstream file")
    return raw.decode("utf-8")


def verify_vendored() -> dict[str, str]:
    """Check every vendored file against its pin; returns name -> sha256."""
    for name in _EXPECTED:
        _source(PROMPT_DIR / name)
    return {name: f"sha256:{h}" for name, h in _EXPECTED.items()}


def _string_constants(path: pathlib.Path, names: tuple[str, ...]) -> dict[str, str]:
    """Module-level `name = "..."` assignments, read from the syntax tree."""
    found: dict[str, str] = {}
    for node in ast.parse(_source(path)).body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id in names
                and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
            found[node.targets[0].id] = node.value.value
    missing = set(names) - set(found)
    if missing:
        raise RuntimeError(f"{path.name}: no string constant {sorted(missing)}")
    return found


def _compile_functions(path: pathlib.Path, names: tuple[str, ...], namespace: dict) -> dict:
    """Compile the named top-level functions alone, into `namespace`."""
    tree = ast.parse(_source(path))
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    missing = set(names) - {f.name for f in fns}
    if missing:
        raise RuntimeError(f"{path.name}: no function {sorted(missing)}")
    exec(compile(ast.Module(body=fns, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


def kendall_tau_b(x, y, variant: str = "b", method: str = "auto"):
    """`scipy.stats.kendalltau(x, y, variant="b")` in pure Python: (tau_b, None).

    tau_b = (concordant - discordant) / sqrt((n0 - n1) * (n0 - n2)), with n0 the
    number of pairs and n1, n2 the pairs tied in x and in y. NaN when either
    side is constant, as scipy returns. The p-value is not computed; upstream
    discards it.
    """
    if variant != "b":
        raise ValueError("only tau-b is reproduced")
    if len(x) != len(y):
        raise ValueError("x and y differ in length")
    concordant = discordant = tied_x = tied_y = pairs = 0
    n = len(x)
    for i in range(n):
        for j in range(i + 1, n):
            pairs += 1
            dx, dy = x[i] - x[j], y[i] - y[j]
            if dx == 0:
                tied_x += 1
            if dy == 0:
                tied_y += 1
            if dx and dy:
                if (dx > 0) == (dy > 0):
                    concordant += 1
                else:
                    discordant += 1
    denom = math.sqrt((pairs - tied_x) * (pairs - tied_y))
    if denom == 0:
        return float("nan"), None
    return (concordant - discordant) / denom, None


_SCORE = re.compile(r'"?score"?\s*[:=]\s*"?\s*(-?\d+(?:\.\d+)?)')
_REASON = re.compile(r'"?reason"?\s*[:=]\s*"(.*)', re.DOTALL)


def repair_json(text: str) -> str:
    """Stand-in for `json_repair.repair_json` on a judge reply upstream could not
    parse: recover the score (and the reason, if present) as JSON, or raise."""
    m = _SCORE.search(text or "")
    if not m:
        raise ValueError(f"judge reply has no score: {(text or '')[:200]!r}")
    reason = _REASON.search(text)
    return json.dumps({"score": float(m.group(1)),
                       "reason": reason.group(1).rstrip('"} \n') if reason else ""})


_CONSTANTS = _string_constants(_PROMPTS, ("unified_llm_judge_base_prompt",
                                          "answer_generation_for_rag"))
JUDGE_TEMPLATE = _CONSTANTS["unified_llm_judge_base_prompt"]
ANSWER_TEMPLATE = _CONSTANTS["answer_generation_for_rag"]

_UPSTREAM = _compile_functions(
    _METRICS,
    ("parse_json_response", "llm_equivalence", "align_with_llm", "event_ordering_score",
     *(f"evaluate_{c}" for c in CATEGORY_ORDER)),
    {"json": json, "re": re, "List": List, "Tuple": Tuple,  # noqa: UP006
     "kendalltau": kendall_tau_b, "repair_json": repair_json,
     "unified_llm_judge_base_prompt": JUDGE_TEMPLATE},
)
parse_json_response = _UPSTREAM["parse_json_response"]
event_ordering_score = _UPSTREAM["event_ordering_score"]
EVALUATORS = {c: _UPSTREAM[f"evaluate_{c}"] for c in CATEGORY_ORDER}


def judge_prompt(rubric_item: str, response: str) -> str:
    """The judge prompt for one rubric item, rendered as upstream renders it."""
    return (JUDGE_TEMPLATE.replace("<rubric_item>", rubric_item)
            .replace("<llm_response>", response))


def answer_prompt(question: Question, envelope: dict) -> str:
    """Upstream's RAG answer prompt, Engraphy's rendered memories as the context."""
    return (ANSWER_TEMPLATE.replace("<context>", render_envelope(envelope))
            .replace("<question>", question.text))


class _Reply:
    __slots__ = ("content",)

    def __init__(self, content: str) -> None:
        self.content = content


class UpstreamModel:
    """The `.invoke(...).content` surface upstream's evaluators call, over the
    harness client. A string is one user turn with no system prompt; a message
    list (`llm_equivalence`) becomes its system and user turns. No schema is
    passed, so a long response travels on stdin on the CLI route."""

    def __init__(self, client) -> None:
        self.client = client
        self.calls = 0
        self.seconds = 0.0
        self.model = ""

    def invoke(self, prompt) -> _Reply:
        if isinstance(prompt, str):
            system, user = "", prompt
        else:
            system = "\n".join(m["content"] for m in prompt if m["role"] == "system")
            user = "\n".join(m["content"] for m in prompt if m["role"] != "system")
        resp = self.client.complete(system, user)
        self.calls += 1
        self.seconds += resp.seconds or 0.0
        self.model = resp.model or self.model
        return _Reply(resp.text or "")


@dataclass(frozen=True, slots=True)
class BeamVerdict(Verdict):
    """One question's official grade. `score` is the figure upstream reports for
    it: `tau_norm` for event_ordering, the rubric mean otherwise. `correct` is
    full marks, kept only because a `Verdict` carries one; the BEAM figure is
    the score."""

    score: float = 0.0
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {**Verdict.as_dict(self), "score": self.score, **self.detail}


class OfficialJudge:
    """Grade one answer with upstream's evaluator for its category."""

    def __init__(self, client) -> None:
        self.client = client

    def grade(self, question: Question, response: str) -> BeamVerdict:
        model = UpstreamModel(self.client)
        started = time.perf_counter()
        try:
            result = EVALUATORS[question.category](
                rubric=list(question.rubric), llm_response=response,
                probing_question=question.text, model=model)
        except (QuotaExhausted, AuthExpired):
            raise
        except (LLMError, ValueError, KeyError, TypeError) as exc:
            return BeamVerdict(correct=False, graded_by="judge_error",
                               error=f"{type(exc).__name__}: {exc}"[:300])
        score = float(result["tau_norm"] if question.category == EVENT_ORDERING
                      else result["llm_judge_score"])
        detail = {k: v for k, v in result.items()}
        detail["judge_calls"] = model.calls
        return BeamVerdict(
            correct=score >= 1.0, reason=json.dumps(result.get("llm_judge_responses"))[:400],
            graded_by="beam_official_judge", model=model.model,
            seconds=model.seconds or (time.perf_counter() - started),
            score=score, detail=detail)


class OfficialReader:
    """Upstream's RAG answer prompt as the user turn, an empty system prompt."""

    def __init__(self, client) -> None:
        self.client = client

    def read(self, question: Question, envelope: dict) -> ReferenceAnswer:
        started = time.perf_counter()
        try:
            resp = self.client.complete("", answer_prompt(question, envelope))
        except (QuotaExhausted, AuthExpired):
            raise
        except LLMError as exc:
            return ReferenceAnswer(question.question_id, "", "", 0.0,
                                   error=f"{type(exc).__name__}: {exc}"[:400])
        raw = (resp.text or "").strip()
        return ReferenceAnswer(question.question_id, raw, raw,
                               resp.seconds or (time.perf_counter() - started), resp.model)


def aggregate(rows: list[dict]) -> dict:
    """`report_results.py` over graded rows: per conversation, the mean score of
    each category; per category, the mean over conversations. Each row carries
    `haystack_id`, `category` and `score`.

    Upstream divides by every conversation that has an evaluation file; a
    conversation missing a category here is left out of that category's mean
    and counted in `conversations`.
    """
    per_conv: dict[str, dict[str, list[float]]] = {}
    for r in rows:
        per_conv.setdefault(r["haystack_id"], {}).setdefault(r["category"], []).append(
            float(r["score"]))
    categories: dict[str, dict] = {}
    for cat in CATEGORY_ORDER:
        means = [sum(v[cat]) / len(v[cat]) for v in per_conv.values() if v.get(cat)]
        n = sum(len(v.get(cat, ())) for v in per_conv.values())
        categories[cat] = {"score": (sum(means) / len(means)) if means else None,
                           "conversations": len(means), "questions": n}
    scored = [c["score"] for c in categories.values() if c["score"] is not None]
    return {
        "categories": categories,
        "overall": {
            "score": (sum(scored) / len(scored)) if scored else None,
            "categories": len(scored),
            "note": "the harness's unweighted mean of the category figures; "
                    "upstream reports the ten categories and no overall",
        },
        "conversations": len(per_conv),
        "questions": len(rows),
    }


def compile_upstream_report(pd):
    """Upstream's `report_llms_accuracies`, compiled alone, for the test that
    checks `aggregate` against it. `pd` stands in for pandas."""
    import os

    return _compile_functions(_REPORT, ("report_llms_accuracies",),
                              {"os": os, "json": json, "pd": pd})["report_llms_accuracies"]


def conventions_manifest(*, reader: str) -> dict:
    """Everything a reader of an official-convention BEAM figure needs to check it."""
    return {
        "label": "graded under BEAM's official rubric judge and Kendall tau-b",
        "source": OFFICIAL_SOURCE,
        "vendored": verify_vendored(),
        "reader": reader,
        "judge_prompt_sha256": "sha256:" + hashlib.sha256(JUDGE_TEMPLATE.encode()).hexdigest(),
        "answer_prompt_sha256": ("sha256:" + hashlib.sha256(ANSWER_TEMPLATE.encode()).hexdigest()
                                 if reader == "official" else None),
        "reproduced": [
            ("evaluate_<category>, parse_json_response, llm_equivalence, align_with_llm and "
             "event_ordering_score compiled from the pinned compute_metrics.py and called as is"),
            ("one judge call per rubric item with unified_llm_judge_base_prompt as the whole "
             "user turn, no system prompt; the question score is the mean over rubric items"),
            ("event_ordering: the response split on newlines, each line aligned by an LLM "
             "equivalence call, Kendall tau-b normalised to (tau + 1) / 2; tau_norm is the "
             "reported score, as report_results.py reads it"),
            ("aggregation: per conversation the category mean, then the mean over "
             "conversations, as report_results.py computes it"),
        ],
        "differences": [
            ("judge is the run's Claude judge model through the Claude Code CLI, not "
             "gpt-4.1-mini at temperature 0; every judge reply is stored"),
            "scipy.stats.kendalltau(variant='b') replaced by a pure-Python tau-b; no p-value",
            ("json_repair.repair_json replaced by a score-recovering stand-in; an unparseable "
             "reply is a judge error retried on resume, where upstream aborts the conversation"),
            "questions are graded one at a time and checkpointed",
            ("the overall figure is the harness's unweighted mean of the category figures; "
             "upstream reports no overall"),
            ("the judge prompt refers to 'the QUESTION' but upstream fills no question into "
             "it; reproduced as is"),
            "memories are Engraphy's, retrieved by Engraphy's search at the run's width",
            ("answers are Engraphy's shipped reader, unchanged: the text after ANSWER:, "
             "which event_ordering splits on newlines as upstream does"
             if reader == "engraphy" else
             "answers use upstream's answer_generation_for_rag prompt with Engraphy's "
             "rendered memories as the context"),
        ],
    }
