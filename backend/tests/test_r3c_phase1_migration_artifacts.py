from __future__ import annotations

import re
from hashlib import sha1
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


def test_index_predicate_validation_is_null_safe_and_consistent() -> None:
    indexes = read_artifact("r3c_phase1_indexes.sql")
    verification = (MIGRATIONS / "tests" / "r3c_phase1_verify.sql").read_text(encoding="utf-8")
    runner = (REPOSITORY_ROOT / ".github" / "scripts" / "r3c_phase1_migration_ci.sh").read_text(
        encoding="utf-8"
    )

    assert indexes.count("normalized_predicate IS DISTINCT FROM") == 2
    assert verification.count("normalized_predicate IS DISTINCT FROM") == 1
    assert "normalized_predicate <>" not in indexes + verification
    for metadata_check in (
        "object_kind <> 'i'",
        "table_name <> expected.table_name",
        "access_method <> 'btree'",
        "indisvalid",
        "indisunique",
        "indnkeyatts <> 2",
        "indnatts <> 2",
        "columns <> ARRAY['processing_lease_expires_at', 'id']",
    ):
        assert metadata_check in indexes
        assert metadata_check in verification
    assert "same-shape index without partial predicate" in runner
    assert "final verification of index without partial predicate" in runner
    assert "rejected index without partial predicate was mutated or replaced" in runner


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


def test_canonical_and_legacy_bootstraps_are_explicitly_separated() -> None:
    canonical = (REPOSITORY_ROOT / "docs" / "05_database" / "schema.sql").read_text(
        encoding="utf-8"
    )
    legacy = read_artifact("tests/r3c_pre_fencing_schema.sql")
    runner = (REPOSITORY_ROOT / ".github" / "scripts" / "r3c_phase1_migration_ci.sh").read_text(
        encoding="utf-8"
    )

    assert canonical.count("processing_attempt_epoch BIGINT NOT NULL DEFAULT 0") == 3
    for column in (
        "processing_owner_token UUID",
        "processing_activation_token UUID",
        "processing_heartbeat_at TIMESTAMPTZ",
        "processing_lease_expires_at TIMESTAMPTZ",
    ):
        assert canonical.count(column) == 3
    assert canonical.count("CREATE INDEX idx_") >= 3
    for index_name in (
        "idx_resumes_processing_lease",
        "idx_jobs_processing_lease",
        "idx_matches_processing_lease",
    ):
        assert canonical.count(index_name) == 1
        assert index_name not in legacy

    assert "a38ec82d81e915581766d0ae6c38c717696a8724" in legacy
    assert "processing_attempt_epoch BIGINT" not in legacy
    fixture_body = "\n".join(legacy.splitlines()[7:]) + "\n"
    fixture_blob = sha1(
        f"blob {len(fixture_body.encode())}\0".encode() + fixture_body.encode(),
        usedforsecurity=False,
    ).hexdigest()
    assert fixture_blob == "8ca16eb6cc0971ea2c82d3b7fe18a8901629c1ba"
    assert "readonly LEGACY_SCHEMA=" in runner
    assert "bootstrap_fresh_database" in runner
    assert "bootstrap_legacy_database" in runner


def test_schema_equivalence_gate_compares_catalog_metadata() -> None:
    snapshot = read_artifact("tests/r3c_schema_catalog_snapshot.sql")
    runner = (REPOSITORY_ROOT / ".github" / "scripts" / "r3c_phase1_migration_ci.sh").read_text(
        encoding="utf-8"
    )

    for object_type in (
        "'table'::text",
        "'column'",
        "'primary_key'",
        "'foreign_key'",
        "'unique_constraint'",
        "'check_constraint'",
        "'index'",
    ):
        assert object_type in snapshot
    for metadata in (
        "format_type",
        "attnotnull",
        "pg_get_expr",
        "pg_get_constraintdef",
        "amname",
        "pg_get_indexdef",
        "indpred",
        "indisvalid",
    ):
        assert metadata in snapshot
    assert "diff -u" in runner
    assert "migrated legacy and fresh canonical schemas are not catalog-equivalent" in runner
