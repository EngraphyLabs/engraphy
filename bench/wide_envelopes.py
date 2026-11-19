"""Save wider retrieval envelopes for a finished LongMemEval run, from its kept store. No LLM.

    python -m bench.wide_envelopes --run-dir runs/<run-id> --k 50

Every question the run saved an envelope for is searched again over the run's own
store with the run's own arm, at width k, through the same `SearchOnly` path the run
used. Output: `runs/<run-id>/envelopes-k<k>.jsonl`, the shape `bench.lever_ab
--envelopes` reads.

**Stability check.** The first `run k` results of each wider search are compared with
the envelope the run saved. Hybrid search over an unchanged store is deterministic up
to recall statistics, so a match means the wider envelope extends the run's own
retrieval; the counts are printed and written beside the file.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys

from psycopg_pool import AsyncConnectionPool

from bench.core import run as harness
from bench.core.meter import Meter
from bench.core.retrieve import SearchOnly
from engraphy.core import embedding
from bench import offline
from bench.console import use_utf8_streams

use_utf8_streams()


def _ids(env: dict) -> list:
    return [(r.get("node") or r).get("id") for r in env.get("results") or []]


async def widen(run_dir: pathlib.Path, k: int) -> dict:
    src = offline.load_source(run_dir)
    manifest = src.manifest
    arm_cfg = manifest["arms"][0] if len(manifest["arms"]) == 1 else None
    if arm_cfg is None:
        raise SystemExit("the run has more than one arm; this tool reads single-arm runs")
    space_id = manifest["packs"][arm_cfg["pack"]]["space_id"]
    arm_space = harness.ArmSpace(space_id, "bench", arm_cfg["extractor"])
    run_k = arm_cfg.get("k") or 10
    out = run_dir / f"envelopes-k{k}.jsonl"
    embedding.load_model()
    same = prefix_differs = 0
    pool = AsyncConnectionPool(harness.APP_DB, open=False, min_size=1, max_size=2)
    await pool.open()
    try:
        with out.open("w", encoding="utf-8", newline="\n") as sink:
            for i, qid in enumerate(sorted(src.envelopes), 1):
                q = src.questions[qid]
                got = await SearchOnly(limit=k).retrieve(pool, arm_space, q, Meter())
                if _ids(got.envelope)[:run_k] == _ids(src.envelopes[qid]):
                    same += 1
                else:
                    prefix_differs += 1
                sink.write(json.dumps({"arm": f"{src.arm}/k{k}", "question_id": qid,
                                       "envelope": got.envelope}) + "\n")
                if i % 25 == 0:
                    print(f"  {i}/{len(src.envelopes)}", flush=True)
    finally:
        await pool.close()
    check = {"run_dir": str(run_dir), "space_id": space_id, "extractor": arm_cfg["extractor"],
             "k": k, "run_k": run_k, "questions": same + prefix_differs,
             "first_run_k_match_saved": same, "first_run_k_differ": prefix_differs}
    (run_dir / f"envelopes-k{k}.check.json").write_text(json.dumps(check, indent=2) + "\n",
                                                       encoding="utf-8")
    return check


def main() -> int:
    ap = argparse.ArgumentParser(prog="bench.wide_envelopes")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--k", type=int, default=50)
    args = ap.parse_args()
    check = asyncio.run(widen(offline.resolve_run_dir(args.run_dir), args.k))
    print(json.dumps(check, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
