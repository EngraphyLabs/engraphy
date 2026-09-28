# dev pack: agent guide

The **dev** pack is for an agent that writes code: what a codebase expects of a
change, what has gone wrong in it before, who owns which part of it, and how
the user wants the work done.

This is its cheat sheet. The generic [skills/](../../skills/README.md) set
covers how to use memory at all, and
[skills/coding-memory-protocol.md](../../skills/coding-memory-protocol.md)
covers when to recall and when to write during a working session. Read those
first; this page names this pack's vocabulary and the conventions particular to
it.

## The shape of the graph

A `component` is the anchor. Everything that governs an area of code points at
its component with `applies_to`, and the person who owns that area points at it
with `owns`:

```
convention ─┐
anti_pattern ─┤
boundary ─────┼─ applies_to ─▶ component ◀─ owns ─ stakeholder
recurring_bug ┤
decision ─────┘
```

That single shape is what makes "what do I need to know before I touch
`src/billing/`?" answerable by one search followed by one traverse.

## Node types: what to write, and when

| Situation | Write a… |
|-----------|----------|
| An area of code earns its first memory: a service, package, module, directory or glob | `component` |
| A rule a change is expected to follow in this codebase | `convention` |
| A way of doing something that has caused trouble here, and what to do instead | `anti_pattern` |
| Code that must not change, or not without approval | `boundary` |
| A defect that has now appeared more than once | `recurring_bug` |
| A person whose ownership or approval matters | `stakeholder` |
| How the user wants work done, including comment style | `preference` |
| A choice made between real alternatives, with its reasoning | `decision` |
| A durable fact about the work that fits none of the above | `note` |

Write **one fact per node**. Two rules in one sentence are two writes, so each
can be recalled, corrected and superseded on its own.

`convention` against `preference`: a convention is a property of the codebase
("this repository validates at the edge, never in the service layer"), and a
preference is a property of the user ("I like short comments that say why").
The convention belongs in the repository scope, the preference in the user's
ambient personal scope.

`anti_pattern` against `recurring_bug`: an anti-pattern is a way of writing
code that invites trouble, so it is advice for the next change. A recurring bug
is a defect that keeps being observed, so it carries a symptom to recognise and
a fix that works.

## Attributes

Every type is `closed`, so only these keys are accepted. Anything else goes in
the body.

- **`component`**: required `path` (a path, glob or module name: `src/billing/`,
  `packages/ui/**`, `PaymentGateway`); optional `repo` and `language`.
- **`convention`**: required `strength` (`hard` = a change that breaks it is a
  defect; `soft` = the default); optional `domain` (for example `testing`,
  `errors`, `naming`).
- **`anti_pattern`**: required `severity` (`low`, `medium`, `high`); optional
  `domain`. Put both halves in the body: what goes wrong, and what to do
  instead.
- **`boundary`**: required `rule` (`do_not_touch`, `ask_first`, `generated`,
  `vendored`, `frozen_interface`); optional `approver` (whose sign-off lifts
  `ask_first`).
- **`recurring_bug`**: required `status` (`open`, `mitigated`, `fixed`);
  optional `last_seen` (date) and `ticket`.
- **`stakeholder`**: required `role`; optional `manages` (the area or subject
  in words) and `contact`.
- **`preference`**: required `strength` (`hard` = never override; `soft` = a
  default); optional `domain`. Use `domain: comments` for comment style and
  density, which is the preference agents most often need and least often
  record.
- **`decision`**: optional `as_of` (the date it was made) and `ticket`.
- **`note`**: no attributes.

**Restate attribute values in the body.** Attributes marked `searchable` in
this pack enter the search surface, and the rest are filters only. Saying the
path, the owner or the ticket in the body as well costs a few words and makes
the memory findable by the words a person would actually use.

## Edges

| Edge | Meaning | Draw it from → to |
|------|---------|-------------------|
| `applies_to` | This memory governs this area of code | `convention`, `anti_pattern`, `boundary`, `recurring_bug`, `decision`, `note`, `component` → `component` |
| `owns` | This person owns or approves changes here | `stakeholder` → `component` |
| `involves` | This memory concerns this person | any → `stakeholder` |
| `relates_to` | A generic association, either direction | any → any |
| `supersedes` | This node replaces an older one | any → any |
| `same_topic` | Same topic, distinct content; attached automatically when the engine keeps both of two near-duplicates | any → any (automatic; do not draw it yourself) |

`component` → `component` with `applies_to` expresses a part of a larger area,
so a rule on the parent is reachable from the child.

Pass edges as `links` on the `write` call when the target already exists, which
saves a round trip and keeps the rule and its attachment atomic from the
agent's point of view.

## Correcting a memory

Code memory goes stale faster than personal memory: conventions get revised,
bugs get fixed, owners move on. Use `supersede`, not a second `write`. A plain
re-write of a changed fact is likely to be **merged** into the stale node,
which leaves the old claim current. This is the contradiction contract in
[skills/writing-and-dedup.md](../../skills/writing-and-dedup.md).

A `recurring_bug` that is fixed can be updated in place instead (`update` with
`status: fixed`) when the symptom and cause are unchanged and only the status
moved.

## At session start

`briefing` here returns, in order: **boundaries** (every off-limits area),
**hard conventions**, **standing preferences** (the ones marked
`strength: hard`), a **relevant** semantic section over every type (**pass a
`hint`** carrying the task and the paths you are about to open, or it comes
back empty), **open recurring bugs**, and **recent decisions** from the last 30
days. The footer reports aged pending captures.

The first three sections are constraints on the change you are about to make,
not background reading.

## Scope

Repository scopes are `code-<repo>` and hold what is true of that codebase. The
user's `personal-<principal>` scope is ambient, so it is unioned into every
read, and it holds the preferences that follow them between repositories. See
[skills/scopes-and-visibility.md](../../skills/scopes-and-visibility.md), and
[docs/08-memory-in-your-coding-agent.md](../../docs/08-memory-in-your-coding-agent.md)
for setting them up.
