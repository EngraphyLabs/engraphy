"""Every statement the engine aims at `pending_writes` names space AND author.

`pending_writes` holds a parked write verbatim -- the incoming title and body
plus a candidate snapshot that may quote content from any scope the author can
read -- so migration 0008's `pending_writes_read` and 0011's
`pending_writes_delete` restrict both to `space_id = <caller's space> AND
author_principal = <caller>`. Under the app role those policies are the live
guard and an explicit WHERE is belt and suspenders.

The engine does not only run on the app role, which is what this test is for.
`engraphy-admin space import` replays a bundle on the operator pool
(admin/cli.py builds it from ENGRAPHY_DATABASE_URL, the operator-privileged
connection) and calls `core.dedup.resolve_duplicate` on it to settle a node its
own write parked. A superuser bypasses RLS, so on that connection the WHERE
clause is not belt and suspenders: it is the only thing scoping the statement,
and an unqualified `DELETE ... WHERE id = %s` would reach any row in any space.

A grep, in the idiom of test_db_guc_protocol.py's CI grep rule, because the
invariant is about every statement that exists rather than about the behaviour
of the ones a live-Postgres test happens to call -- including a statement added
later, which is exactly the case a behavioural test would not cover.
"""
import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).parents[2]

# The modules allowed to address pending_writes at all. core.dedup parks,
# resolves, discards and sweeps; core.pending lists.
_SOURCES = (
    pathlib.Path("engraphy/core/dedup.py"),
    pathlib.Path("engraphy/core/pending.py"),
)

# One SQL statement per match, with its trailing string-concatenation pieces
# joined, so a statement split across adjacent literals is read whole.
_STATEMENT_RE = re.compile(
    r'"((?:SELECT|INSERT INTO|UPDATE|DELETE FROM)[^"]*pending_writes[^"]*)"'
    r'((?:\s*"[^"]*")*)'
)


def _statements(text: str) -> list[str]:
    out = []
    for head, tail in _STATEMENT_RE.findall(text):
        out.append(head + "".join(re.findall(r'"([^"]*)"', tail)))
    return out


def test_every_pending_writes_statement_is_scoped_to_space_and_author():
    checked = 0
    for rel in _SOURCES:
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        for statement in _statements(text):
            checked += 1
            if statement.lstrip().startswith("INSERT INTO"):
                # The INSERT supplies both columns as values rather than
                # matching on them; 0008's WITH CHECK is what pins them.
                assert "author_principal" in statement, f"{rel}: {statement}"
                continue
            assert "space_id = %s" in statement, (
                f"{rel}: statement is not space-scoped: {statement}"
            )
            assert "author_principal = %s" in statement, (
                f"{rel}: statement is not author-scoped: {statement}"
            )
    # Guards the grep itself: a refactor that moved these statements, or a
    # regex that silently stopped matching, must fail here rather than pass
    # by checking nothing.
    assert checked >= 5, f"expected at least 5 pending_writes statements, found {checked}"
