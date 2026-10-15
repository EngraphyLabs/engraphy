"""A completed LongMemEval run, graded under LongMemEval's official conventions.

    python -m bench.longmemeval_pass --run-dir runs/<run-id> --reader engraphy
    python -m bench.longmemeval_pass --run-dir runs/<run-id> --reader official

`--reader engraphy` grades the answers the run's own reader already gave, with the
official judge prompts: how the vendors that publish LongMemEval figures report,
their own answerer under the official judge. No reader call is made. Output in
`<run-dir>/official/`.

`--reader official` reads every question again from the run's saved retrieval
envelope under the official answer template for extracted facts, then grades
with the official judge: the analogue of LoCoMo's matched convention. Output in
`<run-dir>/official-reader/`.

Both resume from their checkpoint, and both run under the supervisor like a run.
The conventions and every difference from upstream are in
`bench/core/longmemeval_official.py` and in each pass's manifest.
"""

from __future__ import annotations

import argparse
import sys

from bench import offline
from bench.core import run as harness
from bench.core.longmemeval_official import OfficialJudge, OfficialReader, conventions_manifest
from bench.core.providers import ClaudeCLIClient
from bench.console import use_utf8_streams

use_utf8_streams()


def main() -> int:
    ap = argparse.ArgumentParser(prog="bench.longmemeval_pass")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--reader", required=True, choices=("engraphy", "official"))
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--judge-concurrency", type=int, default=4)
    ap.add_argument("--arm", help="which arm of the run to read, when it has more than one")
    args = ap.parse_args()

    src = offline.load_source(args.run_dir, arm=args.arm)
    if src.manifest.get("benchmark") != "longmemeval":
        raise SystemExit("this pass grades LongMemEval runs only")
    name = "official" if args.reader == "engraphy" else "official-reader"
    out = src.dir / name
    ck = harness.Checkpoint(out)
    reader_model = harness.ROLE_MODELS["reader"]["model"]
    judge_model = harness.CLAUDE_JUDGE_MODEL
    arm = f"{src.arm}/{name}"

    questions = [q for qid, q in sorted(src.questions.items()) if qid in src.envelopes]
    missing = sorted(qid for qid in src.questions if qid not in src.envelopes)
    manifest = {
        "instrument": "bench.longmemeval_pass",
        "source_run": src.manifest.get("run_id"),
        "source_engine_git_sha": src.manifest.get("engine_git_sha"),
        "engine_git_sha": harness._git_sha(),
        "git_tree_dirty": harness._git_dirty(),
        "arm": arm,
        "dataset": src.manifest.get("dataset"),
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
        "questions_without_saved_envelope": missing,
    }
    print(f"LongMemEval official pass ({args.reader} reader) over "
          f"{src.manifest.get('run_id')} -> {out}")
    print(f"  {len(questions)} questions, arm {arm}")

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
    agg = offline.finish(ck, manifest, f"LongMemEval under the official judge ({args.reader} reader)")
    for arm_name, a in agg.items():
        b = a["overall"]
        print(f"  {arm_name}: {b.get('correct')}/{b.get('n')} = {b.get('accuracy')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
