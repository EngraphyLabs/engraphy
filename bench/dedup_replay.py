"""Paired dedup-threshold replay of a finished run's store, and the judged pair sample.

    python -m bench.dedup_replay replay  --src <space> --suffix r86 --t-low 0.86
    python -m bench.dedup_replay compare --run-dir runs/<run-id> --a <space> --b <space>
    python -m bench.dedup_replay judge   --spaces <space> <space> --out <file> [--seed N]

`replay` copies the source space without its nodes (the space row, config, node types,
edge types and rules, principals, scopes and grants) into `<src>-<suffix>`, sets
`dedup.t_low` there with the same per-space config row `engraphy-admin config set`
writes, and writes every source node again in its original order (`created_at`, then
id) through the harness's own `_write_draft` with `AlwaysDistinct`. Each write reuses
the node's stored embedding, so two replays at different thresholds see identical
vectors and differ only in the band each write lands in. The source space is only
read. It reports the confirm-band count per arm (scope suffix) in the replay and in
the source's own `dedup_log`.

`compare` searches every question of a run at k 25 over two replayed spaces and counts
the questions whose result lists differ in node content or order.

`judge` draws confirm-band pairs from the source spaces' `dedup_log` (each `pending`
row joined to the node its resolution wrote), uniformly within the similarity strata
[0.80, 0.86), [0.86, 0.89) and [0.89, 0.95), and resolves each with the harness's
`LLMAdjudicate`: the incoming memory against its banded candidate. Decisions are
checkpointed in `--out`, so a stop resumes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import random
import sys
from collections import Counter

import psycopg
from psycopg_pool import AsyncConnectionPool

from bench.console import use_utf8_streams
from bench.core import ingest
from bench.core import run as harness
from bench.core.extract import NodeDraft
from bench.core.space import RunSpace, _apply_space_config

use_utf8_streams()

CLONE_TABLES = ("config", "node_types", "edge_types", "edge_rules", "principals",
                "scopes", "scope_grants")
STRATA = {"[0.80, 0.86)": (0.80, 0.86, 150), "[0.86, 0.89)": (0.86, 0.89, 100),
          "[0.89, 0.95)": (0.89, 0.95, 50)}


def _cols(cur, table: str) -> list[str]:
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' "
                "AND table_name=%s AND is_generated='NEVER' AND identity_generation IS NULL "
                "ORDER BY ordinal_position", (table,))
    return [r[0] for r in cur.fetchall()]


def _arm(scope_id: str) -> str:
    return "llm_wide" if scope_id.endswith("-llm-wide") else "llm"


def clone_empty(src: str, dst: str, t_low: float) -> None:
    with psycopg.connect(harness.DB) as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM spaces WHERE id=%s", (dst,))
        if cur.fetchone()[0]:
            raise SystemExit(f"{dst} exists; replay refuses to write into it")
        cols = _cols(cur, "spaces")
        sel = ", ".join("%s" if c == "id" else c for c in cols)
        cur.execute(f"INSERT INTO spaces ({', '.join(cols)}) SELECT {sel} FROM spaces WHERE id=%s",
                    (dst, src))
        for t in CLONE_TABLES:
            cols = _cols(cur, t)
            sel = ", ".join("%s" if c == "space_id" else c for c in cols)
            cur.execute(f"INSERT INTO {t} ({', '.join(cols)}) SELECT {sel} FROM {t} "
                        "WHERE space_id=%s", (dst, src))
        _apply_space_config(cur, dst, {"dedup.t_low": t_low})
        conn.commit()


def _source_counts(src: str) -> dict:
    """Confirm-band rows in the source's own log, attributed to the arm of the
    candidate's scope, as in analysis/2026-10-13-dedup-band-retune.md."""
    with psycopg.connect(harness.DB) as conn:
        rows = conn.execute(
            "SELECT n.scope_id FROM dedup_log l JOIN nodes n ON n.id = l.candidate_id "
            "WHERE l.space_id=%s AND l.band='pending'", (src,)).fetchall()
    return dict(Counter(_arm(r[0]) for r in rows))


async def replay(src: str, suffix: str, t_low: float) -> dict:
    dst = f"{src}-{suffix}"
    clone_empty(src, dst, t_low)
    with psycopg.connect(harness.DB) as conn:
        nodes = conn.execute(
            "SELECT id::text, type, scope_id, title, body, attrs, source_session, "
            "embedding::text, extra_search FROM nodes WHERE space_id=%s "
            "ORDER BY created_at, id", (src,)).fetchall()
    run_space = RunSpace(space_id=dst, principal="bench", pack_name="", pack_version=0)
    pool = AsyncConnectionPool(harness.APP_DB, open=False, min_size=1, max_size=2)
    await pool.open()
    drafts: Counter = Counter()
    extra_mismatch = 0
    stats = ingest.IngestStats(haystack_id=dst)
    surface: dict = {}
    try:
        for i, (nid, ntype, scope, title, body, attrs, sess, vec, extra) in enumerate(nodes, 1):
            vector = json.loads(vec)
            draft = NodeDraft(local_id=nid, node_type=ntype, title=title, body=body,
                              attrs=attrs or {})
            if ntype not in surface:
                surface[ntype] = await ingest.dedup.resolve_attr_surface(pool, dst, ntype)
            keys, on = surface[ntype]
            if (ingest.embedding.render_attr_surface(attrs or {}, keys) if on else "") != (extra or ""):
                extra_mismatch += 1
            await ingest._write_draft(pool, run_space, scope, draft, {}, {},
                                      ingest.AlwaysDistinct(), stats,
                                      embed_document=lambda _text, v=vector: v,
                                      session_id=sess or "", surface_cache=surface)
            drafts[_arm(scope)] += 1
            if i % 500 == 0:
                print(f"  {dst}: {i}/{len(nodes)}", flush=True)
    finally:
        await pool.close()
    with psycopg.connect(harness.DB) as conn:
        cfg = conn.execute("SELECT value FROM config WHERE space_id=%s AND key='dedup.t_low'",
                           (dst,)).fetchone()
    replayed = _source_counts(dst)
    return {"source": src, "replay": dst, "t_low_config_row": cfg[0] if cfg else None,
            "nodes_replayed": len(nodes), "drafts_per_arm": dict(drafts),
            "confirm_band_per_arm": replayed,
            "confirm_band_rate_per_arm": {a: round(replayed.get(a, 0) / n, 4)
                                          for a, n in drafts.items()},
            "source_confirm_band_per_arm": _source_counts(src),
            "write_errors": dict(stats.write_errors),
            "attr_surface_mismatches": extra_mismatch}


async def compare(run_dir: pathlib.Path, a: str, b: str, arm: str | None = None,
                  only: list[str] | None = None, save: str | None = None) -> dict:
    from bench import offline
    from engraphy.core import embedding
    from engraphy.core import search as search_core
    embedding.load_model()
    src = offline.load_source(run_dir, arm=arm)
    arm_cfg = next(x for x in src.manifest["arms"] if x["arm_id"] == src.arm)
    extractor = arm_cfg["extractor"]
    pool = AsyncConnectionPool(harness.APP_DB, open=False, min_size=1, max_size=2)
    await pool.open()
    differ: list[str] = []
    same_set = 0
    empty = 0
    sizes = []
    try:
        for qid in sorted(src.envelopes):
            if only and qid not in only:
                continue
            q = src.questions[qid]
            scope = harness.ArmSpace(a, "bench", extractor).scope_for(q.haystack_id)
            got = []
            for tag, space in (("a", a), ("b", b)):
                env = await search_core.search(pool, space, "bench", scope, q.text,
                                               ingest.SOURCE_CLIENT, limit=25)
                if save:
                    with (run_dir / f"{save}-{tag}.jsonl").open("a", encoding="utf-8",
                                                                newline="\n") as sink:
                        sink.write(json.dumps({"arm": src.arm, "question_id": qid,
                                               "envelope": env}) + "\n")
                got.append([(r["node"]["title"], r["node"]["body"]) for r in env["results"]])
            sizes.append(len(got[0]))
            if not got[0]:
                empty += 1
            if got[0] != got[1]:
                differ.append(qid)
                if set(got[0]) == set(got[1]):
                    same_set += 1
    finally:
        await pool.close()
    return {"run": run_dir.name, "arm": src.arm, "a": a, "b": b,
            "questions": len(src.envelopes), "questions_with_no_results": empty,
            "mean_results": round(sum(sizes) / max(1, len(sizes)), 2),
            "questions_differing": len(differ), "of_which_same_set_reordered": same_set,
            "differing_ids": differ}


PAIRS_SQL = """
with d as (
  select l.*, row_number() over (partition by space_id order by created_at, id) rn
  from dedup_log l where space_id = any(%s)
)
select d.space_id, d.similarity, r.node_id::text, d.candidate_id::text,
       n.type, n.title, n.body, c.title, c.body
from d join d r on d.band = 'pending' and r.space_id = d.space_id and r.rn = d.rn + 1
           and r.band = 'insert' and r.candidate_id = d.candidate_id
           and r.similarity = d.similarity
join nodes n on n.id = r.node_id join nodes c on c.id = d.candidate_id
order by d.space_id, d.rn
"""


def judge(spaces: list[str], out: pathlib.Path, seed: int) -> dict:
    with psycopg.connect(harness.DB) as conn:
        pairs = conn.execute(PAIRS_SQL, (spaces,)).fetchall()
    rng = random.Random(seed)
    sample = []
    for name, (lo, hi, n) in STRATA.items():
        pool = [p for p in pairs if lo <= p[1] < hi]
        for p in rng.sample(pool, min(n, len(pool))):
            sample.append((name, p))
    done = {}
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            done[row["node_id"]] = row
    adj = ingest.LLMAdjudicate(harness._client_for("adjudicator", "claude-cli"))
    gate = harness.REPO.parent / "engraphy-benchmarks" / "ops" / "usage-watchdog"
    sys.path.insert(0, str(gate))
    import spend_gate  # noqa: E402
    with out.open("a", encoding="utf-8", newline="\n") as sink:
        for i, (stratum, (space, sim, nid, cid, ntype, nt, nb, ct, cb)) in enumerate(sample):
            if nid in done:
                continue
            if i % 25 == 0 and spend_gate.check_gate(agent="dedup-judge").decision != "GO":
                return {"stopped": "spend gate HALT", "judged": len(done)}
            draft = NodeDraft(local_id=nid, node_type=ntype, title=nt, body=nb)
            envelope = {"candidates": [{"id": cid, "similarity": round(float(sim), 4),
                                        "title": ct, "body": cb}]}
            d = adj.decide(envelope, draft)
            row = {"stratum": stratum, "space": space, "similarity": round(float(sim), 4),
                   "node_id": nid, "candidate_id": cid, "resolution": d.resolution,
                   "reason": d.reason}
            sink.write(json.dumps(row) + "\n")
            sink.flush()
            done[nid] = row
    tally: dict = {}
    for name in STRATA:
        rows = [r for r in done.values() if r["stratum"] == name]
        tally[name] = {"judged": len(rows),
                       "merge": sum(1 for r in rows if r["resolution"] == "merge")}
    return {"pairs_available": len(pairs), "seed": seed, "fallbacks": adj.fallbacks,
            "strata": tally}


def main() -> int:
    ap = argparse.ArgumentParser(prog="bench.dedup_replay")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("replay")
    p.add_argument("--src", required=True)
    p.add_argument("--suffix", required=True)
    p.add_argument("--t-low", type=float, required=True)
    p = sub.add_parser("compare")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--a", required=True)
    p.add_argument("--b", required=True)
    p.add_argument("--arm")
    p.add_argument("--only", nargs="*")
    p.add_argument("--save", help="write each arm's envelopes to <run-dir>/<save>-a|b.jsonl")
    p = sub.add_parser("judge")
    p.add_argument("--spaces", nargs="+", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--seed", type=int, default=20261126)
    args = ap.parse_args()
    if args.cmd == "replay":
        from engraphy.core import embedding
        embedding.load_model()
        out = asyncio.run(replay(args.src, args.suffix, args.t_low))
    elif args.cmd == "compare":
        from bench import offline
        out = asyncio.run(compare(offline.resolve_run_dir(args.run_dir), args.a, args.b, args.arm,
                                 args.only, args.save))
    else:
        out = judge(args.spaces, pathlib.Path(args.out), args.seed)
    print(json.dumps(out, indent=2))
    return 0 if not out.get("stopped") else 3


if __name__ == "__main__":
    sys.exit(main())
