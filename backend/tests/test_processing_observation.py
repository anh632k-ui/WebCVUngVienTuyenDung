from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.config import Settings
from app.services import processing_observation
from app.services.processing_observation import observe_old_processing, select_old_processing


class FakeSession:
    def __init__(self, result_sets: list[list[Any]]) -> None:
        self._result_sets = iter(result_sets)
        self.closed = False
        self.execute_count = 0

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *args: Any) -> None:
        del args
        self.closed = True

    async def execute(self, statement: Any) -> Any:
        assert statement.is_select is True
        self.execute_count += 1
        rows = next(self._result_sets)
        return SimpleNamespace(all=lambda: rows)


@pytest.mark.asyncio
async def test_observation_is_disabled_by_default_without_opening_database() -> None:
    opened = False

    def session_factory() -> Any:
        nonlocal opened
        opened = True
        raise AssertionError("disabled observation opened a database session")

    report = await observe_old_processing(Settings(_env_file=None), session_factory)

    assert report.enabled is False
    assert report.cutoff is None
    assert report.candidates == ()
    assert opened is False


@pytest.mark.asyncio
async def test_selection_reads_only_bounded_non_sensitive_metadata() -> None:
    now = datetime(2026, 10, 12, tzinfo=UTC)
    resume_id, job_id, match_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    session = FakeSession(
        [
            [SimpleNamespace(id=resume_id, revision=3, updated_at=now, is_deleted=False)],
            [
                SimpleNamespace(
                    id=job_id,
                    revision=5,
                    updated_at=now + timedelta(seconds=1),
                    is_deleted=True,
                )
            ],
            [
                SimpleNamespace(
                    id=match_id,
                    generation=7,
                    updated_at=now + timedelta(seconds=2),
                    resume_revision=3,
                    job_revision=5,
                )
            ],
        ]
    )

    candidates = await select_old_processing(
        lambda: session,  # type: ignore[arg-type]
        cutoff=now + timedelta(minutes=30),
        batch_size=3,
    )

    assert session.closed is True
    assert session.execute_count == 3
    assert [
        (item.resource_kind, item.resource_id, item.attempt_version) for item in candidates
    ] == [
        ("resume", resume_id, 3),
        ("job", job_id, 5),
        ("match", match_id, 7),
    ]
    assert candidates[1].is_deleted is True
    assert candidates[2].resume_revision == 3
    assert candidates[2].job_revision == 5
    assert all(not hasattr(item, "content") for item in candidates)


@pytest.mark.asyncio
async def test_enabled_observation_uses_threshold_and_logs_counts_without_ids_or_content(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    now = datetime(2026, 10, 12, 4, 0, tzinfo=UTC)
    resource_id = uuid.uuid4()
    selected = (
        processing_observation.ProcessingObservation(
            "resume", resource_id, 9, now - timedelta(hours=1)
        ),
    )

    async def select(*args: Any, **kwargs: Any) -> Any:
        del args
        assert kwargs == {"cutoff": now - timedelta(minutes=30), "batch_size": 17}
        return selected

    monkeypatch.setattr(processing_observation, "select_old_processing", select)
    settings = Settings(
        _env_file=None,
        processing_observation_enabled=True,
        processing_observation_threshold_seconds=1_800,
        processing_observation_batch_size=17,
    )

    report = await observe_old_processing(settings, lambda: SimpleNamespace(), now=now)

    assert report.enabled is True
    assert report.candidates == selected
    assert "old_processing_observed total=1 resumes=1 jobs=0 matches=0" in caplog.text
    assert str(resource_id) not in caplog.text
    assert "CV content" not in caplog.text
