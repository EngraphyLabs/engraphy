"""The cueing surface: the text that decides whether an agent uses memory.

Three things are held here, because all three are prose that a test is the only
thing standing between and quiet drift:

1. The trigger table is authored in skills/coding-memory-protocol.md and copied
   into agent/coding-agent-instructions.md for harnesses that load one file.
   The two copies must stay identical.
2. Every core tool description names the moment to call the tool, and the
   follow-up an outcome can oblige. A description that only names a mechanism
   is a tool an agent does not think to reach for.
3. The parked-write instruction is byte-pinned to the wire fixture, the way
   MERGED_INSTRUCTION is.
"""

import json
import pathlib

import pytest
import yaml

from engraphy.core.dedup import PENDING_INSTRUCTION
from engraphy.server.tool_registry import _BASE_DESCRIPTIONS

REPO_ROOT = pathlib.Path(__file__).parents[2]
SKILL = REPO_ROOT / "skills" / "coding-memory-protocol.md"
INSTRUCTIONS = REPO_ROOT / "agent" / "coding-agent-instructions.md"
COPILOT = REPO_ROOT / "agent" / "copilot" / "engraphy-memory.instructions.md"
CC_SKILL = REPO_ROOT / "agent" / "claude-code" / "skills" / "engraphy-memory" / "SKILL.md"
OPERATOR_GUIDE = REPO_ROOT / "docs" / "08-getting-agents-to-use-memory.md"
DEV_PACK = REPO_ROOT / "packs" / "dev" / "pack.yaml"
WIRE_PENDING = REPO_ROOT / "engraphy" / "tests" / "fixtures" / "wire" / "write_needs_confirmation.json"

TRIGGER_HEADER = "| When this happens | What to write |"

#: Files this change owns. House style: no em dashes in shipped prose.
AUTHORED_FILES = [
    SKILL,
    INSTRUCTIONS,
    COPILOT,
    CC_SKILL,
    OPERATOR_GUIDE,
    DEV_PACK,
    REPO_ROOT / "packs" / "dev" / "agent-guide.md",
    REPO_ROOT / "docs" / "08-getting-agents-to-use-memory.md",
    REPO_ROOT / "agent" / "README.md",
    REPO_ROOT / "agent" / "claude-code" / "hooks" / "engraphy_cue.py",
    REPO_ROOT / "agent" / "claude-code" / "settings-snippet.json",
]


def trigger_rows(path: pathlib.Path) -> list[str]:
    """The rows of the trigger table in a markdown file, header excluded."""
    lines = path.read_text(encoding="utf-8").splitlines()
    start = lines.index(TRIGGER_HEADER)
    rows = []
    for line in lines[start + 2:]:  # +2 skips the header separator
        if not line.startswith("|"):
            break
        rows.append(line.strip())
    return rows


def test_the_trigger_table_is_identical_in_both_copies():
    authored = trigger_rows(SKILL)
    copied = trigger_rows(INSTRUCTIONS)
    assert authored, "the skill has no trigger table"
    assert authored == copied, (
        "skills/coding-memory-protocol.md is the authored copy; re-copy the table "
        "into agent/coding-agent-instructions.md"
    )


def test_the_trigger_table_covers_every_writable_dev_type():
    pack = yaml.safe_load(DEV_PACK.read_text(encoding="utf-8"))
    table = "\n".join(trigger_rows(SKILL))
    for node_type in pack["node_types"]:
        assert f"`{node_type}`" in table, f"no trigger writes a {node_type}"


def test_the_instruction_block_names_the_vocabulary():
    # tools/list publishes no node types (the input schema is generated from
    # wire_types, which knows nothing of a space's pack), so the instruction
    # block is where an agent learns which types exist.
    pack = yaml.safe_load(DEV_PACK.read_text(encoding="utf-8"))
    text = INSTRUCTIONS.read_text(encoding="utf-8")
    for node_type in pack["node_types"]:
        assert f"`{node_type}`" in text, f"the instruction block never mentions {node_type}"
    for tool in ("briefing", "search", "write", "resolve_duplicate", "pending_list", "supersede"):
        assert f"`{tool}`" in text


def authored_block() -> str:
    return INSTRUCTIONS.read_text(encoding="utf-8").split(
        "<!-- BEGIN ENGRAPHY INSTRUCTIONS -->", 1)[1].split(
        "<!-- END ENGRAPHY INSTRUCTIONS -->", 1)[0].strip("\n")


@pytest.mark.parametrize("path,keys", [
    (COPILOT, ("name:", "description:", "applyTo: '**'")),
    (CC_SKILL, ("name: engraphy-memory", "description:")),
])
def test_a_destination_file_carries_the_block_verbatim(path, keys):
    """Each harness file is the block plus the frontmatter that harness reads,
    and nothing else. A body that drifts from the authored block is a second
    protocol, told slightly differently."""
    front, _, body = path.read_text(encoding="utf-8").partition("---\n\n")
    assert front.startswith("---\n")
    for key in keys:
        assert key in front, f"{path.name} frontmatter is missing {key}"
    assert body.strip("\n") == authored_block()


def test_the_operator_guide_pastes_the_block_verbatim():
    """The guide is the copy-paste source, so its snippet is the block itself,
    markers included, not a retelling of it."""
    text = OPERATOR_GUIDE.read_text(encoding="utf-8")
    snippet = text.split("<!-- BEGIN ENGRAPHY INSTRUCTIONS -->", 1)[1].split(
        "<!-- END ENGRAPHY INSTRUCTIONS -->", 1)[0].strip("\n")
    assert snippet == authored_block()


@pytest.mark.parametrize("path", [COPILOT, CC_SKILL], ids=["copilot", "skill"])
def test_the_operator_guide_carries_each_destination_file_whole(path):
    """A section lifted onto a page on its own is still installable: the guide
    prints each destination's file entire, frontmatter included, rather than
    telling the reader to fetch the body from another section."""
    guide = OPERATOR_GUIDE.read_text(encoding="utf-8")
    assert path.read_text(encoding="utf-8").strip("\n") in guide, (
        f"{path.name} is not pasted into the guide verbatim")


def test_the_operator_guide_names_every_destination():
    # A snippet a reader cannot place is a snippet they do not install.
    text = OPERATOR_GUIDE.read_text(encoding="utf-8")
    for destination in (
        "~/.claude/CLAUDE.md",
        "~/.claude/skills/engraphy-memory/SKILL.md",
        "~/.claude/settings.json",
        "engraphy-memory.instructions.md",
        ".github/copilot-instructions.md",
        "engraphy-admin pack apply packs/dev/pack.yaml",
        "engraphy-admin pack upgrade",
    ):
        assert destination in text, f"the guide never names {destination}"


def test_both_copies_cue_the_two_moments_explicitly():
    # The no-hook harness has nothing else to lean on: the recall moment and
    # the write moment have to be stated, not implied.
    for path in (INSTRUCTIONS, COPILOT, CC_SKILL):
        text = path.read_text(encoding="utf-8")
        assert "At the start of a ticket, bug fix or review" in text
        assert "Before you reply, two checks" in text
        assert "`pending_list`" in text and "`briefing(scope=" in text


def test_the_instruction_block_is_delimited_for_pasting():
    text = INSTRUCTIONS.read_text(encoding="utf-8")
    assert text.count("<!-- BEGIN ENGRAPHY INSTRUCTIONS -->") == 1
    assert text.count("<!-- END ENGRAPHY INSTRUCTIONS -->") == 1
    assert text.index("BEGIN ENGRAPHY") < text.index(TRIGGER_HEADER) < text.index("END ENGRAPHY")


@pytest.mark.parametrize("path", AUTHORED_FILES, ids=[p.name for p in AUTHORED_FILES])
def test_authored_prose_follows_house_style(path):
    assert path.is_file(), path
    assert "—" not in path.read_text(encoding="utf-8"), "em dash"


# ---------------------------------------------------------------- tool cues

#: tool -> substrings its description must carry. Each one is a moment or a
#: consequence, never a restatement of the mechanism.
REQUIRED_CUES = {
    "briefing": ["start of a task", "hint"],
    "search": ["before you act", "not already loaded"],
    "write": ["the moment it is stated", "same turn", "needs_confirmation", "supersede"],
    "pending_list": ["NOT saved", "start of a session"],
    "resolve_duplicate": ["not saved", "expires"],
    "supersede": ["CHANGED"],
    "link": ["link a new memory"],
    "traverse": ["call it when"],
}


@pytest.mark.parametrize("tool", sorted(REQUIRED_CUES))
def test_base_description_names_the_moment(tool):
    description = _BASE_DESCRIPTIONS[tool]
    for cue in REQUIRED_CUES[tool]:
        assert cue in description, f"{tool}: '{cue}' missing from its description"


def test_dev_pack_overrides_carry_their_whole_contract():
    # A pack override REPLACES the base line, so an override that drops the
    # follow-ups silently removes them from that space's tool surface.
    overrides = yaml.safe_load(DEV_PACK.read_text(encoding="utf-8"))["tool_descriptions"]
    assert "hint" in overrides["briefing"]
    assert "before" in overrides["search"]
    for cue in ("same turn", "needs_confirmation", "resolve_duplicate", "supersede"):
        assert cue in overrides["write"], f"the dev pack's write override drops '{cue}'"
    for cue in ("nothing was saved", "expires"):
        assert cue in overrides["resolve_duplicate"].lower()


def test_pending_instruction_is_pinned_to_the_wire_fixture():
    fixture = json.loads(WIRE_PENDING.read_text(encoding="utf-8"))
    assert fixture["response"]["instruction"] == PENDING_INSTRUCTION


def test_pending_instruction_states_the_consequence_and_the_deadline():
    assert "Nothing is saved yet" in PENDING_INSTRUCTION
    assert "same turn" in PENDING_INSTRUCTION
    assert "expires" in PENDING_INSTRUCTION
    assert "distinct" in PENDING_INSTRUCTION and "merge" in PENDING_INSTRUCTION
