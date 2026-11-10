"""A completed BEAM run, graded under BEAM's official evaluation.

    python -m bench.beam_pass --run-dir runs/<run-id> --reader engraphy
    python -m bench.beam_pass --run-dir runs/<run-id> --reader official

`--reader engraphy` grades the answers the run's own reader already gave, with
upstream's rubric judge and Kendall tau-b. No reader call is made. Output in
`<run-dir>/official/`.

`--reader official` reads every question again from the run's saved retrieval
envelope under upstream's RAG answer prompt (`answer_generation_for_rag`), then
grades the same way. Output in `<run-dir>/official-reader/`.

Grading makes one judge call per rubric item (about 1,050 over the 400 questions
of the 100K split), plus, for event_ordering, one equivalence call per answer
line per still-unmatched rubric item. Both passes resume from their checkpoint
and run under the supervisor like a run. The conventions and every difference
from upstream are in `bench/core/beam_official.py` and in each pass's manifest.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from bench import offline
from bench.core import run as harness
from bench.core.beam_official import (
    CATEGORY_ORDER,
    OfficialJudge,
    OfficialReader,
    aggregate,
    conventions_manifest,
)
from bench.core.providers import ClaudeCLIClient
from bench.console import use_utf8_streams

use_utf8_streams()


def finish(ck: harness.Checkpoint, manifest: dict, title: str) -> dict:
    """Join answers and verdicts, aggregate as `report_results.py` does, write the pass."""
    verdicts = {(v["arm"], v["question_id"]): v for v in ck.rows("verdicts.jsonl")}
    results = []
    for row in ck.rows("answers.jsonl"):
        v = verdicts.get((row["arm"], row["question_id"]))
        if v is not None:
            results.append({**row, **v})
    ck.path("results.jsonl").write_text(
        "".join(json.dumps(r, default=str) + "\n" for r in results), encoding="utf-8")
    by_arm: dict[str, list[dict]] = {}
    for r in results:
        by_arm.setdefault(r["arm"], []).append(r)
    agg = {arm: aggregate(rows) for arm, rows in by_arm.items()}
    ungraded = len(ck.rows("answers.jsonl")) - len(results)
    manifest = {**manifest, "aggregate": agg, "quota_stop": False,
                "rows_answered_but_ungraded": ungraded, "finished_at": offline._now()}
    ck.path("manifest.json").write_text(json.dumps(manifest, indent=2, default=str),
                                        encoding="utf-8")
    lines = [f"# {title}", "",
             ("Scores are on 0 to 1. event_ordering is Kendall tau-b normalised to "
              "(tau + 1) / 2; every other category is the mean rubric-judge score. Each "
              "category is the mean over conversations of the per-conversation mean, as "
              "upstream's report_results.py computes it."), ""]
    for arm, a in agg.items():
        lines += [f"## `{arm}`", "", "| category | score | conversations | questions |",
                  "|---|---|---|---|"]
        for cat in CATEGORY_ORDER:
            c = a["categories"][cat]
            if c["questions"]:
                lines.append(f"| {cat} | {100 * c['score']:.1f}% | {c['conversations']} "
                             f"| {c['questions']} |")
        o = a["overall"]
        if o["score"] is not None:
            lines.append(f"| overall (unweighted mean of {o['categories']} categories, "
                         f"the harness's) | {100 * o['score']:.1f}% | {a['conversations']} "
                         f"| {a['questions']} |")
        lines.append("")
    ck.path("report.md").write_text("\n".join(lines), encoding="utf-8")
    return agg


def main() -> int:
    ap = argparse.ArgumentParser(prog="bench.beam_pass")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--reader", required=True, choices=("engraphy", "official"))
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--judge-concurrency", type=int, default=4)
    ap.add_argument("--arm", help="which arm of the run to read, when it has more than one")
    ap.add_argument("--name", help="output directory name under the run, default "
                    "'official' or 'official-reader'")
    args = ap.parse_args()

    src = offline.load_source(args.run_dir, arm=args.arm)
    if src.manifest.get("benchmark") != "beam":
        raise SystemExit("this pass grades BEAM runs only")
    name = args.name or ("official" if args.reader == "engraphy" else "official-reader")
    out = src.dir / name
    ck = harness.Checkpoint(out)
    reader_model = harness.ROLE_MODELS["reader"]["model"]
    judge_model = harness.CLAUDE_JUDGE_MODEL
    arm = f"{src.arm}/{name}"

    questions = [q for qid, q in sorted(src.questions.items()) if qid in src.envelopes]
    missing = sorted(qid for qid in src.questions if qid not in src.envelopes)
    manifest = {
        "instrument": "bench.beam_pass",
        "source_run": src.manifest.get("run_id"),
        "source_engine_git_sha": src.manifest.get("engine_git_sha"),
        "engine_git_sha": harness._git_sha(),
        "git_tree_dirty": harness._git_dirty(),
        "cli_settings_isolated": os.environ.get("ENGRAPHY_BENCH_CLI_ISOLATE_SETTINGS") == "1",
        "arm": arm,
        "dataset": src.manifest.get("dataset"),
        "dataset_licence": harness.DATASET_LICENCES["beam"],
        "haystacks": src.manifest.get("haystacks"),
        "retrieval_configs": src.manifest.get("retrieval_configs"),
        "role_models": {
            "reader": ({"provider": "claude-cli", "model": reader_model}
                       if args.reader == "official"
                       else {"source": "the run's own reader answers"}),
            "judge": {"provider": "claude-cli", "model": judge_model},
        },
        "conventions": conventions_manifest(reader=args.reader),
        "questions": len(questions),
        "rubric_items": sum(len(q.rubric) for q in questions),
        "questions_without_saved_envelope": missing,
    }
    print(f"BEAM official pass ({args.reader} reader) over "
          f"{src.manifest.get('run_id')} -> {out}")
    print(f"  {len(questions)} questions, {manifest['rubric_items']} rubric items, arm {arm}")

    todo = [{"arm": arm, "question_id": q.question_id} for q in questions]

    def read_one(item: dict) -> dict:
        q = src.questions[item["question_id"]]
        env = src.envelopes[q.question_id]
        size, digest = offline.envelope_digest(env)
        base = {"arm": arm, "question_id": q.question_id, "haystack_id": q.haystack_id,
                "category": q.category, "abstain_expected": q.abstain_expected,
                "envelope_bytes": size, "envelope_sha256": digest}
        if args.reader == "engraphy":
            row = src.source_row(q.question_id)
            if row is None:
                return {**base, "error": "no answer from the run's reader"}
            return {**base, "answer": row.get("answer") or "", "reader_model": row.get(
                "reader_model"), "source_arm": row.get("arm"), "error": ""}
        ans = OfficialReader(ClaudeCLIClient(model=reader_model)).read(q, env)
        return {**base, "answer": ans.text, "reader_output": ans.raw,
                "reader_seconds": ans.seconds, "reader_model": ans.model, "error": ans.error}

    def grade_one(row: dict):
        q = src.questions[row["question_id"]]
        return OfficialJudge(ClaudeCLIClient(model=judge_model)).grade(q, row.get("answer") or "")

    reason = offline.read_pass(ck, todo, read_one, concurrency=args.concurrency)
    if reason:
        return offline.stop(ck, manifest, reason)
    reason = offline.judge_pass(ck, grade_one, concurrency=args.judge_concurrency)
    if reason:
        return offline.stop(ck, manifest, reason)
    agg = finish(ck, manifest, f"BEAM under the official evaluation ({args.reader} reader)")
    for arm_name, a in agg.items():
        o = a["overall"]
        print(f"  {arm_name}: overall {o['score']} over {o['categories']} categories, "
              f"{a['questions']} questions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
