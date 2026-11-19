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
