from __future__ import annotations

import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = REPOSITORY_ROOT / "docs" / "05_database" / "migrations"


def read_artifact(name: str) -> str:
    return (MIGRATIONS / name).read_text(encoding="utf-8")


def test_expand_declares_exact_phase1_shape() -> None:
    sql = read_artifact("r3c_phase1_expand.sql")
    for table in ("resumes", "job_descriptions", "match_results"):
        assert sql.count(f"('{table}', 'processing_") == 5
    assert sql.count("processing_attempt_epoch', 'bigint', true, '0'") == 3
    assert sql.count("processing_owner_token', 'uuid', false, NULL") == 3
    assert sql.count("processing_activation_token', 'uuid', false, NULL") == 3
    assert sql.count("processing_heartbeat_at', 'timestamp with time zone', false, NULL") == 3
    assert sql.count("processing_lease_expires_at', 'timestamp with time zone', false, NULL") == 3


def test_expand_is_fail_closed_and_bounded() -> None:
    sql = read_artifact("r3c_phase1_expand.sql")
    assert "NOT AUTHORIZED FOR LOCAL/PRODUCTION EXECUTION" in sql
    assert "BEGIN;" in sql and "COMMIT;" in sql
    assert "SET LOCAL lock_timeout" in sql
    assert "SET LOCAL statement_timeout" in sql
    assert "ADD COLUMN IF NOT EXISTS" not in sql.upper()
    assert "RAISE EXCEPTION" in sql
    assert "CREATE TRIGGER" not in sql.upper()
    assert not re.search(r"CHECK\s*\([^)]*processing_owner", sql, re.IGNORECASE)


def test_indexes_are_concurrent_partial_lease_indexes() -> None:
    sql = read_artifact("r3c_phase1_indexes.sql")
    assert "NOT AUTHORIZED FOR LOCAL/PRODUCTION EXECUTION" in sql
    assert sql.count("CREATE INDEX CONCURRENTLY") == 3
    assert "BEGIN;" not in sql and "START TRANSACTION" not in sql.upper()
    assert sql.count("(processing_lease_expires_at, id)") == 3
    assert sql.count("processing_lease_expires_at IS NOT NULL") >= 3
    assert "indisvalid" in sql
    assert "DROP INDEX" not in sql.upper()


def test_migration_does_not_modify_runtime_or_canonical_bootstrap() -> None:
    expand = read_artifact("r3c_phase1_expand.sql")
    indexes = read_artifact("r3c_phase1_indexes.sql")
    combined = expand + indexes
    assert "UPDATE public.resumes" not in combined
    assert "UPDATE public.job_descriptions" not in combined
    assert "UPDATE public.match_results" not in combined
    assert "CREATE TABLE" not in combined.upper()


def test_ci_runner_has_multiple_identity_gates() -> None:
    runner = (REPOSITORY_ROOT / ".github" / "scripts" / "r3c_phase1_migration_ci.sh").read_text(
        encoding="utf-8"
    )
    for marker in (
        "GITHUB_ACTIONS",
        "RUNNER_ENVIRONMENT",
        "GITHUB_REPOSITORY",
        "R3C_DATABASE_NAME",
        "R3C_DATABASE_HOST",
        "R3C_DATABASE_PORT",
        "server_version_num",
        "table_type='BASE TABLE'",
        "SELECT count(*) FROM public.skills",
    ):
        assert marker in runner
