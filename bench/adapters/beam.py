"""BEAM loader: shim (design/09 §Interface 1).

BEAM (Tavakoli et al., "Beyond a Million Tokens", `Mohammadta/BEAM` on Hugging
Face, CC BY-SA 4.0) holds long synthetic user-assistant conversations, each with
20 probing questions over ten memory abilities. The 100K split is 20
conversations and 400 questions.

Input. `--dataset datasets/beam/100K.parquet`. The parquet is the pinned file
whose sha256 the run manifest records; it is read through its sibling
`rows_*.json` files, the Hugging Face datasets-server pages of the same split (5
rows each), because the harness does not depend on pyarrow. A directory holding
those pages is accepted too. Every page's sha256 is recorded in the corpus notes.

Lowering, matched to upstream (`mohammadtavakoli78/BEAM`,
`src/beam/download_dataset.py` and `src/answer_probing_questions/`):

- **One haystack per conversation**, id = `conversation_id` ("1" to "20"), the
  directory name upstream uses under `chats/100K/`.
- **One session per chat batch**, in file order, `session_1` onwards. Each batch
  opens with the one turn carrying a `time_anchor` ("March-15-2024"), which is
  the session's timestamp, verbatim. The anchors are checked to run forward in
  time; a file that does not is refused rather than re-sorted.
- **Turn text is the `content` field, verbatim.** User turns that open a topic
  end in a ` ->-> 1,1` style marker (batch, plan bullet). Upstream keeps it: its
  `chat.json` content carries it, and both of its answer paths (long context and
  RAG) pass the content unchanged to the model. So it is kept here, and counted.
- `turn_id` is the turn's `id`, the number `source_chat_ids` cite, so a
  question's evidence names turns in the store's own provenance.
- **One question per probing question**, id `<conversation>:<category>:<index>`,
  where index is its position in that category's list: the `(key, index)` pair
  upstream's grader uses to find the rubric. `category` is BEAM's key verbatim.
  The rubric is carried on `Question.rubric`; on Hugging Face it is a Python
  repr inside the repr of `probing_questions`, parsed with `ast.literal_eval`,
  and it equals upstream's `chats/100K/<id>/probing_questions.json` for all 20
  conversations.
- `gold_answer` is the category's reference field (`GOLD_FIELD`); it is what
  the harness's own judge compares against. The official figure grades the
  rubric instead (`bench/core/beam_official.py`).
- `abstain_expected` is set for the `abstention` category.
- `question_date` is None: upstream shows the answering model no date.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from bench.core.corpus import (
    ROLE_ASSISTANT,
    ROLE_USER,
    Corpus,
    CorpusError,
    Haystack,
    Question,
    Session,
    Turn,
    require,
)

__all__ = ["CATEGORIES", "GOLD_FIELD", "BeamLoader"]

# The reference answer per category. The field name differs by category in the
# dataset; instruction and preference following have no answer, only the
# compliance the response should show.
GOLD_FIELD: dict[str, str] = {
    "abstention": "ideal_response",
    "contradiction_resolution": "ideal_answer",
    "event_ordering": "answer",
    "information_extraction": "answer",
    "instruction_following": "expected_compliance",
    "knowledge_update": "answer",
    "multi_session_reasoning": "answer",
    "preference_following": "expected_compliance",
    "summarization": "ideal_summary",
    "temporal_reasoning": "answer",
}
CATEGORIES: frozenset[str] = frozenset(GOLD_FIELD)
ABSTENTION = "abstention"

_ROWS = re.compile(r"^rows_(\d+)\.json$")
_MARKER = re.compile(r"\s->->\s*[^\s]+\s*$")


class BeamLoader:
    """`BenchmarkLoader` for BEAM. One conversation -> one haystack."""

    name = "beam"

    def load(self, path: Path) -> Corpus:
        rows, files = _read_rows(path)
        haystacks: list[Haystack] = []
        questions: list[Question] = []
        markers = {"user": 0, "assistant": 0}
        dangling: list[str] = []

        for row in rows:
            cid = str(require(row, "conversation_id", where="row"))
            where = f"conversation {cid}"
            sessions, turn_ids = _sessions(row, where=where, markers=markers)
            haystacks.append(Haystack(haystack_id=cid, sessions=sessions))
            probing = _probing(row, where=where)
            for category in sorted(probing):
                for index, item in enumerate(probing[category]):
                    qwhere = f"{where}.{category}[{index}]"
                    evidence = tuple(str(i) for i in _ids(item.get("source_chat_ids")))
                    dangling += [f"{cid}:{e}" for e in evidence if e not in turn_ids]
                    questions.append(Question(
                        question_id=f"{cid}:{category}:{index}",
                        haystack_id=cid,
                        text=str(require(item, "question", where=qwhere)),
                        category=category,
                        gold_answer=str(require(item, GOLD_FIELD[category], where=qwhere)),
                        evidence=evidence,
                        abstain_expected=category == ABSTENTION,
                        question_date=None,
                        rubric=_rubric(require(item, "rubric", where=qwhere), where=qwhere),
                    ))

        return Corpus(
            name="beam",
            haystacks=tuple(haystacks),
            questions=tuple(questions),
            notes={
                "source": "Mohammadta/BEAM, read from Hugging Face datasets-server pages",
                "rows_files": files,
                "session_unit": "one chat batch per session, timestamp = its time_anchor",
                "turn_text": "content verbatim, upstream's ' ->-> batch,bullet' markers kept",
                "turns_with_marker": markers,
                "question_date": None,
                "evidence_ids_not_in_conversation": dangling,
            },
        ).validate()


def _read_rows(path: Path) -> tuple[list[dict], dict[str, str]]:
    folder = path.parent if path.is_file() else path
    if not folder.is_dir():
        raise CorpusError(f"dataset path not found: {path}")
    pages = sorted((int(m.group(1)), p) for p in folder.iterdir()
                   if (m := _ROWS.match(p.name)))
    if not pages:
        raise CorpusError(f"{folder}: no rows_<offset>.json pages beside the dataset")
    rows: list[tuple[int, dict]] = []
    files: dict[str, str] = {}
    total = None
    for _, page in pages:
        raw = page.read_bytes()
        files[page.name] = "sha256:" + hashlib.sha256(raw).hexdigest()
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CorpusError(f"{page}: not valid UTF-8 JSON: {exc}") from exc
        total = require(data, "num_rows_total", where=page.name) if total is None else total
        for entry in require(data, "rows", where=page.name):
            rows.append((int(require(entry, "row_idx", where=page.name)),
                         require(entry, "row", where=page.name)))
    rows.sort(key=lambda r: r[0])
    if [i for i, _ in rows] != list(range(total)):
        raise CorpusError(f"{folder}: pages hold rows {[i for i, _ in rows]}, "
                          f"not 0 to {total - 1}; a page is missing or repeated")
    return [r for _, r in rows], files


def _sessions(row: dict, *, where: str, markers: dict) -> tuple[tuple[Session, ...], set[str]]:
    chat = require(row, "chat", where=where)
    if not isinstance(chat, list) or not chat:
        raise CorpusError(f"{where}: 'chat' is not a non-empty list")
    sessions: list[Session] = []
    turn_ids: set[str] = set()
    previous: datetime | None = None
    for n, batch in enumerate(chat, start=1):
        bwhere = f"{where}.chat[{n - 1}]"
        anchors = [t.get("time_anchor") for t in batch if t.get("time_anchor")]
        if len(set(anchors)) != 1:
            raise CorpusError(f"{bwhere}: expected one time_anchor, found {anchors}")
        anchor = anchors[0]
        when = _parse_anchor(anchor, where=bwhere)
        if previous is not None and when < previous:
            raise CorpusError(f"{bwhere}: time_anchor {anchor} runs backwards")
        previous = when
        turns: list[Turn] = []
        for t in batch:
            role = str(require(t, "role", where=bwhere))
            if role not in (ROLE_USER, ROLE_ASSISTANT):
                raise CorpusError(f"{bwhere}: unknown role {role!r}")
            text = require(t, "content", where=bwhere)
            if not isinstance(text, str):
                raise CorpusError(f"{bwhere}: content is not a string")
            if _MARKER.search(text):
                markers[role] += 1
            turn_id = str(require(t, "id", where=bwhere))
            if turn_id in turn_ids:
                raise CorpusError(f"{bwhere}: turn id {turn_id} repeats")
            turn_ids.add(turn_id)
            turns.append(Turn(speaker=role, text=text, turn_id=turn_id, timestamp=anchor))
        sessions.append(Session(session_id=f"session_{n}", turns=tuple(turns), timestamp=anchor))
    return tuple(sessions), turn_ids


def _parse_anchor(value: str, *, where: str) -> datetime:
    """'March-15-2024' -> datetime."""
    try:
        return datetime.strptime(value, "%B-%d-%Y")  # noqa: DTZ007 -- a date, no zone
    except ValueError as exc:
        raise CorpusError(f"{where}: time_anchor {value!r} is not Month-DD-YYYY") from exc


def _probing(row: dict, *, where: str) -> dict[str, list[dict]]:
    raw = require(row, "probing_questions", where=where)
    try:
        probing = ast.literal_eval(raw) if isinstance(raw, str) else raw
    except (ValueError, SyntaxError) as exc:
        raise CorpusError(f"{where}: probing_questions is not a Python literal: {exc}") from exc
    if not isinstance(probing, dict) or set(probing) != CATEGORIES:
        raise CorpusError(f"{where}: probing_questions keys {sorted(probing)} are not "
                          f"the ten BEAM categories {sorted(CATEGORIES)}")
    return probing


def _rubric(value: object, *, where: str) -> tuple[str, ...]:
    items = ast.literal_eval(value) if isinstance(value, str) else value
    if not isinstance(items, list) or not items or not all(isinstance(i, str) for i in items):
        raise CorpusError(f"{where}: rubric is not a non-empty list of strings")
    return tuple(items)


def _ids(value: object) -> list[int]:
    """`source_chat_ids` is a list, a dict of lists, or absent (abstention)."""
    if value is None:
        return []
    if isinstance(value, str):
        value = ast.literal_eval(value)
    if isinstance(value, dict):
        return [i for v in value.values() for i in _ids(v)]
    if isinstance(value, (list, tuple)):
        return [i for v in value for i in (_ids(v) if isinstance(v, (list, tuple, dict)) else [v])]
    return [value]
