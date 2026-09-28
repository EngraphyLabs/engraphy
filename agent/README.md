# agent

What a coding agent loads so that memory is part of how it works.

| File | What it is |
|---|---|
| [coding-agent-instructions.md](coding-agent-instructions.md) | The standing instruction block. Paste the delimited section into a Copilot user instructions file, `.github/copilot-instructions.md`, `AGENTS.md`, `CLAUDE.md` or `~/.claude/CLAUDE.md`. |
| [copilot/engraphy-memory.instructions.md](copilot/engraphy-memory.instructions.md) | The same block as a VS Code instructions file, frontmatter and all, for a Copilot user or workspace instructions file. Its body is the block above, held identical by a test. |
| [claude-code/settings-snippet.json](claude-code/settings-snippet.json) | Hook wiring for Claude Code: session start, and the first request of a session. |
| [claude-code/hooks/engraphy_cue.py](claude-code/hooks/engraphy_cue.py) | The hook itself. Standard library only, no network, exits 0 on every path. |

Edit the block in `coding-agent-instructions.md`, then copy the text between
its markers into `copilot/engraphy-memory.instructions.md`, under that file's
frontmatter. `engraphy/tests/test_agent_cueing.py` holds the two identical, and
holds the trigger table identical to the skill that authors it.

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

In GitHub Copilot there is one layer plus the tool descriptions, because
Copilot runs no hooks: the instructions file and the descriptions the server
publishes carry the protocol between them, which is why the dev pack's
`tool_descriptions` each state a whole contract rather than a mechanism.
