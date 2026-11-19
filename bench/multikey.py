"""Fact-augmented multi-key indexing, on a clone of a run's store.

    python -m bench.multikey clone   --run-dir runs/<run-id>
    python -m bench.multikey augment --run-dir runs/<run-id> [--concurrency 4]
    python -m bench.multikey envelopes --run-dir runs/<run-id>

`clone` copies the run's space row for row into `<space>-mk`: the space's config,
node types, edge types and rules, principals, scopes and grants, every node under a
new id (title, body, attrs, embedding and extra_search unchanged), and every edge
remapped to the new ids. The run's own store is only read.

`augment` asks a model (`bench/prompts/multikey.md`, the judge model at low effort,
20 memories a call) for each memory's atomic facts, entities, key phrases and dates,
appends them to that memory's `extra_search` on the clone, and recomputes its
embedding through the engine's own `searchable_text` and `embed_document`. Titles and
bodies are untouched, and `extra_search` never reaches the reader's envelope. Keys
are checkpointed in `runs/<run-id>/multikey/keys.jsonl` before any update, so a stop
resumes, and the spend gate is consulted before every 25 calls when
`ENGRAPHY_SPEND_GATE` names spend_gate.py.

`envelopes` searches the clone with the run's own arm at the shipped width, writing
`runs/<run-id>/envelopes-multikey.jsonl` for `bench.lever_ab --envelopes`.
"""

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures as cf
import json
import os
import pathlib
import subprocess
import sys

import psycopg
from psycopg_pool import AsyncConnectionPool

from bench import offline
from bench.core import run as harness
from bench.core.meter import Meter
from bench.core.retrieve import SearchOnly
from bench.console import use_utf8_streams

use_utf8_streams()

BATCH = 20
PROMPT = "multikey.md"
CLONE_TABLES = ("config", "node_types", "edge_types", "edge_rules", "principals",
                "scopes", "scope_grants")


def _space(src) -> tuple[str, dict]:
    arm_cfg = src.manifest["arms"][0]
    return src.manifest["packs"][arm_cfg["pack"]]["space_id"], arm_cfg


def _cols(cur, table: str) -> list[str]:
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' "
                "AND table_name=%s AND is_generated='NEVER' AND identity_generation IS NULL "
                "ORDER BY ordinal_position", (table,))
    return [r[0] for r in cur.fetchall()]


def clone(src_space: str, dst_space: str) -> dict:
    counts = {}
    with psycopg.connect(harness.DB) as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM spaces WHERE id=%s", (dst_space,))
        if cur.fetchone()[0]:
            raise SystemExit(f"{dst_space} exists; clone refuses to overwrite it")
        cols = _cols(cur, "spaces")
        sel = ", ".join("%s" if c == "id" else c for c in cols)
        cur.execute(f"INSERT INTO spaces ({', '.join(cols)}) SELECT {sel} FROM spaces WHERE id=%s",
                    (dst_space, src_space))
        for t in CLONE_TABLES:
            cols = _cols(cur, t)
            sel = ", ".join("%s" if c == "space_id" else c for c in cols)
            cur.execute(f"INSERT INTO {t} ({', '.join(cols)}) SELECT {sel} FROM {t} "
                        "WHERE space_id=%s", (dst_space, src_space))
            counts[t] = cur.rowcount
        cur.execute("CREATE TEMP TABLE idmap AS SELECT id AS old, gen_random_uuid() AS new "
                    "FROM nodes WHERE space_id=%s", (src_space,))
        cols = _cols(cur, "nodes")
        sel = ", ".join("m.new" if c == "id" else "%s" if c == "space_id" else
                        "(SELECT m2.new FROM idmap m2 WHERE m2.old = n.canonical_id)"
                        if c == "canonical_id" else f"n.{c}" for c in cols)
        cur.execute(f"INSERT INTO nodes ({', '.join(cols)}) SELECT {sel} FROM nodes n "
                    "JOIN idmap m ON m.old = n.id", (dst_space,))
        counts["nodes"] = cur.rowcount
        cols = _cols(cur, "edges")
        node_cols = [c for c in cols if c in ("src", "dst", "src_id", "dst_id", "source_id",
                                              "target_id", "from_id", "to_id")]
        sel = ", ".join("gen_random_uuid()" if c == "id" else "%s" if c == "space_id" else
                        f"(SELECT m.new FROM idmap m WHERE m.old = e.{c})" if c in node_cols
                        else f"e.{c}" for c in cols)
        cur.execute(f"INSERT INTO edges ({', '.join(cols)}) SELECT {sel} FROM edges e "
                    "WHERE e.space_id=%s", (dst_space, src_space))
        counts["edges"] = cur.rowcount
        counts["edge_node_columns"] = node_cols
        cur.execute("SELECT (SELECT count(*) FROM nodes WHERE space_id=%s), "
                    "(SELECT count(*) FROM nodes WHERE space_id=%s)", (src_space, dst_space))
        a, b = cur.fetchone()
        if a != b:
            raise SystemExit(f"clone node count {b} differs from source {a}; rolled back")
        conn.commit()
    return counts


def _gate_ok() -> bool:
    gate = os.environ.get("ENGRAPHY_SPEND_GATE")
    if not gate:
        return True
    out = subprocess.run([sys.executable, gate, "multikey"], capture_output=True, text=True)
    return out.stdout.strip().startswith("GO")


def augment(run_dir: pathlib.Path, space: str, concurrency: int) -> dict:
    from bench.core.providers import ClaudeCLIClient
    from bench.lever_transforms import _JSON_OBJ

    prompt = (harness.REPO / "bench" / "prompts" / PROMPT).read_text(encoding="utf-8")
    out_dir = run_dir / "multikey"
    out_dir.mkdir(exist_ok=True)
    keys_path = out_dir / "keys.jsonl"
    done: dict[str, str] = {}
    if keys_path.exists():
        for line in keys_path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row["keys"]:  # an empty reply is retried, never counted as keyed
                done[row["id"]] = row["keys"]
    with psycopg.connect(harness.DB) as conn:
        rows = conn.execute("SELECT id::text, title, body, extra_search FROM nodes "
                            "WHERE space_id=%s ORDER BY id", (space,)).fetchall()
    client = ClaudeCLIClient(model=harness.CLAUDE_JUDGE_MODEL)

    def call(batch):
        listing = "\n\n".join(f"[{i}] {t}\n{b}" for i, (_, t, b, _) in enumerate(batch, 1))
        resp = client.complete(prompt, f"Memories:\n\n{listing}", max_tokens=4000, effort="low")
        m = None
        for m in _JSON_OBJ.finditer(resp.text or ""):
            pass
        got = {}
        if m:
            try:
                got = json.loads(m.group(0))
            except ValueError:
                got = {}
        return [(nid, str(got.get(str(i), "")).strip()) for i, (nid, *_rest) in
                enumerate(batch, 1)]

    stopped = None
    for attempt in range(1, 4):  # memories left without keys are retried, up to three passes
        todo = [r for r in rows if r[0] not in done]
        if not todo:
            break
        batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
        print(f"augment {space} pass {attempt}: {len(rows)} memories, {len(done)} keyed, "
              f"{len(batches)} calls to go", flush=True)
        stopped = _key_pass(batches, call, keys_path, done, concurrency)
        if stopped:
            break
    if stopped:
        print(f"stopped: {stopped}; rerun to resume")
        return {"stopped": stopped, "keyed": len(done), "memories": len(rows)}
    return _apply(rows, done, space, out_dir)


def _key_pass(batches, call, keys_path, done, concurrency):
    from bench.core.providers import QuotaExhausted
    stopped = None
    with keys_path.open("a", encoding="utf-8", newline="\n") as sink, \
            cf.ThreadPoolExecutor(max_workers=concurrency) as pool:
        for start in range(0, len(batches), 25):
            if not _gate_ok():
                stopped = "spend gate HALT"
                break
            try:
                for result in pool.map(call, batches[start:start + 25]):
                    for nid, k in result:
                        sink.write(json.dumps({"id": nid, "keys": k}) + "\n")
                        if k:
                            done[nid] = k
                    sink.flush()
            except QuotaExhausted as exc:
                stopped = f"quota: {exc}"[:200]
                break
            print(f"  {min(len(batches), start + 25)}/{len(batches)} calls", flush=True)
    return stopped


def _apply(rows, done, space, out_dir):
    from engraphy.core import embedding
    from engraphy.core.dedup import _vector_literal
    embedding.load_model()
    updated = empty = 0
    with psycopg.connect(harness.DB) as conn:
        cur = conn.cursor()
        for nid, title, body, extra in rows:
            k = done.get(nid, "")
            if not k:
                empty += 1
                continue
            base = (extra or "").split("\n[multikey]\n")[0]
            new_extra = f"{base}\n[multikey]\n{k}" if base else f"[multikey]\n{k}"
            if new_extra == (extra or ""):
                continue
            vec = embedding.embed_document(embedding.searchable_text(title, body, new_extra))
            cur.execute("UPDATE nodes SET extra_search=%s, embedding=%s::vector, "
                        "embedding_model=%s WHERE id=%s",
                        (new_extra, _vector_literal(vec), embedding.MODEL_STAMP, nid))
            updated += 1
        conn.commit()
    summary = {"space": space, "memories": len(rows), "updated": updated,
               "without_keys": empty, "without_keys_share": round(empty / max(1, len(rows)), 4),
               "prompt": PROMPT, "batch": BATCH, "model": harness.CLAUDE_JUDGE_MODEL}
    (out_dir / "augment.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


async def envelopes(run_dir: pathlib.Path, src, space: str, arm_cfg: dict) -> dict:
    from engraphy.core import embedding
    embedding.load_model()
    arm_space = harness.ArmSpace(space, "bench", arm_cfg["extractor"])
    k = arm_cfg.get("k") or 25
    out = run_dir / "envelopes-multikey.jsonl"
    pool = AsyncConnectionPool(harness.APP_DB, open=False, min_size=1, max_size=2)
    await pool.open()
    n = 0
    try:
        with out.open("w", encoding="utf-8", newline="\n") as sink:
            for qid in sorted(src.envelopes):
                got = await SearchOnly(limit=k).retrieve(pool, arm_space, src.questions[qid],
                                                         Meter())
                sink.write(json.dumps({"arm": f"{src.arm}/multikey", "question_id": qid,
                                       "envelope": got.envelope}) + "\n")
                n += 1
    finally:
        await pool.close()
    return {"space": space, "k": k, "questions": n, "out": str(out)}


def main() -> int:
    ap = argparse.ArgumentParser(prog="bench.multikey")
    ap.add_argument("command", choices=("clone", "augment", "envelopes"))
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()
    run_dir = offline.resolve_run_dir(args.run_dir)
    src = offline.load_source(run_dir)
    space, arm_cfg = _space(src)
    clone_space = f"{space}-mk"
    if args.command == "clone":
        print(json.dumps(clone(space, clone_space), indent=2))
    elif args.command == "augment":
        summary = augment(run_dir, clone_space, args.concurrency)
        print(json.dumps(summary, indent=2))
        if summary.get("stopped"):
            return 3
        # more than 2% of memories without keys after three passes: not a fair arm
        return 5 if summary.get("without_keys_share", 0) > 0.02 else 0
    else:
        print(json.dumps(asyncio.run(envelopes(run_dir, src, clone_space, arm_cfg)), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
