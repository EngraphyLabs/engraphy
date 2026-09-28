"""Every shipped pack, held to the same bar.

test_pack_validate.py and test_pack_name_patterns.py check the starter pack and
the fixtures by name. This file discovers `packs/*/pack.yaml` instead, so a pack
added to the repository is covered the moment it lands: schema, name patterns,
reserved names, the edges the engine writes itself, and the pack format this
engine understands.
"""

import pathlib

import psycopg
import pytest
import yaml

from conftest import insert_node

from engraphy.admin import packs

REPO_ROOT = pathlib.Path(__file__).parents[2]
PACK_FILES = sorted((REPO_ROOT / "packs").glob("*/pack.yaml"))


def _load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_the_repository_ships_packs():
    # A glob that silently matched nothing would make every test below vacuous.
    assert PACK_FILES, "no packs/*/pack.yaml found"
    assert {p.parent.name for p in PACK_FILES} >= {"starter", "conversational", "dev"}


@pytest.mark.parametrize("path", PACK_FILES, ids=[p.parent.name for p in PACK_FILES])
def test_shipped_pack_is_valid(path):
    pack = _load(path)
    assert packs.validate(pack) == []
    assert packs.validate_name_patterns(pack) == []
    assert packs.validate_reserved_names(pack) == []


@pytest.mark.parametrize("path", PACK_FILES, ids=[p.parent.name for p in PACK_FILES])
def test_shipped_pack_declares_the_edges_the_engine_writes(path):
    # The merge path attaches same_topic and the supersede tool inserts
    # supersedes, so a pack that omits either leaves the engine unable to
    # complete its own write path.
    pack = _load(path)
    assert packs.check_same_topic_declared(pack) is None
    assert "supersedes" in pack["edge_types"]


@pytest.mark.parametrize("path", PACK_FILES, ids=[p.parent.name for p in PACK_FILES])
def test_shipped_pack_format_is_understood_by_this_engine(path):
    assert packs.check_pack_format(_load(path)) is None


@pytest.mark.parametrize("path", PACK_FILES, ids=[p.parent.name for p in PACK_FILES])
def test_shipped_pack_has_an_agent_guide(path):
    assert (path.parent / "agent-guide.md").is_file()


# ------------------------------------------------- applying them for real

@pytest.mark.parametrize("path", PACK_FILES, ids=[p.parent.name for p in PACK_FILES])
def test_shipped_pack_applies_from_empty(conn, path):
    pack = _load(path)
    space_id = f"shipped-{path.parent.name}"
    cur = conn.cursor()
    cur.execute("INSERT INTO spaces (id, display_name) VALUES (%s, %s)", (space_id, space_id))
    packs.apply(pack, space_id, cur)

    cur.execute("SELECT name FROM node_types WHERE space_id = %s ORDER BY name", (space_id,))
    assert [r[0] for r in cur.fetchall()] == sorted(pack["node_types"])
    cur.execute("SELECT name FROM edge_types WHERE space_id = %s ORDER BY name", (space_id,))
    assert [r[0] for r in cur.fetchall()] == sorted(pack["edge_types"])
    # The briefing and the per-space tool descriptions are read back from
    # config at call time, so an applied pack has to leave both there.
    cur.execute("SELECT value FROM config WHERE space_id = %s AND key = 'pack.briefing'", (space_id,))
    assert cur.fetchone()[0] == pack.get("briefing", {})
    cur.execute(
        "SELECT value FROM config WHERE space_id = %s AND key = 'pack.tool_descriptions'",
        (space_id,),
    )
    assert cur.fetchone()[0] == pack.get("tool_descriptions", {})


def test_dev_pack_attaches_a_rule_to_the_code_area_it_governs(conn):
    """The dev pack's whole shape: a convention, an anti-pattern and an owner
    all reachable from one component, and a rule the pack does not permit
    refused by the engine rather than by convention."""
    pack = _load(REPO_ROOT / "packs" / "dev" / "pack.yaml")
    space_id = "dev-chain"
    cur = conn.cursor()
    cur.execute("INSERT INTO spaces (id, display_name) VALUES (%s, 'Dev chain')", (space_id,))
    cur.execute(
        "INSERT INTO principals (space_id, id, display_name) VALUES (%s, 'author', 'Author')",
        (space_id,),
    )
    packs.apply(pack, space_id, cur)
    cur.execute(
        "INSERT INTO scopes (space_id, id, display_name, owner_principal, visibility) "
        "VALUES (%s, 'code-billing-api', 'billing-api', 'author', 'private')",
        (space_id,),
    )

    def node(node_type, attrs, title, body):
        return insert_node(conn, space_id, "code-billing-api", node_type=node_type,
                           attrs=attrs, author_principal="author", title=title, body=body)

    component = node("component", {"path": "src/billing/", "language": "python"},
                     "The billing package", "src/billing/, the billing package.")
    convention = node("convention", {"strength": "hard", "domain": "errors"},
                      "Billing errors carry a reason code",
                      "Every error raised in src/billing/ carries a reason code.")
    anti_pattern = node("anti_pattern", {"severity": "high"},
                        "Never call the gateway from a controller",
                        "Controllers that call the gateway directly bypass retries. "
                        "Go through PaymentGateway instead.")
    owner = node("stakeholder", {"role": "Tech lead", "manages": "billing"},
                 "Priya, billing tech lead", "Priya approves changes to src/billing/.")

    for src in (convention, anti_pattern):
        cur.execute(
            "INSERT INTO edges (space_id, src_id, dst_id, type) VALUES (%s, %s, %s, 'applies_to')",
            (space_id, src, component),
        )
    cur.execute(
        "INSERT INTO edges (space_id, src_id, dst_id, type) VALUES (%s, %s, %s, 'owns')",
        (space_id, owner, component),
    )

    # One read of the component answers "what governs this area, and whose is it".
    cur.execute(
        "SELECT n.type FROM edges e JOIN nodes n ON n.id = e.src_id "
        "WHERE e.space_id = %s AND e.dst_id = %s ORDER BY n.type",
        (space_id, component),
    )
    assert [r[0] for r in cur.fetchall()] == ["anti_pattern", "convention", "stakeholder"]

    # `owns` runs from a stakeholder to a component and nowhere else: the pack
    # declares no rule for the reverse, and the engine refuses it in the
    # database rather than leaving it to the agent's good manners.
    with pytest.raises(psycopg.errors.CheckViolation, match="no rule for type=owns"):
        cur.execute(
            "INSERT INTO edges (space_id, src_id, dst_id, type) VALUES (%s, %s, %s, 'owns')",
            (space_id, component, owner),
        )
