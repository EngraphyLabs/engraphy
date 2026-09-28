# Memory in your coding agent

This guide puts Engraphy into the loop of real work: the agent recalls what
governs a piece of code before it changes it, and records what you tell it at
the moment you tell it.

Three pieces do that, and they are independent enough to install in any order:

| Piece | What it does |
|---|---|
| The [dev pack](../packs/dev/pack.yaml) | Gives the space the vocabulary of code work: `component`, `convention`, `anti_pattern`, `boundary`, `recurring_bug`, `stakeholder`, `preference`, `decision`, `note`, and a briefing that opens with the off-limits areas and the hard rules. |
| The [instruction block](../agent/coding-agent-instructions.md) | The standing text your agent loads every session: resolve the scope, brief before the first edit, search before touching new code, write on the trigger, finish the write in the same turn. |
| The [Claude Code hooks](../agent/claude-code/) | Inject the same contract at session start and on the first request, so it holds even in a session that loaded no instructions file. |

Everything here assumes the server is already reachable from the machine you
work on and registered with your agent. See [02-setup.md](02-setup.md) for the
server, and the extension walkthrough for registering it with an editor.

## 1. Give work its own space, on the dev pack

A pack is the schema of a whole space, and `pack apply` runs once per space, so
work memory goes in a space of its own. That also keeps work and personal
memory apart when you search.

From a checkout of this repository, or the admin image:

```bash
engraphy-admin space create --id work --display-name "Work" --principal devon
engraphy-admin pack apply packs/dev/pack.yaml --space work
engraphy-admin token create --space work --principal devon --client-name "vs code"
```

`space create` also creates `personal-devon` in that space, as an ambient
scope: the engine unions an ambient scope into every read of another scope.
`token create` prints the bearer token once. Register it with your editor under
the server name `engraphy`, which is the name the instruction block and the
hooks both use.

To put the dev pack into a space that already has a pack, use
`engraphy-admin pack upgrade packs/dev/pack.yaml --space <space>` instead. It
adds what is new, it replaces that space's briefing and tool descriptions with
the dev pack's, and it refuses to drop a node type that still holds memories.
Read what it reports before you rely on the result.

## 2. Create the scope for a repository

Repository scopes are named `code-<repo>`. Create one for each repository you
want remembered. The quickest route is to ask your agent, which has the
`scope_create` tool:

> Create an Engraphy scope `code-billing-api`, described as "The billing-api
> repository: its conventions, anti-patterns, off-limits areas, recurring bugs
> and owners."

The repository scope holds what is true of that codebase alone. The
preferences that follow you between repositories, including how you want code
commented, belong in your personal scope, which is ambient and therefore
already in play on every read.

## 3. Install the instruction block

Copy the text between the `BEGIN ENGRAPHY INSTRUCTIONS` and `END ENGRAPHY
INSTRUCTIONS` markers in
[agent/coding-agent-instructions.md](../agent/coding-agent-instructions.md)
into the file your agent loads. Replace `code-<repo>` with the scope you
created if you want it named outright.

### GitHub Copilot in VS Code

Copilot has no hooks, so the instruction block is what carries the protocol,
and it is the piece to install first. Where it goes depends on how widely you
want it to apply:

| Where | How | Use it when |
|---|---|---|
| Every repository you open, personal | Command Palette, **Chat: New Instructions File**, saved as a **user** instructions file, with `applyTo: '**'` in its frontmatter | You work in repositories you do not own, or you want memory on everywhere without committing anything. It is stored in your VS Code profile and roams with Settings Sync. |
| One repository, shared | `.github/copilot-instructions.md` | The team shares the memory space and wants the protocol in the repository. |
| One repository, any agent | `AGENTS.md` | Several agents work on the repository; Copilot and other harnesses read this file. |
| Part of a repository | `.github/instructions/engraphy-memory.instructions.md`, with an `applyTo` glob | You want the protocol on one area of the tree. |
| Copilot Agent Host sessions | `~/.copilot/instructions` | You use Agent Host, whose user instructions live outside VS Code's profile storage. |

The user instructions file is the one to reach for on a work machine: it
applies to every repository you open, and nothing is added to your employer's
repository. VS Code's own reference is
[Custom instructions](https://code.visualstudio.com/docs/copilot/customization/custom-instructions).

Confirm Engraphy is registered as an MCP server for the editor (`.vscode/mcp.json`
for one workspace, or your user `mcp.json`), then start a new chat: the tools
appear as `briefing`, `search`, `write` and the rest.

### Claude Code

| Where | File |
|---|---|
| Every repository | `~/.claude/CLAUDE.md` |
| One repository | `CLAUDE.md` at the repository root |

The same text works in both. On a work machine, use `~/.claude/CLAUDE.md` for
the same reason as above.

## 4. Add the hooks (Claude Code)

The hooks state the contract at session start, and hand the agent its hint on
the first request of the session. They make no network call and need no token,
so they cannot stall a session or leak a credential, and every path exits 0.

1. Open `agent/claude-code/settings-snippet.json`.
2. Merge its `hooks` block into `~/.claude/settings.json`, or into
   `.claude/settings.json` for one project.
3. Replace `<ENGRAPHY>` with the path to this checkout, for example
   `C:/Users/devon/engraphy` or `/Users/devon/engraphy`. On macOS and Linux,
   change `python` to `python3` in both commands.
4. Start a new session. The first message of a session now carries the scope
   and the pre-work contract.

A session where the hooks are absent still has the instruction block, and a
session where the instructions file is absent still has the hooks. Install both
and the protocol survives either being missed.

## 5. Check that it works

Four checks, in a fresh session in a repository you have created a scope for:

1. **It recalls before it works.** Give it a task in a part of the code it has
   not seen ("add a retry to the payment client"). It should call `briefing`,
   and `search` for the paths it is about to open, before proposing a change.
2. **It writes on the trigger.** Tell it something durable: "we never call the
   payment client directly from a controller, always through
   `PaymentGateway`." It should `write` an `anti_pattern` or a `convention` in
   that same turn, without being asked to save anything.
3. **It finishes the write.** Tell it something close to what it just wrote, so
   the write comes back `needs_confirmation`. It should call
   `resolve_duplicate` before it replies, not leave the memory parked.
4. **The memory is there next time.** Start a new session and ask "what should
   I know before touching the payment client?" The briefing and one search
   should return what you told it.

If the second check does not happen, confirm the instruction block is loaded:
in Copilot, the instructions file is listed in the chat's references; in Claude
Code, `/memory` shows the files in play.

## What goes where

| Memory | Scope | Type |
|---|---|---|
| This repository does it this way | `code-<repo>` | `convention` |
| This mistake has been made here before | `code-<repo>` | `anti_pattern` |
| Do not touch this, or ask first | `code-<repo>` | `boundary` |
| This defect keeps coming back | `code-<repo>` | `recurring_bug` |
| This person owns or approves this area | `code-<repo>` | `stakeholder` |
| We chose this, for this reason | `code-<repo>` | `decision` |
| I want my code commented like this | `personal-<principal>` | `preference`, `domain: comments` |
| I want work done like this, everywhere | `personal-<principal>` | `preference` |

The full protocol, including what not to write, is
[skills/coding-memory-protocol.md](../skills/coding-memory-protocol.md). The
pack's own vocabulary reference is
[packs/dev/agent-guide.md](../packs/dev/agent-guide.md).
