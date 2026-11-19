"""One arm of a lever A/B: re-read a finished run's questions and grade them with the official judge.

    python -m bench.lever_ab --run-dir runs/<run-id> --name baseline
    python -m bench.lever_ab --run-dir runs/<run-id> --name con-prose --contract con
    python -m bench.lever_ab --run-dir runs/<run-id> --name con-json --contract con --render json

Every arm reads the same questions from the same run. An arm differs from the
baseline only in what its flags name: the reader's output contract (`--contract`),
how the envelope is shown to the reader (`--render`), and an envelope transform
applied before reading (`--transform`). The baseline is the shipped reader
(grounded stance, verify contract, prose rendering) over the run's saved
envelopes, so baseline and arm see identical memory unless a transform says
otherwise.

Every answer is graded by LongMemEval's official judge prompts (one pass, as
`bench.longmemeval_pass`), abstention questions included. Run with
ENGRAPHY_BENCH_CLI_ISOLATE_SETTINGS=1 so neither the reader nor the judge sees user
or project settings, and ENGRAPHY_BENCH_ANONYMOUS_USER=1; the manifest records both.

`--per-type N` keeps at most N questions per question type, the first N by a hash
of the question id, so the same slice is drawn on every machine. `--slice dev` or
`--slice test` keeps the questions whose id hash is even or odd: a dev half for
fixing a prompt and a disjoint half for measuring it.

Output: `runs/<run-id>/levers/<name>/`, resumable from its checkpoint. The reader's
notes or check line are kept in `reader_check`, apart from the graded `answer`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter

from bench import offline
from bench.core import run as harness
from bench.core.answer import READER_CONTRACTS, READER_STANCES, RENDERINGS, Reader
from bench.core.longmemeval_official import OfficialJudge
from bench.core.providers import ClaudeCLIClient
from bench.core.retrieve import Retrieval
from bench.console import use_utf8_streams

use_utf8_streams()

# Envelope transforms: name -> (factory(source) -> function(question, envelope) -> envelope,
# description). "none" is the identity; bench/lever_transforms.py holds the others.
def _dated(make):
    def factory(src):
        from bench.lever_transforms import SessionDates
        return make(SessionDates(harness.REPO / "datasets" / src.manifest["dataset"]["path"]))
    return factory


def _transforms() -> dict[str, tuple]:
    from bench import lever_transforms as lt
    return {
        "none": (lambda src: (lambda q, env: env), "the run's saved envelope, unchanged"),
        "date-annotate": (_dated(lt.date_annotate),
                          "each memory shows its source session's timestamp (attrs.session_date, "
                          "matched from its verbatim source turns) in place of the ingest date"),
        "date-sort": (_dated(lt.date_sort),
                      "date-annotate, with the memories ordered oldest session first"),
    }


TRANSFORMS = _transforms()


def id_hash(qid: str) -> str:
    return hashlib.sha256(qid.encode("utf-8")).hexdigest()


def select(questions: dict, per_type: int = 0, slice_: str = "all") -> list[str]:
    """Question ids, stratified by type and ordered by id hash; deterministic."""
    ids = sorted(questions, key=id_hash)
    if slice_ in ("dev", "test"):
        want = 0 if slice_ == "dev" else 1
        ids = [q for q in ids if int(id_hash(q)[-1], 16) % 2 == want]
    if per_type:
        seen: Counter = Counter()
        kept = []
        for q in ids:
            cat = questions[q].category
            if seen[cat] < per_type:
                seen[cat] += 1
                kept.append(q)
        ids = kept
    return sorted(ids)


def main() -> int:
    ap = argparse.ArgumentParser(prog="bench.lever_ab")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--arm", help="which arm of the run to read, when it has more than one")
    ap.add_argument("--envelopes", help="read this envelope file instead of the run's own")
    ap.add_argument("--contract", default="verify", choices=READER_CONTRACTS)
    ap.add_argument("--stance", default="grounded", choices=READER_STANCES)
    ap.add_argument("--render", default="prose", choices=RENDERINGS)
    ap.add_argument("--transform", default="none")
    ap.add_argument("--max-tokens", type=int, default=1000)
    ap.add_argument("--per-type", type=int, default=0)
    ap.add_argument("--slice", default="all", choices=("all", "dev", "test"))
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--judge-concurrency", type=int, default=6)
    args = ap.parse_args()
    if args.transform not in TRANSFORMS:
        raise SystemExit(f"--transform: choose from {sorted(TRANSFORMS)}")
    make_transform, transform_note = TRANSFORMS[args.transform]

    src = offline.load_source(args.run_dir, args.envelopes, arm=args.arm)
    transform = make_transform(src)
    if src.manifest.get("benchmark") != "longmemeval":
        raise SystemExit("this A/B grades LongMemEval runs only")
    out = src.dir / "levers" / args.name
    ck = harness.Checkpoint(out)
    reader_model = harness.ROLE_MODELS["reader"]["model"]
    judge_model = harness.CLAUDE_JUDGE_MODEL
    probe = Reader(None, stance=args.stance, contract=args.contract, render=args.render)
    arm = f"{src.arm}/lever-{args.name}"

    qids = [q for q in select(src.questions, args.per_type, args.slice) if q in src.envelopes]
    manifest = {
        "instrument": "bench.lever_ab",
        "name": args.name,
        "source_run": src.manifest.get("run_id"),
        "source_engine_git_sha": src.manifest.get("engine_git_sha"),
        "harness_git_sha": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                                          text=True).stdout.strip() or None,
        "git_tree_dirty": harness._git_dirty(),
        "arm": arm,
        "envelopes": src.envelope_source,
        "transform": {"name": args.transform, "note": transform_note},
        "reader": {**probe.system_manifest, "model": reader_model, "render": args.render,
                   "max_tokens": args.max_tokens,
                   "system_sha256": hashlib.sha256(probe.system.encode("utf-8")).hexdigest()},
        "judge": {"convention": "LongMemEval official judge prompts, one pass",
                  "model": judge_model},
        "selection": {"per_type": args.per_type or None, "slice": args.slice,
                      "questions": len(qids),
                      "by_type": dict(Counter(src.questions[q].category for q in qids))},
        "cli_settings_isolated": os.environ.get("ENGRAPHY_BENCH_CLI_ISOLATE_SETTINGS") == "1",
        "anonymous_user_instruction": os.environ.get("ENGRAPHY_BENCH_ANONYMOUS_USER") == "1",
    }
    if not (manifest["cli_settings_isolated"] and manifest["anonymous_user_instruction"]):
        print("warning: settings isolation or the anonymous-user instruction is off; "
              "the manifest records it", file=sys.stderr)
    print(f"lever A/B arm {args.name} over {src.manifest.get('run_id')} -> {out}")
    print(f"  {len(qids)} questions: {manifest['selection']['by_type']}")

    items = [{"arm": arm, "question_id": q} for q in qids]

    def read_one(item: dict) -> dict:
        q = src.questions[item["question_id"]]
        env = transform(q, src.envelopes[q.question_id])
        size, digest = offline.envelope_digest(env)
        reader = Reader(ClaudeCLIClient(model=reader_model), stance=args.stance,
                        contract=args.contract, render=args.render,
                        max_tokens=args.max_tokens)
        ans = reader.read(q, Retrieval(envelope=env, strategy=args.transform))
        row = {"arm": arm, "question_id": q.question_id, "haystack_id": q.haystack_id,
               "category": q.category, "abstain_expected": q.abstain_expected,
               **ans.as_dict()}
        row["envelope_bytes"], row["envelope_sha256"] = size, digest
        row["answer_parsed"] = (args.contract not in ("verify", "con")) or bool(ans.check)
        return row

    def grade_one(row: dict):
        q = src.questions[row["question_id"]]
        return OfficialJudge(ClaudeCLIClient(model=judge_model)).grade(q, row.get("answer") or "")

    reason = offline.read_pass(ck, items, read_one, concurrency=args.concurrency)
    if reason:
        return offline.stop(ck, manifest, reason)
    reason = offline.judge_pass(ck, grade_one, concurrency=args.judge_concurrency)
    if reason:
        return offline.stop(ck, manifest, reason)
    agg = offline.finish(ck, manifest, f"Lever A/B arm `{args.name}`")
    rows = harness.load_rows(ck.path("results.jsonl"))
    parsed = sum(1 for r in rows if r.get("answer_parsed"))
    print(f"  answers with a parsable ANSWER line: {parsed}/{len(rows)}")
    for name, a in agg.items():
        b = a["overall"]
        print(f"  {name}: {b.get('correct')}/{b.get('n')} = {b.get('accuracy')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
