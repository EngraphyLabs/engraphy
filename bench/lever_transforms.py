"""Envelope transforms for the lever A/B (bench.lever_ab --transform).

A transform takes a question and the run's saved retrieval envelope and returns the
envelope the reader sees. It never reads the gold answer, the question's type, or
any evidence pointer.

**Session dates.** Each memory the extractor writes keeps the verbatim turns it was
drawn from (`Source (verbatim):` in its body). Those turns are matched exactly
against the question's haystack in the dataset to find the session they came from,
and so the session's timestamp. The match uses only the memory's own text and the
haystack the question already owns; it is the date a deployment would carry as the
memory's write time, where the benchmark's ingest instead stamps the run date.
"""

from __future__ import annotations

import copy
import json
import re
from functools import lru_cache

SOURCE_MARK = "Source (verbatim):"
_TURN_SPLIT = re.compile(r"\n(?=(?:user|assistant): )")
_ROLE = re.compile(r"^(user|assistant): ")


class SessionDates:
    """Memory -> session timestamp, from the dataset's haystack."""

    def __init__(self, dataset_path) -> None:
        data = json.loads(open(dataset_path, encoding="utf-8").read())
        self._q = {q["question_id"]: q for q in data}

    @lru_cache(maxsize=64)
    def _index(self, qid: str) -> dict[str, frozenset]:
        q = self._q[qid]
        idx: dict[str, set] = {}
        for sess, date in zip(q["haystack_sessions"], q["haystack_dates"]):
            for t in sess:
                c = (t.get("content") or "").strip()
                if c:
                    idx.setdefault(c, set()).add(date)
        return {k: frozenset(v) for k, v in idx.items()}

    def dates_of(self, qid: str, node: dict) -> list[str]:
        body = node.get("body") or ""
        if SOURCE_MARK not in body or qid not in self._q:
            return []
        idx = self._index(qid)
        found: set = set()
        for t in _TURN_SPLIT.split(body.split(SOURCE_MARK, 1)[1].strip()):
            c = _ROLE.sub("", t.strip()).strip()
            if not c:
                continue
            if c in idx:
                found |= idx[c]
            else:
                head = c[:200]
                for k, v in idx.items():
                    if k.startswith(head):
                        found |= v
                        break
        return sorted(found)


def _nodes(env: dict) -> list[dict]:
    return [it.get("node", it) for it in (env.get("results") or []) if isinstance(it, dict)]


def date_annotate(dates: SessionDates):
    """Each memory carries its session's timestamp in place of the ingest date."""
    def fn(q, env):
        env = copy.deepcopy(env)
        for node in _nodes(env):
            ds = dates.dates_of(q.question_id, node)
            node.pop("created_at", None)
            if ds:
                node["attrs"] = {**(node.get("attrs") or {}),
                                 "session_date": ds[-1] if len(ds) == 1 else " / ".join(ds)}
        return env
    return fn


def date_sort(dates: SessionDates):
    """date_annotate, then the memories ordered oldest session first; undated last, by rank."""
    annotate = date_annotate(dates)

    def fn(q, env):
        env = annotate(q, env)
        results = env.get("results") or []

        def key(pair):
            rank, it = pair
            node = it.get("node", it) if isinstance(it, dict) else {}
            d = ((node.get("attrs") or {}).get("session_date") or "").split(" / ")[0]
            return (0, d, rank) if d else (1, "", rank)

        env["results"] = [it for _, it in sorted(enumerate(results), key=key)]
        env["ordering"] = "ordered by session date, oldest first"
        return env
    return fn


def coverage(dates: SessionDates, q, env) -> tuple[int, int]:
    """(memories dated, memories) for one envelope: a check reported in the manifest."""
    nodes = _nodes(env)
    return sum(1 for n in nodes if dates.dates_of(q.question_id, n)), len(nodes)


# ---- lever 3: rerank the retrieved set, then keep an adaptive number of memories

RERANK_PROMPT = "rerank.md"
_JSON_OBJ = re.compile(r"\{[^{}]*\}", re.S)


def parse_scores(text: str, n: int) -> dict[int, int] | None:
    """{rank: score} for ranks 1..n from the reranker's reply, or None if unusable."""
    m = None
    for m in _JSON_OBJ.finditer(text or ""):
        pass
    if m is None:
        return None
    try:
        raw = json.loads(m.group(0))
    except ValueError:
        return None
    scores = {}
    for k, v in raw.items():
        try:
            rank, score = int(k), int(v)
        except (TypeError, ValueError):
            continue
        if 1 <= rank <= n:
            scores[rank] = max(0, min(3, score))
    return scores if len(scores) >= max(1, n // 2) else None


def rerank_adaptive(client, prompt_text: str, *, threshold: int = 2, keep_min: int = 5,
                    keep_max: int = 25, effort: str = "low"):
    """Score every retrieved memory 0-3 for the question with one model call, then keep
    those at or above `threshold`, ordered by score then original rank, at least
    `keep_min` and at most `keep_max`. An unusable reply keeps the first `keep_max` in
    the original order and is recorded."""
    from bench.core.answer import _node_block

    def fn(q, env):
        env = copy.deepcopy(env)
        results = env.get("results") or []
        n = len(results)
        listing = "\n\n".join(
            f"[{i}] {_node_block(it.get('node', it))}" for i, it in enumerate(results, start=1))
        asked = f"Date asked: {q.question_date}\n" if getattr(q, "question_date", None) else ""
        user = f"Question: {q.text}\n{asked}\nMemories:\n\n{listing}"
        info = {"lever": "rerank-adaptive", "retrieved": n, "threshold": threshold,
                "keep_min": keep_min, "keep_max": keep_max}
        try:
            resp = client.complete(prompt_text, user, max_tokens=2000, effort=effort)
            scores = parse_scores(resp.text, n)
            info["rerank_output_tokens"] = getattr(resp, "output_tokens", 0)
        except Exception as exc:  # a quota stop must still stop the run
            from bench.core.providers import QuotaExhausted
            if isinstance(exc, QuotaExhausted):
                raise
            scores, info["error"] = None, f"{type(exc).__name__}: {exc}"[:200]
        if scores is None:
            info["fallback"] = "unusable reranker reply; original order kept"
            env["results"] = results[:keep_max]
        else:
            order = sorted(range(1, n + 1), key=lambda r: (-scores.get(r, 0), r))
            kept = [r for r in order if scores.get(r, 0) >= threshold][:keep_max]
            if len(kept) < keep_min:
                kept = order[:min(keep_min, n)]
            env["results"] = [results[r - 1] for r in kept]
            info["scores"] = {str(r): scores.get(r, 0) for r in range(1, n + 1)}
        info["kept"] = len(env["results"])
        env["_lever"] = info
        return env
    return fn
