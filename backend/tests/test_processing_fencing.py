from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.core.config import Settings
from app.core.processing_fencing import (
    ProcessingAttemptGuard,
    ProcessingFencingSchemaNotReady,
    ProcessingResourceKind,
    activate_reclaimed_attempt,
    verify_processing_fencing_schema,
)
from app.models.job import JobDescription
from app.models.match_result import MatchResult
from app.models.resume import Resume


def test_feature_is_disabled_by_default_and_guard_repr_never_contains_tokens() -> None:
    settings = Settings(_env_file=None)
    owner_token = uuid.uuid4()
    activation_token = uuid.uuid4()
    guard = ProcessingAttemptGuard(
        ProcessingResourceKind.RESUME,
        uuid.uuid4(),
        4,
        7,
        owner_token,
        activation_token,
    )

    assert settings.processing_fencing_enabled is False
    assert str(owner_token) not in repr(guard)
    assert str(activation_token) not in repr(guard)


def test_orm_declares_all_attempt_fields_and_exact_partial_lease_indexes() -> None:
    for model, index_name, status_column in (
        (Resume, "idx_resumes_processing_lease", "parsing_status"),
        (JobDescription, "idx_jobs_processing_lease", "parsing_status"),
        (MatchResult, "idx_matches_processing_lease", "status"),
    ):
        table = model.__table__
        assert {
            "processing_attempt_epoch",
            "processing_owner_token",
            "processing_activation_token",
            "processing_heartbeat_at",
            "processing_lease_expires_at",
        }.issubset(table.columns.keys())
        index = next(item for item in table.indexes if item.name == index_name)
        assert [column.name for column in index.columns] == [
            "processing_lease_expires_at",
            "id",
        ]
        predicate = str(index.dialect_options["postgresql"]["where"])
        assert status_column in predicate and "PROCESSING" in predicate


class _Result:
    def __init__(self, row: tuple[int, int]) -> None:
        self._row = row

    def one(self) -> tuple[int, int]:
        return self._row


class _Session:
    def __init__(self, row: tuple[int, int] = (15, 3), *, fail: bool = False) -> None:
        self._row = row
        self._fail = fail

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *args: Any) -> None:
        del args

    async def execute(self, statement: Any) -> _Result:
        del statement
        if self._fail:
            raise RuntimeError("secret database error")
        return _Result(self._row)


@pytest.mark.asyncio
async def test_schema_readiness_refuses_missing_or_unverifiable() -> None:
    await verify_processing_fencing_schema(lambda: _Session())  # type: ignore[arg-type]

    with pytest.raises(ProcessingFencingSchemaNotReady, match="not ready"):
        await verify_processing_fencing_schema(lambda: _Session((14, 3)))  # type: ignore[arg-type]
    with pytest.raises(ProcessingFencingSchemaNotReady, match="could not be verified") as captured:
        await verify_processing_fencing_schema(lambda: _Session(fail=True))  # type: ignore[arg-type]
    assert "secret" not in str(captured.value)


@pytest.mark.asyncio
async def test_activation_rejects_an_already_activated_guard_without_touching_database() -> None:
    guard = ProcessingAttemptGuard(
        ProcessingResourceKind.JOB,
        uuid.uuid4(),
        1,
        2,
        uuid.uuid4(),
        uuid.uuid4(),
    )
    with pytest.raises(ValueError, match="must not already"):
        await activate_reclaimed_attempt(_Session(), guard, lease_seconds=1_800)  # type: ignore[arg-type]
