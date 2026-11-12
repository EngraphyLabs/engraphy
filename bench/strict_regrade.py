"""Re-grade a finished run's strict-convention answers with the strict judge, judge calls only.

    python -m bench.strict_regrade --run-dir runs/<run-id> --arm <arm> --out runs/<run-id>/strict-isolated

Every non-adversarial answer the run's reader gave is graded again, best-of-3, by the
same strict judge the run used. No reader is called: the answers are taken from the
run's own results, unchanged. Adversarial questions are graded by the fixed
abstention rule, which calls no model, so they are not re-graded. With
ENGRAPHY_BENCH_CLI_ISOLATE_SETTINGS=1 the judge sees only its own prompt; the
manifest records which. Resumes from its checkpoint.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

from bench import offline
from bench.core import run as harness
from bench.console import use_utf8_streams

use_utf8_streams()

KEEP = ("arm", "question_id", "haystack_id", "category", "abstain_expected", "answer",
        "reader_model", "envelope_bytes", "envelope_sha256")


def main() -> int:
    ap = argparse.ArgumentParser(prog="bench.strict_regrade")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--arm", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--judge-concurrency", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="only the first N answers (a check)")
    args = ap.parse_args()

    src = offline.load_source(args.run_dir, arm=args.arm)
    out = pathlib.Path(args.out)
    ck = harness.Checkpoint(out)
    rows = [json.loads(line) for line in (src.dir / "results.jsonl").read_text(
        encoding="utf-8").splitlines() if line.strip()]
    rows = [r for r in rows if r["arm"] == args.arm and r.get("graded_by") == "judge"]
    if args.limit:
        rows = rows[:args.limit]
    if not ck.path("answers.jsonl").exists():
        ck.path("answers.jsonl").write_text(
            "".join(json.dumps({k: r.get(k) for k in KEEP}) + "\n" for r in rows),
            encoding="utf-8")

    manifest = {
        "instrument": "bench.strict_regrade",
        "source_run": src.manifest.get("run_id"),
        "source_engine_git_sha": src.manifest.get("engine_git_sha"),
        "engine_git_sha": harness._git_sha(),
        "arm": args.arm,
        "dataset": src.manifest.get("dataset"),
        "questions": len(rows),
        "judge": {"convention": "strict", "model": harness.CLAUDE_JUDGE_MODEL,
                  "passes": "best-of-3, as the run graded"},
        "cli_settings_isolated": os.environ.get("ENGRAPHY_BENCH_CLI_ISOLATE_SETTINGS") == "1",
        "answers": "the run's own reader answers, unchanged; no reader is called",
    }
    print(f"strict re-grade of {src.manifest.get('run_id')} -> {out}: {len(rows)} answers")

    judge = harness._build_judge("claude")

    def grade_one(row: dict):
        return judge.grade_majority(src.questions[row["question_id"]], row.get("answer") or "")

    reason = offline.judge_pass(ck, grade_one, concurrency=args.judge_concurrency)
    if reason:
        return offline.stop(ck, manifest, reason)
    agg = offline.finish(ck, manifest, "LoCoMo strict convention, re-graded")
    for name, a in agg.items():
        b = a["overall_excl_adversarial"]
        print(f"  {name}: {b.get('correct')}/{b.get('n')} = {b.get('accuracy')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
