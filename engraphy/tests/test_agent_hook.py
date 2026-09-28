"""agent/claude-code/hooks/engraphy_cue.py.

The hook is deliberately outside the package (it is installed by path into a
harness, and runs on whatever Python the machine has), so it is loaded here by
file path. What matters, and what is tested: it names the right scope, it emits
the shape Claude Code reads, the hinted cue fires once per session, and no
input of any kind makes it exit non-zero.
"""

import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import pytest

HOOK_PATH = (pathlib.Path(__file__).parents[2] / "agent" / "claude-code" / "hooks"
             / "engraphy_cue.py")


def _load():
    spec = importlib.util.spec_from_file_location("engraphy_cue", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cue = _load()


@pytest.mark.parametrize("remote,expected", [
    ("https://github.com/devon-clarkk/engraphy.git", "code-engraphy"),
    ("https://github.com/devon-clarkk/engraphy", "code-engraphy"),
    ("git@github.com:acme/Billing_API.git", "code-billing-api"),
    ("ssh://git@git.example.com:2222/acme/billing-api.git", "code-billing-api"),
    ("https://dev.azure.com/acme/_git/Payments Service", "code-payments-service"),
])
def test_scope_from_every_remote_spelling(remote, expected):
    assert cue.scope_for_repo(remote, "/somewhere/else") == expected


def test_scope_falls_back_to_the_directory_name(tmp_path):
    checkout = tmp_path / "Payments Service"
    checkout.mkdir()
    assert cue.scope_for_repo(None, str(checkout)) == "code-payments-service"


def test_scope_id_stays_within_the_engine_constraint():
    scope = cue.scope_for_repo("https://example.com/" + "x" * 200 + ".git", "/tmp")
    assert len(scope) <= cue.MAX_SCOPE_ID
    assert not scope.endswith("-")


def test_no_usable_name_yields_no_scope(tmp_path, monkeypatch):
    # An unnamed checkout is not a guess: the agent resolves the scope itself.
    monkeypatch.setattr(cue.pathlib.Path, "name", property(lambda self: ""))
    assert cue.scope_for_repo("", "/") is None


def test_session_start_context_names_the_scope_and_the_three_steps():
    context = cue.session_start_context("code-billing-api")
    assert "code-billing-api" in context
    assert "pending_list" in context
    assert "briefing" in context and "hint" in context
    assert "search" in context
    assert "resolve_duplicate" in context


def test_session_start_context_without_a_scope_defers_to_scope_list():
    context = cue.session_start_context(None)
    assert "scope_list" in context


def test_prompt_context_carries_the_request_as_the_hint():
    context = cue.prompt_context("code-billing-api", "add a retry to the payment client")
    assert "add a retry to the payment client" in context
    assert "briefing(scope=code-billing-api" in context


def test_claim_once_is_true_once_per_session(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAPHY_STATE_DIR", str(tmp_path / "state"))
    assert cue.claim_once("abc-123") is True
    assert cue.claim_once("abc-123") is False
    assert cue.claim_once("a-different-session") is True


def test_claim_once_repeats_rather_than_losing_the_cue_when_state_is_unwritable(tmp_path, monkeypatch):
    blocked = tmp_path / "file"
    blocked.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("ENGRAPHY_STATE_DIR", str(blocked / "state"))
    assert cue.claim_once("abc-123") is True
    assert cue.claim_once("abc-123") is True


# --------------------------------------------------------------- end to end

def _run(args, event, env_state):
    done = subprocess.run(
        [sys.executable, str(HOOK_PATH), *args],
        input=json.dumps(event) if isinstance(event, (dict, list)) else event,
        capture_output=True, text=True, timeout=30, check=False,
        env={**os.environ, "ENGRAPHY_STATE_DIR": str(env_state)},
    )
    return done


def test_session_start_emits_the_shape_claude_code_reads(tmp_path):
    done = _run(["session-start"], {"cwd": str(tmp_path)}, tmp_path / "state")
    assert done.returncode == 0
    payload = json.loads(done.stdout)
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "pending_list" in payload["hookSpecificOutput"]["additionalContext"]


def test_session_start_plain_emits_bare_text(tmp_path):
    done = _run(["session-start", "--plain"], {"cwd": str(tmp_path)}, tmp_path / "state")
    assert done.returncode == 0
    assert not done.stdout.lstrip().startswith("{")
    assert "briefing" in done.stdout


def test_prompt_fires_once_and_then_stays_quiet(tmp_path):
    event = {"cwd": str(tmp_path), "session_id": "s1",
             "prompt": "add a retry to the payment client"}
    first = _run(["prompt"], event, tmp_path / "state")
    second = _run(["prompt"], event, tmp_path / "state")
    assert first.returncode == second.returncode == 0
    assert "add a retry to the payment client" in first.stdout
    assert second.stdout.strip() == ""


@pytest.mark.parametrize("prompt", ["hi", "/clear", "   "])
def test_prompt_ignores_what_is_not_a_task(tmp_path, prompt):
    done = _run(["prompt"], {"cwd": str(tmp_path), "session_id": "s2", "prompt": prompt},
                tmp_path / "state")
    assert done.returncode == 0
    assert done.stdout.strip() == ""


@pytest.mark.parametrize("args,stdin", [
    (["session-start"], "not json at all"),
    (["prompt"], ""),
    (["prompt"], "[1, 2, 3]"),
    ([], "{}"),
    (["unknown-command"], "{}"),
])
def test_every_input_exits_zero(tmp_path, args, stdin):
    # The one rule the hook has: it can degrade, it cannot fail a session.
    done = _run(args, stdin, tmp_path / "state")
    assert done.returncode == 0
