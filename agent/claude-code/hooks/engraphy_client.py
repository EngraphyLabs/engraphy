"""A minimal Engraphy client for a hook: standard library only.

The hook runs on whatever Python the machine has, before a session starts, so
it depends on nothing that needs installing. That rules out the MCP SDK and
httpx, and leaves `urllib` speaking the Streamable HTTP transport directly.
Three things make that reasonable rather than reckless:

- The engine runs its session manager stateless (`app.py`,
  `StreamableHTTPSessionManager(stateless=True)`), so there is no session id to
  track and no handshake to keep alive. One POST is one call.
- The hook only ever reads: `scope_list`, `pending_list`, `briefing`. Nothing
  here writes a memory, because nothing becomes memory without the agent's
  judgment.
- Every failure returns None. A memory server that is down, slow, or speaking
  something unexpected degrades the session to the text-only contract; it never
  fails it.

The credential is read from the registration the harness already holds
(`~/.claude.json`), so the hook adds no second copy of the token. It is never
logged, and never injected into the session.
"""

from __future__ import annotations

import json
import os
import pathlib
import urllib.error
import urllib.request

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "engraphy-cue-hook", "version": "1"}
SERVER_KEY = "engraphy"


def registration(path: pathlib.Path | None = None) -> tuple[str, dict[str, str]] | None:
    """(url, headers) for the Engraphy server, or None when it is not set up.

    `ENGRAPHY_URL` plus `ENGRAPHY_TOKEN` win when both are set. Otherwise the
    harness's own registration is read: `mcpServers.engraphy` in
    `~/.claude.json`, at the top level or under any project, which is where
    `claude mcp add` puts it.
    """
    url = os.environ.get("ENGRAPHY_URL")
    token = os.environ.get("ENGRAPHY_TOKEN")
    if url and token:
        return url, {"Authorization": f"Bearer {token}"}

    path = path or pathlib.Path.home() / ".claude.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001  (absent, unreadable, or not JSON)
        return None
    if not isinstance(config, dict):
        return None

    candidates = [config.get("mcpServers")]
    projects = config.get("projects")
    if isinstance(projects, dict):
        candidates += [p.get("mcpServers") for p in projects.values() if isinstance(p, dict)]
    for servers in candidates:
        if not isinstance(servers, dict):
            continue
        entry = servers.get(SERVER_KEY)
        if not isinstance(entry, dict) or not entry.get("url"):
            continue
        headers = {k: v for k, v in (entry.get("headers") or {}).items() if isinstance(v, str)}
        return entry["url"], headers
    return None


def _parse(body: bytes, content_type: str) -> dict | None:
    """A Streamable HTTP reply is either a JSON body or an SSE stream carrying
    one. Take the last `data:` payload that holds a JSON-RPC result."""
    text = body.decode("utf-8", errors="replace")
    if "text/event-stream" in content_type:
        found = None
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                message = json.loads(line[len("data:"):].strip())
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict) and ("result" in message or "error" in message):
                found = message
        return found
    try:
        message = json.loads(text)
    except json.JSONDecodeError:
        return None
    return message if isinstance(message, dict) else None


class Client:
    """One POST per call, no session state, every failure a None."""

    def __init__(self, url: str, headers: dict[str, str], timeout: float = 8.0):
        self.url = url
        self.headers = headers
        self.timeout = timeout
        self._id = 0

    def _post(self, method: str, params: dict) -> dict | None:
        self._id += 1
        payload = json.dumps(
            {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        ).encode("utf-8")
        request = urllib.request.Request(
            self.url, data=payload, method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": PROTOCOL_VERSION,
                **self.headers,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                message = _parse(response.read(), response.headers.get("Content-Type", ""))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            return None
        if not message or "result" not in message:
            return None
        result = message["result"]
        return result if isinstance(result, dict) else None

    def initialize(self) -> bool:
        """Stateless transports accept a call without it, and a stateful one
        does not, so it is sent and its failure is not fatal."""
        return self._post("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        }) is not None

    def call(self, tool: str, arguments: dict | None = None) -> dict | None:
        """A tool's envelope, or None. `structuredContent` is the envelope the
        engine returns; the text content carries the same JSON for a client
        that asked for no structured output."""
        result = self._post("tools/call", {"name": tool, "arguments": arguments or {}})
        if result is None or result.get("isError"):
            return None
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            return structured
        for block in result.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                try:
                    parsed = json.loads(block.get("text") or "")
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    return parsed
        return None


def connect(timeout: float = 8.0, config_path: pathlib.Path | None = None) -> Client | None:
    """A ready client, or None when Engraphy is not registered on this machine."""
    found = registration(config_path)
    if not found:
        return None
    url, headers = found
    client = Client(url, headers, timeout=timeout)
    client.initialize()
    return client
