"""The app role holds exactly the privileges its RLS policies admit.

Two artifacts have to agree, and nothing but a test keeps them agreeing:

* `engraphy/db/migrations/*` create the policies, and `engraphy/db/schema.sql`
  is the generated record of the set they produce.
* `deploy/provision-app-role.sql` grants the `engraphy_app` role its table
  privileges, and `engraphy/tests/conftest.py` provisions the same role for the
  suite.

Under FORCE ROW LEVEL SECURITY a command with no policy is denied whatever the
grant says, so the two directions of disagreement fail differently and both
matter:

* **A grant wider than the policy set** can never be exercised. It is a
  privilege held for no reason, waiting for a policy that gets disabled or a
  future one written carelessly to make it live.
* **A policy with no matching grant** is the sharper one, because FORCE RLS
  turns a policy-less write into a silent zero-row no-op rather than an error
  (conftest's own note on the DELETE grant says so). A new policy whose grant
  was forgotten therefore ships as a success envelope over a write that did not
  happen.

A parse rather than a live probe: the question is what a deployment's own
provisioning script says, which no test database's current state can answer. A
live `has_table_privilege` check against a provisioned cluster is the natural
complement.
"""
import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).parents[2]
SCHEMA_SQL = REPO_ROOT / "engraphy" / "db" / "schema.sql"
PROVISION_SQL = REPO_ROOT / "deploy" / "provision-app-role.sql"
CONFTEST_PY = REPO_ROOT / "engraphy" / "tests" / "conftest.py"

_ROLE = "engraphy_app"
_COMMANDS = ("SELECT", "INSERT", "UPDATE", "DELETE")

# The RLS-covered tables. Asserted against the schema below rather than trusted,
# so a new RLS table has to be added here deliberately.
_RLS_TABLES = frozenset({
    "nodes", "edges", "scopes", "scope_grants", "principals",
    "pending_writes", "inbox", "dedup_log", "metrics_rollup",
})

# `FORCE` is emitted as `ALTER TABLE ONLY public.x`, `ENABLE` without the ONLY.
_FORCE_RE = re.compile(r"ALTER TABLE (?:ONLY )?public\.(\w+) FORCE ROW LEVEL SECURITY")
_ENABLE_RE = re.compile(r"ALTER TABLE (?:ONLY )?public\.(\w+) ENABLE ROW LEVEL SECURITY")
_POLICY_RE = re.compile(r"CREATE POLICY \w+ ON public\.(\w+)(?: AS \w+)? FOR (\w+)")
_POLICY_BODY_RE = re.compile(
    r"CREATE POLICY (\w+) ON public\.(\w+)(?: AS \w+)? FOR \w+(.*?);\s*\n", re.DOTALL)


def _schema() -> str:
    return SCHEMA_SQL.read_text(encoding="utf-8")


def _policy_commands() -> dict[str, set[str]]:
    """table -> the set of commands its policies admit, per schema.sql."""
    out: dict[str, set[str]] = {t: set() for t in _RLS_TABLES}
    for table, command in _POLICY_RE.findall(_schema()):
        if table not in out:
            continue
        out[table] |= set(_COMMANDS) if command == "ALL" else {command}
    return out


def _strip_sql_comments(sql: str) -> str:
    """Drop `--` line comments. Required, not tidiness: a statement's own prose
    explains the grants it makes, so the words GRANT and REVOKE appear in the
    comments, and a dot-matches-newline scan would let a comment start a match
    that swallows the real statement after it."""
    return "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())


def _granted_commands(sql: str) -> dict[str, set[str]]:
    """table -> the commands the role ends up holding, applying GRANTs and
    REVOKEs in file order so a REVOKE after a GRANT is honoured the way
    Postgres would apply it."""
    out: dict[str, set[str]] = {t: set() for t in _RLS_TABLES}
    pattern = re.compile(
        r"\b(GRANT|REVOKE)\s+(.*?)\s+ON\s+(.*?)\s+(?:TO|FROM)\s+" + _ROLE + r"\s*;",
        re.DOTALL | re.IGNORECASE,
    )
    for verb, privileges, tables in pattern.findall(_strip_sql_comments(sql)):
        commands = {c for c in _COMMANDS if re.search(r"\b" + c + r"\b", privileges, re.IGNORECASE)}
        if not commands:
            continue
        for raw in tables.split(","):
            table = raw.strip().strip('"').removeprefix("public.")
            if table not in out:
                continue
            if verb.upper() == "GRANT":
                out[table] |= commands
            else:
                out[table] -= commands
    return out


def _conftest_granted_commands() -> dict[str, set[str]]:
    """conftest names its tables through module constants interpolated into the
    statement, so the constants are resolved and the statements expanded into
    plain SQL first. One parser then serves both artifacts."""
    text = CONFTEST_PY.read_text(encoding="utf-8")
    constants = {}
    for name, body in re.findall(r"^(_[A-Z_]+_TABLES) = \((.*?)\)", text, re.MULTILINE | re.DOTALL):
        names = [piece.strip().strip("\"'") for piece in body.split(",")]
        constants[name] = [n for n in names if n]
    assert constants, "no table constants found in conftest.py"

    statement = re.compile(
        r"(GRANT|REVOKE)\s+(.*?)\s+ON\s+\{', '\.join\((_[A-Z_]+_TABLES)\)\}"
        r"\s+(?:TO|FROM)\s+\{APP_ROLE\}")
    expanded = []
    for verb, privileges, const in statement.findall(text):
        tables = constants.get(const)
        assert tables, "conftest references unknown constant " + const
        keyword = "TO" if verb.upper() == "GRANT" else "FROM"
        expanded.append(
            verb + " " + privileges + " ON " + ", ".join(tables)
            + " " + keyword + " " + _ROLE + ";")
    assert expanded, "no app-role GRANT/REVOKE statements found in conftest.py"
    return _granted_commands("\n".join(expanded))


def test_the_rls_table_list_matches_the_schema():
    """Guards every check below: if a migration adds an RLS table and this list
    is not updated, the grant checks would silently skip it."""
    forced = set(_FORCE_RE.findall(_schema()))
    assert forced == set(_RLS_TABLES), (
        "schema.sql forces RLS on " + repr(sorted(forced))
        + "; this test knows " + repr(sorted(_RLS_TABLES))
    )


def test_every_rls_table_is_both_enabled_and_forced():
    """ENABLE alone leaves the table OWNER exempt. FORCE is what closes that,
    and the pair is the premise every policy below rests on."""
    schema = _schema()
    enabled = set(_ENABLE_RE.findall(schema))
    forced = set(_FORCE_RE.findall(schema))
    assert enabled >= _RLS_TABLES, "not ENABLEd: " + repr(sorted(_RLS_TABLES - enabled))
    assert forced >= _RLS_TABLES, "not FORCEd: " + repr(sorted(_RLS_TABLES - forced))


def test_every_policy_is_scoped_to_the_space_guc():
    """A policy that never mentions engraphy.space_id is not space-isolating,
    whatever else it checks. The GUC is transaction-local and unset outside
    db.transaction(), so naming it is also what makes a policy fail closed."""
    offenders = [
        table + "." + name
        for name, table, body in _POLICY_BODY_RE.findall(_schema())
        if table in _RLS_TABLES and "engraphy.space_id" not in body
    ]
    assert offenders == [], "policies not scoped to the space GUC: " + repr(offenders)


def test_production_grants_match_the_policy_set_exactly():
    policies = _policy_commands()
    granted = _granted_commands(PROVISION_SQL.read_text(encoding="utf-8"))
    for table in sorted(_RLS_TABLES):
        assert granted[table] == policies[table], (
            table + ": provisioning grants " + repr(sorted(granted[table]))
            + ", policies admit " + repr(sorted(policies[table]))
            + ". A wider grant is a privilege that cannot be exercised; a"
            " narrower one makes the policy's writes silent no-ops under FORCE RLS."
        )


def test_the_test_fixture_provisions_the_same_grants_as_production():
    """conftest.py is the suite's counterpart of the provisioning script, and
    the script's header asks that the two be kept in sync. If they drift, the
    suite stops testing the privileges a deployment actually runs with."""
    policies = _policy_commands()
    granted = _conftest_granted_commands()
    for table in sorted(_RLS_TABLES):
        assert granted[table] == policies[table], (
            table + ": conftest grants " + repr(sorted(granted[table]))
            + ", policies admit " + repr(sorted(policies[table]))
        )
