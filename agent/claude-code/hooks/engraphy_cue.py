"""Engraphy's Claude Code hooks: the cue that makes memory part of the work.

Two events, one file, no network:

    python engraphy_cue.py session-start   name the scope, state the pre-work contract
    python engraphy_cue.py prompt          once per session, hand the agent its hint

The hooks inject context; the agent makes the calls. That division is
deliberate. A hook that called `briefing` itself would need a second copy of
the bearer token outside the MCP client's configuration, and it would put
recalled memory into the transcript through a path the MCP server never sees.
Injecting the contract instead keeps one credential, one code path and one
place where recalled content enters a session: the agent's own tool calls.

`session-start` fires before the first user message exists, so it can only name
the scope and the contract. `prompt` fires on the first substantive message and
carries the text that makes a good `hint`, which is the half of the briefing
that returns task-relevant memory. A marker file under the state directory
keeps "once per session" true across these separate processes.

FAIL SILENT, ALWAYS. Every path exits 0. A hook that cannot resolve a scope,
cannot read its event, or cannot write its marker prints nothing and gets out
of the way: the session continues, and the instructions file still carries the
protocol.

Usage is documented in docs/08-memory-in-your-coding-agent.md; the settings
snippet beside this file is what Claude Code reads.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys

#: Repository scope ids are `code-<repo>`, the convention the instruction block
#: and skills/coding-memory-protocol.md both name, so an agent can derive the
#: scope from the checkout and confirm it against `scope_list`.
SCOPE_PREFIX = "code-"
#: scopes.id is CHECK (id ~ '^[a-z0-9][a-z0-9-]{1,62}$') in the engine schema.
MAX_SCOPE_ID = 63
#: Enough of the first message to make a useful hint, short enough to stay out
#: of the way of the message itself.
MAX_HINT = 500
#: Below this a first message is a greeting or a slash command, not a task.
MIN_TASK_CHARS = 12


def state_dir() -> pathlib.Path:
    """Where the once-per-session marker lives. Overridable for tests and for
    a machine that keeps state somewhere other than the home directory."""
    override = os.environ.get("ENGRAPHY_STATE_DIR")
    if override:
        return pathlib.Path(override)
    return pathlib.Path.home() / ".engraphy" / "state"


def slug(name: str) -> str:
    """A repository name as a scope id fragment: lower case, single hyphens."""
    out = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return out


def scope_for_repo(repo: str | None, cwd: str) -> str | None:
    """`code-<repo>` from the origin remote, falling back to the directory name.

    Handles the three remote spellings git hands back: an https URL, an ssh
    URL, and scp-style `git@host:owner/repo.git`. Returns None when neither
    source yields anything usable, which leaves the agent to resolve the scope
    itself through `scope_list`.
    """
    candidate = ""
    if repo:
        tail = repo.strip().rstrip("/").removesuffix(".git")
        candidate = re.split(r"[/:]", tail)[-1] if tail else ""
    if not slug(candidate):
        candidate = pathlib.Path(cwd).name
    body = slug(candidate)
    if not body:
        return None
    return (SCOPE_PREFIX + body)[:MAX_SCOPE_ID].rstrip("-")


def repo_url(cwd: str) -> str | None:
    """The origin remote of the checkout the session started in, or None."""
    try:
        done = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=cwd, capture_output=True, text=True, timeout=3, check=False,
        )
    except Exception:  # noqa: BLE001  (a hook never fails a session)
        return None
    return done.stdout.strip() or None


def session_start_context(scope: str | None) -> str:
    """The pre-work contract, named scope and all."""
    where = f"`{scope}`" if scope else "the scope `scope_list` gives for this repository"
    lines = [
        "Engraphy memory is available on this machine, as the `engraphy` MCP server.",
        (f"This checkout's memory scope is {where}, and the user's personal scope is "
         "ambient, so a read of one returns both."),
        "",
        "Before your first edit in this session:",
        ("1. Call `pending_list`. Anything it returns is an earlier write that was "
         "never saved; resolve each one with `resolve_duplicate`."),
        (f"2. Call `briefing(scope={scope or '<the repository scope>'}, hint=<the "
         "request, plus the paths you are about to open>). The hint is what fills "
         "the relevant section."),
        ("3. Before changing code in an area you have not already read this session, "
         "`search` that scope for the paths, file names and component names you are "
         "about to touch."),
        "",
        ("When the user states a convention, an anti-pattern, an off-limits area, a "
         "recurring bug, a coding or comment preference, a decision and its reasoning, "
         "or a person and what they manage: `write` it in that same turn. If the write "
         "returns `needs_confirmation`, nothing is saved yet, so call "
         "`resolve_duplicate` before you reply."),
    ]
    return "\n".join(lines)


def prompt_context(scope: str | None, request: str) -> str:
    """The hinted half, on the first substantive message of the session."""
    target = scope or "<the repository scope>"
    return (
        "Engraphy: this is the first request of the session, so call "
        f'`briefing(scope={target}, hint="{request}")` before your first edit, '
        "unless you already have. Add the paths you expect to open to the hint. "
        "Write what the session teaches as it is taught, and resolve any "
        "`needs_confirmation` in the same turn."
    )


def read_event() -> dict:
    """Claude Code hands a hook its event as JSON on stdin. Absence is normal."""
    try:
        raw = sys.stdin.buffer.read().decode("utf-8-sig", errors="replace")
    except Exception:  # noqa: BLE001
        return {}
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def marker_path(session_id) -> pathlib.Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "", str(session_id or "unknown"))[:64] or "unknown"
    return state_dir() / f"session-{safe}.json"


def claim_once(session_id) -> bool:
    """True the first time a session asks, False afterwards. A state directory
    that cannot be written returns True every time: repeating the cue is a
    smaller fault than losing it."""
    path = marker_path(session_id)
    try:
        if path.exists():
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"hinted": True}), encoding="utf-8")
    except Exception:  # noqa: BLE001
        return True
    return True


def emit(event_name: str, context: str, plain: bool = False) -> None:
    if plain:  # a harness that takes stdout as context unchanged
        sys.stdout.flush()
        sys.stdout.buffer.write((context + "\n").encode("utf-8"))
        return
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": context,
        }
    }))


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else ""
    plain = "--plain" in argv
    event = read_event()
    cwd = event.get("cwd") or os.getcwd()

    if command == "session-start":
        emit("SessionStart", session_start_context(scope_for_repo(repo_url(cwd), cwd)), plain)
        return 0

    if command == "prompt":
        text = (event.get("prompt") or "").strip()
        if len(text) < MIN_TASK_CHARS or text.startswith("/"):
            return 0
        if not claim_once(event.get("session_id")):
            return 0
        scope = scope_for_repo(repo_url(cwd), cwd)
        emit("UserPromptSubmit", prompt_context(scope, text[:MAX_HINT]), plain)
        return 0

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception:  # noqa: BLE001  (the one rule this file has)
        sys.exit(0)
