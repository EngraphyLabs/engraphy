"""The question subset a LongMemEval run covers, chosen by a sampler this repository did not write.

    python -m bench.longmemeval.sample --per-type 10 [--dataset ...]

Reproduces `sample_questions_stratified` from `mem0ai/memory-benchmarks` at
`4b61c5d31b9c668a12b4f5e78064248a02c82d2b`, `benchmarks/longmemeval/run.py`,
statement for statement: group by question type, sort each group by question
id, then for each type in sorted order draw `per_type` with one
`random.Random(seed)` shared across types. Seed 42 is upstream's default. The
oracle and S files carry the same 500 question ids and types, so the subset is
the same in either file. Prints the ids comma-separated, the form `--haystacks`
takes, since each LongMemEval question is its own haystack.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict

QUESTION_TYPES = [
    "temporal-reasoning",
    "multi-session",
    "knowledge-update",
    "single-session-user",
    "single-session-assistant",
    "single-session-preference",
]


def sample_questions_stratified(questions, per_type=5, seed=42, selected_types=None):
    type_filter = set(selected_types) if selected_types else set(QUESTION_TYPES)
    groups = defaultdict(list)
    for q in questions:
        if q["question_type"] in type_filter:
            groups[q["question_type"]].append(q)
    for qtype in groups:
        groups[qtype].sort(key=lambda q: q["question_id"])
    rng = random.Random(seed)
    sampled = []
    for qtype in sorted(groups.keys()):
        group = groups[qtype]
        n = min(per_type, len(group))
        sampled.extend(rng.sample(group, n))
    sampled.sort(key=lambda q: q["question_id"])
    return sampled


def main() -> int:
    ap = argparse.ArgumentParser(prog="bench.longmemeval.sample")
    ap.add_argument("--dataset", default="datasets/longmemeval/longmemeval_oracle.json")
    ap.add_argument("--per-type", type=int, required=True)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    with open(args.dataset, encoding="utf-8") as fh:
        data = json.load(fh)
    picked = sample_questions_stratified(data, per_type=args.per_type, seed=args.seed)
    print(",".join(q["question_id"] for q in picked))
    return 0


if __name__ == "__main__":
    sys.exit(main())
