"""Where a LongMemEval answer was lost: extraction, retrieval or reading. No model call.

    python -m bench.longmemeval.diagnose --run-dir runs/<run-id> --out <dir>

For every question of a finished run, with its strict, official and
official-reader verdicts beside it:

- **store**: every active memory in the question's own scope, read straight from
  the database (an oracle store holds tens of memories, so all of it is read; a
  plain SELECT, so no recall statistic moves);
- **context**: the memories in the envelope the reader actually saw, from the
  run's saved `envelopes.jsonl`;
- the harness's own `gold_support` heuristic, the one LoCoMo's failure
  attribution uses, over each: does the text carry at least half of the gold
  answer's content words.

A wrong answer is staged **extraction** when the gold is not in the store,
**retrieval** when it is in the store but not in the context, and **reading** when
it was in the context. The heuristic abstains on a gold with no content words (a
number, a date), on a preference rubric, and on abstention questions; those are
`undetermined` here and are adjudicated by hand, recorded separately.

Writes `diagnosis.jsonl` (ids, categories, numbers and stages only: committable)
and `review.jsonl` (with the question, gold, answers and the store text, for the
hand adjudication: local only).
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

import psycopg

from bench import offline
from bench.core import run as harness
from bench.core.diagnostics import compact_context, context_text, gold_support
from bench.console import use_utf8_streams

use_utf8_streams()


def _node_text(title: str, body: str, attrs: dict) -> str:
    pairs = "; ".join(f"{k}: {v}" for k, v in sorted((attrs or {}).items())
                      if v not in (None, "", [], {}))
    return f"{title} {body} {pairs}".strip()


def _verdicts(path: pathlib.Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    return {r["question_id"]: r for r in harness.load_rows(path)}


def stage(correct: bool | None, abstain: bool, category: str, store: dict,
          context: dict) -> str:
    if correct:
        return "correct"
    if abstain or category == "single-session-preference":
        return "undetermined"
    gic, gis = context.get("supported"), store.get("supported")
    if gic is True:
        return "reading"
    if gic is False and gis is True:
        return "retrieval"
    if gic is False and gis is False:
        return "extraction"
    return "undetermined"


def main() -> int:
    ap = argparse.ArgumentParser(prog="bench.longmemeval.diagnose")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    src = offline.load_source(args.run_dir)
    space_id = next(iter(src.manifest["packs"].values()))["space_id"]
    extractor = src.arm.split("-", 1)[0]
    arm_space = harness.ArmSpace(space_id, "bench", extractor)
    strict = {r["question_id"]: r for r in harness.load_rows(src.dir / "results.jsonl")}
    official = _verdicts(src.dir / "official" / "results.jsonl")
    official_reader = _verdicts(src.dir / "official-reader" / "results.jsonl")

    url = os.environ.get("ENGRAPHY_TEST_DATABASE_URL")
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    diag = (out / "diagnosis.jsonl").open("w", encoding="utf-8", newline="\n")
    review = (out / "review.jsonl").open("w", encoding="utf-8", newline="\n")
    with psycopg.connect(url) as conn:
        for qid, q in sorted(src.questions.items()):
            scope = arm_space.scope_for(q.haystack_id)
            nodes = conn.execute(
                "SELECT title, body, attrs FROM nodes WHERE space_id = %s AND scope_id = %s "
                "AND status = 'active' ORDER BY created_at", (space_id, scope)).fetchall()
            store_text = "\n".join(_node_text(t, b, a) for t, b, a in nodes)
            env = src.envelopes.get(qid) or {}
            ctx_nodes = compact_context(env, max_nodes=1000, body_chars=100000)
            ctx_text = context_text(ctx_nodes)
            gis = gold_support(q.gold_answer, store_text)
            gic = gold_support(q.gold_answer, ctx_text)
            s, o, r = strict.get(qid, {}), official.get(qid, {}), official_reader.get(qid, {})
            row = {
                "question_id": qid, "category": q.category, "abstain_expected": q.abstain_expected,
                "store_memories": len(nodes), "context_memories": len(ctx_nodes),
                "gold_in_store": {k: gis.get(k) for k in ("supported", "fraction")},
                "gold_in_context": {k: gic.get(k) for k in ("supported", "fraction")},
                "strict_correct": s.get("correct"), "declined": s.get("abstained"),
                "official_correct": o.get("correct"),
                "official_reader_correct": r.get("correct"),
                "stage_official": stage(o.get("correct"), q.abstain_expected, q.category, gis, gic),
                "stage_official_reader": stage(r.get("correct"), q.abstain_expected, q.category,
                                               gis, gic),
            }
            diag.write(json.dumps(row) + "\n")
            review.write(json.dumps({**row, "question": q.text, "gold": q.gold_answer,
                                     "question_date": q.question_date,
                                     "engraphy_answer": s.get("answer"),
                                     "official_reader_answer": r.get("answer"),
                                     "store": store_text, "context_ids": [n["id"] for n in ctx_nodes]},
                                    ensure_ascii=False) + "\n")
    diag.close()
    review.close()
    print(f"diagnosed {len(src.questions)} questions -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
