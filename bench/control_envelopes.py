"""The envelope-size control for the assistant-turns extractor.

    python -m bench.control_envelopes --base runs/lme-oracle-n30-a \
        --asst runs/lme-oracle-fix-asst-a

For each question, the base run's saved envelope plus only the memories of the
assistant-turns run's envelope whose verbatim source is wholly assistant turns
(`speaker_of` = "the assistant"). The combined list is ordered by search score, highest
first, so each memory keeps the place its score gives it, and cut to the
shipped width of 25, the most the assistant-turns arm could return. Memories drawn from user
turns, or from both speakers, come only from the base store, so the reader sees the
baseline's user facts unchanged plus the assistant's statements: the envelope grows as
it does in the assistant-turns arm, without changing which user memories it holds.

Writes `<base>/envelopes-b0-plus-asst.jsonl` and `<base>/envelopes-b0-plus-asst.check.json`.
Reads only saved envelopes; no store, model or network call.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
from collections import defaultdict

from bench import offline
from bench.lever_transforms import speaker_of

NAME = "envelopes-b0-plus-asst"
WIDTH = 25


def _load(run_dir: pathlib.Path) -> tuple[str, dict]:
    rows = [json.loads(line) for line in
            (run_dir / "envelopes.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    arms = {r["arm"] for r in rows}
    if len(arms) != 1:
        raise SystemExit(f"{run_dir}: expected one arm, found {sorted(arms)}")
    return arms.pop(), {r["question_id"]: r["envelope"] for r in rows}


def combine(base_env: dict, asst_env: dict) -> tuple[dict, int]:
    base_items = list(base_env.get("results") or [])
    bodies = {(it.get("node") or {}).get("body") for it in base_items}
    extra = [it for it in (asst_env.get("results") or [])
             if speaker_of(it.get("node") or {}) == "the assistant"
             and (it.get("node") or {}).get("body") not in bodies]
    merged = sorted(base_items + extra, key=lambda it: -(it.get("score") or 0.0))[:WIDTH]
    env = dict(base_env)
    env["results"] = merged
    return env, sum(1 for it in merged if it in extra)


def main() -> int:
    ap = argparse.ArgumentParser(prog="bench.control_envelopes")
    ap.add_argument("--base", required=True)
    ap.add_argument("--asst", required=True)
    args = ap.parse_args()
    base_dir = offline.resolve_run_dir(args.base)
    asst_dir = offline.resolve_run_dir(args.asst)
    base_arm, base = _load(base_dir)
    _, asst = _load(asst_dir)
    src = offline.load_source(base_dir)
    by_type: dict[str, list] = defaultdict(list)
    out = base_dir / f"{NAME}.jsonl"
    with out.open("w", encoding="utf-8", newline="\n") as sink:
        for qid in sorted(base):
            if qid not in asst:
                raise SystemExit(f"{qid} has no assistant-turns envelope")
            env, added = combine(base[qid], asst[qid])
            sink.write(json.dumps({"arm": f"{base_arm}/b0-plus-asst", "question_id": qid,
                                   "envelope": env}) + "\n")
            qtype = src.questions[qid].category
            by_type[qtype].append((len(base[qid].get("results") or []),
                                   len(asst[qid].get("results") or []), len(env["results"]), added))
    summary = {"base_run": base_dir.name, "asst_run": asst_dir.name, "questions": len(base),
               "per_type_median": {t: {"base": statistics.median(v[0] for v in rows),
                                       "asst_scoped": statistics.median(v[1] for v in rows),
                                       "control": statistics.median(v[2] for v in rows),
                                       "added": statistics.median(v[3] for v in rows)}
                                   for t, rows in sorted(by_type.items())},
               "added_total": sum(v[3] for rows in by_type.values() for v in rows)}
    (base_dir / f"{NAME}.check.json").write_text(json.dumps(summary, indent=2) + "\n",
                                                 encoding="utf-8", newline="\n")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
