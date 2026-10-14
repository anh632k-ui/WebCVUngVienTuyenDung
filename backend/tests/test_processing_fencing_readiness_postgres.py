from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.processing_fencing import (
    ProcessingFencingSchemaNotReady,
    verify_processing_fencing_schema,
)

pytestmark = pytest.mark.skipif(
    os.getenv("GITHUB_ACTIONS") != "true" or not os.getenv("DATABASE_URL"),
    reason="destructive disposable-database catalog QA runs only on GitHub Actions",
)

TABLES = (
    ("resumes", "parsing_status"),
    ("job_descriptions", "parsing_status"),
    ("match_results", "status"),
)
INDEX_NAMES = {
    "resumes": "idx_resumes_processing_lease",
    "job_descriptions": "idx_jobs_processing_lease",
    "match_results": "idx_matches_processing_lease",
}


def _ci_url():
    assert os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
    url = make_url(os.environ["DATABASE_URL"])
    assert url.host in {"127.0.0.1", "localhost", "::1"}
    return url


async def _create_tables(connection) -> None:
    for table_name, status_column in TABLES:
        await connection.execute(
            text(
                f"CREATE TABLE public.{table_name} ("
                "id uuid NOT NULL, "
                f"{status_column} varchar(20) NOT NULL DEFAULT 'PENDING', "
                "processing_attempt_epoch bigint NOT NULL DEFAULT 0, "
                "processing_owner_token uuid NULL, "
                "processing_activation_token uuid NULL, "
                "processing_heartbeat_at timestamp with time zone NULL, "
                "processing_lease_expires_at timestamp with time zone NULL)"
            )
        )


def _canonical_index(table_name: str, status_column: str) -> str:
    return (
        f"CREATE INDEX {INDEX_NAMES[table_name]} ON public.{table_name} "
        "(processing_lease_expires_at, id) "
        f"WHERE {status_column} = 'PROCESSING' "
        "AND processing_lease_expires_at IS NOT NULL"
    )


async def _create_case_indexes(connection, case: str) -> None:
    for table_name, status_column in TABLES:
        if table_name != "resumes":
            await connection.execute(text(_canonical_index(table_name, status_column)))
            continue

        index_name = INDEX_NAMES[table_name]
        if case == "missing":
            continue
        if case == "plain":
            ddl = (
                f"CREATE INDEX {index_name} ON public.{table_name} "
                "(processing_lease_expires_at, id)"
            )
        elif case == "broader":
            ddl = (
                f"CREATE INDEX {index_name} ON public.{table_name} "
                "(processing_lease_expires_at, id) WHERE parsing_status = 'PROCESSING'"
            )
        elif case == "wrong_method":
            ddl = (
                f"CREATE INDEX {index_name} ON public.{table_name} "
                "USING hash (processing_lease_expires_at)"
            )
        elif case == "unique":
            ddl = (
                f"CREATE UNIQUE INDEX {index_name} ON public.{table_name} "
                "(processing_lease_expires_at, id) "
                "WHERE parsing_status = 'PROCESSING' "
                "AND processing_lease_expires_at IS NOT NULL"
            )
        elif case == "include":
            ddl = (
                f"CREATE INDEX {index_name} ON public.{table_name} "
                "(processing_lease_expires_at, id) INCLUDE (parsing_status) "
                "WHERE parsing_status = 'PROCESSING' "
                "AND processing_lease_expires_at IS NOT NULL"
            )
        else:
            ddl = _canonical_index(table_name, status_column)
        await connection.execute(text(ddl))

    if case == "invalid":
        await connection.execute(text("DROP INDEX public.idx_resumes_processing_lease"))
        await connection.execute(
            text(
                "INSERT INTO public.resumes "
                "(id, parsing_status, processing_lease_expires_at) VALUES "
                "('10000000-0000-0000-0000-000000000001', 'PROCESSING', "
                " '2026-01-01T00:00:00Z'), "
                "('10000000-0000-0000-0000-000000000001', 'PROCESSING', "
                " '2026-01-01T00:00:00Z')"
            )
        )
        with pytest.raises(DBAPIError):
            await connection.execute(
                text(
                    "CREATE UNIQUE INDEX CONCURRENTLY idx_resumes_processing_lease "
                    "ON public.resumes (processing_lease_expires_at, id) "
                    "WHERE parsing_status = 'PROCESSING' "
                    "AND processing_lease_expires_at IS NOT NULL"
                )
            )
        await connection.rollback()
        validity = await connection.scalar(
            text(
                "SELECT indisvalid FROM pg_catalog.pg_index "
                "WHERE indexrelid = 'public.idx_resumes_processing_lease'::regclass"
            )
        )
        assert validity is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "ready"),
    [
        ("canonical", True),
        ("missing", False),
        ("invalid", False),
        ("plain", False),
        ("broader", False),
        ("wrong_method", False),
        ("unique", False),
        ("include", False),
    ],
)
async def test_readiness_requires_exact_canonical_indexes(case: str, ready: bool) -> None:
    base_url = _ci_url()
    database_name = f"r3c_readiness_{case}_{uuid.uuid4().hex}"
    admin_engine = create_async_engine(
        base_url.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    target_engine = None
    try:
        async with admin_engine.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{database_name}"'))

        target_engine = create_async_engine(
            base_url.set(database=database_name), isolation_level="AUTOCOMMIT"
        )
        async with target_engine.connect() as connection:
            await _create_tables(connection)
            await _create_case_indexes(connection, case)
        session_factory = async_sessionmaker(
            target_engine, expire_on_commit=False, class_=AsyncSession
        )

        if ready:
            await verify_processing_fencing_schema(session_factory)
        else:
            with pytest.raises(ProcessingFencingSchemaNotReady, match="not ready"):
                await verify_processing_fencing_schema(session_factory)
    finally:
        if target_engine is not None:
            await target_engine.dispose()
        async with admin_engine.connect() as connection:
            drop_database = text(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)')
            await connection.execute(drop_database)
        await admin_engine.dispose()
