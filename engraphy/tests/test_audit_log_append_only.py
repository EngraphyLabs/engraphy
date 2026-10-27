"""`audit_log` is append-only, and the app role's grants say so.

design/03 §Audit makes audit_log the record of what an identity did. Its value
depends on the process that writes it being unable to rewrite it: an engine bug,
or a token that reaches an unintended code path, can then add a row but never
remove or alter one. `audit_log` carries no RLS policy (0008; it is
instance-level, like api_tokens), so the table grants are the whole of the
control, and deploy/provision-app-role.sql is where they are stated.

Two greps rather than a live-Postgres probe. The grant assertion is about the
provisioning script a deployment actually runs, which no test database state can
speak for; the statement assertion is about every statement that exists rather
than the ones a behavioural test happens to reach. A live check that the role
cannot UPDATE audit_log is the natural complement and needs a provisioned
cluster to be meaningful.
"""
import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).parents[2]
PROVISION_SQL = REPO_ROOT / "deploy" / "provision-app-role.sql"

# Where the engine is allowed to touch audit_log at all.
_ENGINE_DIRS = ("engraphy/core", "engraphy/server", "engraphy/admin")


def test_provisioning_grants_the_app_role_no_write_on_audit_log_beyond_insert():
    sql = PROVISION_SQL.read_text(encoding="utf-8")
    grants = [
        line.strip() for line in sql.splitlines()
        if re.match(r"\s*GRANT\b", line) and "audit_log" in line
    ]
    assert grants, "provision-app-role.sql grants nothing on audit_log"
    for line in grants:
        privileges = re.match(r"GRANT\s+(.*?)\s+ON\b", line).group(1).upper()
        for forbidden in ("UPDATE", "DELETE", "TRUNCATE"):
            assert forbidden not in privileges, (
                f"audit_log is append-only; provisioning grants {forbidden}: {line}"
            )
    assert re.search(r"REVOKE\s+.*\bON\s+audit_log\b", sql), (
        "provisioning must REVOKE the wider privileges too, so re-running it "
        "converges a cluster provisioned under an earlier grant"
    )


def test_the_engine_only_ever_inserts_into_audit_log():
    offenders = []
    for rel in _ENGINE_DIRS:
        for path in (REPO_ROOT / rel).rglob("*.py"):
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not re.search(r"(UPDATE|DELETE\s+FROM|TRUNCATE)\s+audit_log\b", line):
                    continue
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")
    assert offenders == [], (
        "audit_log is append-only, so the engine issues only INSERTs against it; "
        f"found {offenders}"
    )
