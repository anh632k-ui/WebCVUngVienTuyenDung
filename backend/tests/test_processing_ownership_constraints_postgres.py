from __future__ import annotations

import os
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

_DATABASE = "r3c_phase4a_heartbeat"
_USER = "r3c_phase4a_ci"

METADATA_CONSISTENCY = """
(
    owner_token IS NULL
    AND activation_token IS NULL
    AND heartbeat_at IS NULL
    AND lease_expires_at IS NULL
)
OR
(
    owner_token IS NOT NULL
    AND lease_expires_at IS NOT NULL
    AND (
        (activation_token IS NULL AND heartbeat_at IS NULL)
        OR
        (activation_token IS NOT NULL AND heartbeat_at IS NOT NULL)
    )
)
"""

ACTIVE_ONLY_WHILE_PROCESSING = "owner_token IS NULL OR state = 'PROCESSING'"
FENCED_ONLY_PROCESSING = "state <> 'PROCESSING' OR owner_token IS NOT NULL"


def _database_url() -> str | None:
    if os.getenv("R3C_PHASE4A_POSTGRES_INTEGRATION") != "true":
        return None
    if os.getenv("GITHUB_ACTIONS") != "true" or os.getenv("RUNNER_ENVIRONMENT") != "github-hosted":
        return None
    raw = os.getenv("R3C_PHASE4A_DATABASE_URL", "")
    parsed = urlsplit(raw)
    if not (
        parsed.scheme == "postgresql+asyncpg"
        and parsed.hostname == "127.0.0.1"
        and parsed.port == 5432
        and parsed.username == _USER
        and parsed.path == f"/{_DATABASE}"
        and not parsed.query
        and not parsed.fragment
    ):
        return None
    return raw


DATABASE_URL = _database_url()
if os.getenv("R3C_PHASE4A_POSTGRES_INTEGRATION") == "true" and DATABASE_URL is None:
    raise RuntimeError("Phase 4A PostgreSQL opt-in is set but the isolated CI guard failed")
pytestmark = pytest.mark.skipif(
    DATABASE_URL is None,
    reason="Constraint QA runs only on the disposable Phase 4A GitHub PostgreSQL database",
)


@pytest_asyncio.fixture
async def constraint_engine() -> AsyncEngine:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _create_state_table(session: AsyncSession, table_name: str) -> None:
    await session.execute(
        text(
            f"""
            CREATE TEMP TABLE {table_name} (
                id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                state text NOT NULL,
                is_deleted boolean NOT NULL DEFAULT false,
                attempt_epoch bigint NOT NULL DEFAULT 0,
                owner_token uuid,
                activation_token uuid,
                heartbeat_at timestamptz,
                lease_expires_at timestamptz
            ) ON COMMIT PRESERVE ROWS
            """
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("table_name", ["resume_states", "job_states", "match_states"])
@pytest.mark.parametrize(
    ("state", "deleted", "epoch", "metadata"),
    [
        ("PENDING", False, 0, "NULL, NULL, NULL, NULL"),
        ("PROCESSING", False, 0, "NULL, NULL, NULL, NULL"),
        (
            "PROCESSING",
            False,
            1,
            "gen_random_uuid(), gen_random_uuid(), clock_timestamp(), "
            "clock_timestamp() + interval '1 minute'",
        ),
        (
            "PROCESSING",
            False,
            2,
            "gen_random_uuid(), NULL, NULL, clock_timestamp() + interval '1 minute'",
        ),
        ("PARSED", False, 3, "NULL, NULL, NULL, NULL"),
        ("COMPLETED", False, 3, "NULL, NULL, NULL, NULL"),
        ("FAILED", False, 4, "NULL, NULL, NULL, NULL"),
        (
            "PROCESSING",
            True,
            5,
            "gen_random_uuid(), gen_random_uuid(), clock_timestamp(), "
            "clock_timestamp() + interval '1 minute'",
        ),
        ("PROCESSING", True, 5, "NULL, NULL, NULL, NULL"),
    ],
)
async def test_metadata_constraint_accepts_current_compatible_state_matrix(
    constraint_engine: AsyncEngine,
    table_name: str,
    state: str,
    deleted: bool,
    epoch: int,
    metadata: str,
) -> None:
    async with AsyncSession(constraint_engine) as session:
        await _create_state_table(session, table_name)
        await session.execute(
            text(
                f"ALTER TABLE {table_name} ADD CONSTRAINT metadata_consistent "
                f"CHECK ({METADATA_CONSISTENCY})"
            )
        )
        await session.execute(
            text(
                f"""
                INSERT INTO {table_name} (
                    state, is_deleted, attempt_epoch, owner_token, activation_token,
                    heartbeat_at, lease_expires_at
                ) VALUES (:state, :deleted, :epoch, {metadata})
                """
            ),
            {"state": state, "deleted": deleted, "epoch": epoch},
        )
        await session.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata",
    [
        "gen_random_uuid(), NULL, NULL, NULL",
        "NULL, gen_random_uuid(), NULL, NULL",
        "NULL, NULL, clock_timestamp(), NULL",
        "NULL, NULL, NULL, clock_timestamp()",
        "gen_random_uuid(), gen_random_uuid(), NULL, clock_timestamp()",
        "gen_random_uuid(), NULL, clock_timestamp(), clock_timestamp()",
        "gen_random_uuid(), gen_random_uuid(), clock_timestamp(), NULL",
    ],
)
async def test_metadata_constraint_rejects_partial_null_combinations(
    constraint_engine: AsyncEngine,
    metadata: str,
) -> None:
    async with AsyncSession(constraint_engine) as session:
        await _create_state_table(session, "invalid_states")
        await session.execute(
            text(
                "ALTER TABLE invalid_states ADD CONSTRAINT metadata_consistent "
                f"CHECK ({METADATA_CONSISTENCY})"
            )
        )
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    f"""
                    INSERT INTO invalid_states (
                        state, owner_token, activation_token, heartbeat_at, lease_expires_at
                    ) VALUES ('PROCESSING', {metadata})
                    """
                )
            )


@pytest.mark.asyncio
async def test_metadata_constraint_rejects_terminal_state_with_active_ownership(
    constraint_engine: AsyncEngine,
) -> None:
    async with AsyncSession(constraint_engine) as session:
        await _create_state_table(session, "invalid_terminal")
        await session.execute(
            text(
                "ALTER TABLE invalid_terminal ADD CONSTRAINT active_only_processing "
                f"CHECK ({ACTIVE_ONLY_WHILE_PROCESSING})"
            )
        )
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    """
                    INSERT INTO invalid_terminal (
                        state, owner_token, activation_token, heartbeat_at, lease_expires_at
                    ) VALUES (
                        'FAILED', gen_random_uuid(), gen_random_uuid(), clock_timestamp(),
                        clock_timestamp() + interval '1 minute'
                    )
                    """
                )
            )


@pytest.mark.asyncio
async def test_not_valid_still_rejects_new_insert_and_update(
    constraint_engine: AsyncEngine,
) -> None:
    async with AsyncSession(constraint_engine) as session:
        await _create_state_table(session, "not_valid_writes")
        await session.execute(
            text(
                "ALTER TABLE not_valid_writes ADD CONSTRAINT metadata_consistent "
                f"CHECK ({METADATA_CONSISTENCY}) NOT VALID"
            )
        )
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await session.execute(
                    text(
                        "INSERT INTO not_valid_writes (state, owner_token) "
                        "VALUES ('PROCESSING', gen_random_uuid())"
                    )
                )
        await session.execute(text("INSERT INTO not_valid_writes (state) VALUES ('PENDING')"))
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await session.execute(
                    text(
                        "UPDATE not_valid_writes SET owner_token = gen_random_uuid() "
                        "WHERE state = 'PENDING'"
                    )
                )


@pytest.mark.asyncio
async def test_fenced_only_constraint_cannot_be_applied_while_legacy_writers_exist(
    constraint_engine: AsyncEngine,
) -> None:
    async with AsyncSession(constraint_engine) as session:
        await _create_state_table(session, "legacy_states")
        await session.execute(
            text("INSERT INTO legacy_states (state) VALUES ('PROCESSING'), ('PENDING')")
        )
        await session.execute(
            text(
                "ALTER TABLE legacy_states ADD CONSTRAINT fenced_only "
                f"CHECK ({FENCED_ONLY_PROCESSING}) NOT VALID"
            )
        )
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await session.execute(
                    text("INSERT INTO legacy_states (state) VALUES ('PROCESSING')")
                )
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await session.execute(
                    text("UPDATE legacy_states SET state = 'PROCESSING' WHERE state = 'PENDING'")
                )
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await session.execute(
                    text("ALTER TABLE legacy_states VALIDATE CONSTRAINT fenced_only")
                )
