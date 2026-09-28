# agent

What a coding agent loads so that memory is part of how it works.

| File | What it is |
|---|---|
| [coding-agent-instructions.md](coding-agent-instructions.md) | The standing instruction block. Paste the delimited section into `~/.copilot/copilot-instructions.md`, `.github/copilot-instructions.md`, `AGENTS.md`, `CLAUDE.md` or `~/.claude/CLAUDE.md`. |
| [claude-code/settings-snippet.json](claude-code/settings-snippet.json) | Hook wiring for Claude Code: session start, and the first request of a session. |
| [claude-code/hooks/engraphy_cue.py](claude-code/hooks/engraphy_cue.py) | The hook itself. Standard library only, no network, exits 0 on every path. |

Install steps for both harnesses are in
[docs/08-memory-in-your-coding-agent.md](../docs/08-memory-in-your-coding-agent.md).
The protocol these files carry is authored in
[skills/coding-memory-protocol.md](../skills/coding-memory-protocol.md), and
the vocabulary it uses comes from [packs/dev](../packs/dev/pack.yaml).

Two layers, deliberately. The instruction block travels with the harness and
holds in any session that loads it, including harnesses with no hook support.
The hooks state the same contract inside the session itself, which covers a
session that loaded no instructions file. Either alone works, and both together
is what makes the protocol hard to miss.
