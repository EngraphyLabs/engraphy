# Memory in your coding agent

This guide puts Engraphy into the loop of real work: the agent recalls what
governs a piece of code before it changes it, and records what you tell it at
the moment you tell it.

Sections 3 and 4 are the two harnesses, and they are independent: install the
one you use, or both. Copilot runs no hooks, so there the instructions file and
the tool descriptions carry the protocol between them. Claude Code adds hooks,
which perform the session-start half rather than asking for it.

Three pieces do the work:

| Piece | What it does |
|---|---|
| The [dev pack](../packs/dev/pack.yaml) | Gives the space the vocabulary of code work: `component`, `convention`, `anti_pattern`, `boundary`, `recurring_bug`, `stakeholder`, `preference`, `decision`, `note`, and a briefing that opens with the off-limits areas and the hard rules. |
| The [instruction block](../agent/coding-agent-instructions.md) | The standing text your agent loads every session: resolve the scope, brief before the first edit, search before touching new code, write on the trigger, finish the write in the same turn. |
| The [Claude Code hooks](../agent/claude-code/) | Inject the same contract at session start and on the first request, so it holds even in a session that loaded no instructions file. Claude Code only. |

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

## 3. Copilot in VS Code (work)

Copilot runs no hooks, so two levers carry the whole protocol: the instructions
file, and the tool descriptions the server publishes. The pack supplies the
second one the moment it is applied, and this step installs the first.

### The file to create

`agent/copilot/engraphy-memory.instructions.md` in this repository is the file,
ready to use. It carries the frontmatter Copilot reads:

```yaml
---
name: Engraphy memory
description: Recall what governs this code before changing it, and record what the user states, as they state it.
applyTo: '**'
---
```

`applyTo: '**'` is what makes it always-on rather than attached to one part of
the tree.

**For every repository you open, without committing anything** (the one to use
on a work machine):

1. Command Palette, **Chat: New Instructions File**, and choose the **user**
   location rather than the workspace one. VS Code stores it in your profile,
   and Settings Sync roams it to your other machines.
2. Paste the contents of `agent/copilot/engraphy-memory.instructions.md` over
   the new file, keeping the frontmatter.
3. Name the scope outright if you like: replace `code-<repo>` in the Scope
   section with the scope you created in step 2.

**For one repository, shared with the team**, put the same body in
`.github/copilot-instructions.md` (no frontmatter needed there), and check that
`github.copilot.chat.codeGeneration.useInstructionFiles` is enabled, which is
what makes VS Code discover that file. `.github/instructions/engraphy-memory.instructions.md`
takes the file as it stands, frontmatter included, and `AGENTS.md` is the
cross-agent equivalent. If you use Copilot Agent Host, its user instructions
live in `~/.copilot/instructions` rather than the VS Code profile.

### Register the server

Engraphy must be registered as an MCP server for the editor: `.vscode/mcp.json`
for one workspace, or your user `mcp.json`. Register it under the name
`engraphy`, because the instructions file names the server. Use the token from
step 1.

### Confirm both levers are live

Start a new chat, then:

- Ask "what memory tools do you have?" The reply should list `briefing`,
  `search`, `write` and the rest, and their descriptions carry the protocol
  even on their own.
- Send any request, then expand **References** on the response. The
  instructions file should be listed there. If it is not, the file is in the
  wrong location, or `useInstructionFiles` is off.

VS Code's own reference is
[Custom instructions](https://code.visualstudio.com/docs/copilot/customization/custom-instructions).

## 4. Claude Code (home)

Two files, and the second one does automatically what Copilot does by
instruction.

### The instructions

Paste the text between the `BEGIN ENGRAPHY INSTRUCTIONS` and `END ENGRAPHY
INSTRUCTIONS` markers in
[agent/coding-agent-instructions.md](../agent/coding-agent-instructions.md)
into:

| Where | File |
|---|---|
| Every repository | `~/.claude/CLAUDE.md` |
| One repository | `CLAUDE.md` at the repository root |

### The hooks

The hooks state the contract at session start, and hand the agent the first
request of the session as its briefing hint, so the recall half happens whether
or not the instructions file was read. They make no network call and need no
token, so they cannot stall a session or leak a credential, and every path
exits 0.

1. Open `agent/claude-code/settings-snippet.json`.
2. Merge its `hooks` block into `~/.claude/settings.json`, or into
   `.claude/settings.json` for one project.
3. Replace `<ENGRAPHY>` with the path to this checkout, for example
   `C:/Users/devon/engraphy` or `/Users/devon/engraphy`. On macOS and Linux,
   change `python` to `python3` in both commands.
4. Start a new session. Its first message now carries the scope and the
   pre-work contract.

`/memory` lists the instruction files in play, and the hook's context appears
at the top of a new session.

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

If the second check does not happen, confirm the instructions really are
loaded: in Copilot, expand **References** on the response and look for the
instructions file; in Claude Code, run `/memory`. If they are loaded and the
write still does not happen, check that the space is on the dev pack, because
the tool descriptions a space publishes come from its pack.

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
