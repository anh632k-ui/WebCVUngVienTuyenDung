from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest

from app.ai.job_parser import JobParseError, JobParseErrorKind
from app.ai.job_schemas import ParsedJobResult, ParsedJobSkill
from app.ai.schemas import TaxonomySkill
from app.ai.vector_embedding import BGE_M3_EMBEDDING_DIMENSION, BGE_M3_MODEL_NAME
from app.workers import job_parse_worker
from app.workers.job_parse_worker import (
    JobParseContext,
    JobParseTaskOutcome,
    process_job_parse_task,
)


class DummySession:
    async def __aenter__(self) -> DummySession:
        return self

    async def __aexit__(self, *args: object) -> None:
        del args


class FakeEmbeddingProvider:
    model_name = BGE_M3_MODEL_NAME
    dimension = BGE_M3_EMBEDDING_DIMENSION

    def __init__(self, events: list[str], values: list[float] | None = None) -> None:
        self.events = events
        self.values = values or [0.25] * self.dimension

    async def embed(self, text: str) -> list[float]:
        self.events.append(f"embed:{text}")
        return self.values


def session_factory() -> DummySession:
    return DummySession()


def parse_context() -> JobParseContext:
    return JobParseContext(
        raw_content="Requirements\nPython required",
        taxonomy=(TaxonomySkill(1, "Python", "python", "HARD", "Language"),),
    )


def parsed_result(*, skill_id: int = 1) -> ParsedJobResult:
    return ParsedJobResult(
        normalized_text="Requirements\nPython required",
        min_experience_years=Decimal("2.0"),
        education_requirement="Bachelor's degree",
        skills=(
            ParsedJobSkill(
                skill_id=skill_id,
                name="Python",
                normalized_name="python",
                importance="MANDATORY",
                min_years_required=Decimal("1.0"),
            ),
        ),
    )


def patch_claim_and_context(
    monkeypatch: pytest.MonkeyPatch,
    events: list[str],
    *,
    claimed: bool = True,
    context: JobParseContext | None = None,
) -> None:
    async def claim(*args: Any, **kwargs: Any) -> bool:
        del args, kwargs
        events.append("claim")
        return claimed

    async def load(*args: Any, **kwargs: Any) -> JobParseContext | None:
        del args, kwargs
        events.append("context")
        return parse_context() if context is None else context

    monkeypatch.setattr(job_parse_worker, "claim_job_revision", claim)
    monkeypatch.setattr(job_parse_worker, "_load_parse_context", load)


@pytest.mark.asyncio
async def test_claim_miss_discards_without_context_parser_or_embedding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    patch_claim_and_context(monkeypatch, events, claimed=False)

    outcome = await process_job_parse_task(
        uuid.uuid4(),
        1,
        session_factory=session_factory,  # type: ignore[arg-type]
        embedding_provider=FakeEmbeddingProvider(events),
    )

    assert outcome is JobParseTaskOutcome.DISCARDED
    assert events == ["claim"]


@pytest.mark.asyncio
async def test_worker_orders_claim_context_parser_embedding_and_terminal_cas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    patch_claim_and_context(monkeypatch, events)

    def parse(*args: Any, **kwargs: Any) -> ParsedJobResult:
        del args, kwargs
        events.append("parse")
        return parsed_result()

    async def persist(*args: Any, **kwargs: Any) -> bool:
        del args
        assert kwargs["expected_revision"] == 7
        assert kwargs["parsed"] == parsed_result()
        assert len(kwargs["embedding"]) == BGE_M3_EMBEDDING_DIMENSION
        events.append("persist")
        return True

    monkeypatch.setattr(job_parse_worker, "parse_job_description", parse)
    monkeypatch.setattr(job_parse_worker, "_persist_success", persist)

    outcome = await process_job_parse_task(
        uuid.uuid4(),
        7,
        session_factory=session_factory,  # type: ignore[arg-type]
        embedding_provider=FakeEmbeddingProvider(events),
    )

    assert outcome is JobParseTaskOutcome.PARSED
    assert events == [
        "claim",
        "context",
        "parse",
        "embed:Requirements\nPython required",
        "persist",
    ]


@pytest.mark.asyncio
async def test_context_stale_after_claim_discards_without_expensive_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    patch_claim_and_context(monkeypatch, events)

    async def stale_context(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        events.append("context")
        return None

    monkeypatch.setattr(job_parse_worker, "_load_parse_context", stale_context)

    outcome = await process_job_parse_task(
        uuid.uuid4(),
        1,
        session_factory=session_factory,  # type: ignore[arg-type]
        embedding_provider=FakeEmbeddingProvider(events),
    )

    assert outcome is JobParseTaskOutcome.DISCARDED
    assert events == ["claim", "context"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["context", "parser", "invariant", "embedding"])
async def test_claimed_stage_failure_attempts_controlled_failed_cas(
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    events: list[str] = []
    patch_claim_and_context(monkeypatch, events)
    messages: list[str] = []

    if failure_stage == "context":

        async def failing_context(*args: Any, **kwargs: Any) -> JobParseContext:
            del args, kwargs
            raise RuntimeError("postgresql://secret")

        monkeypatch.setattr(job_parse_worker, "_load_parse_context", failing_context)

    def parse(*args: Any, **kwargs: Any) -> ParsedJobResult:
        del args, kwargs
        if failure_stage == "parser":
            raise JobParseError(JobParseErrorKind.INVALID_INPUT, "private JD content")
        if failure_stage == "invariant":
            return parsed_result(skill_id=999)
        return parsed_result()

    async def mark_failed(*args: Any, **kwargs: Any) -> JobParseTaskOutcome:
        del args
        messages.append(kwargs["message"])
        return JobParseTaskOutcome.FAILED

    values = [0.0] * (BGE_M3_EMBEDDING_DIMENSION - 1) if failure_stage == "embedding" else None
    monkeypatch.setattr(job_parse_worker, "parse_job_description", parse)
    monkeypatch.setattr(job_parse_worker, "_mark_failed", mark_failed)

    outcome = await process_job_parse_task(
        uuid.uuid4(),
        1,
        session_factory=session_factory,  # type: ignore[arg-type]
        embedding_provider=FakeEmbeddingProvider(events, values),
    )

    assert outcome is JobParseTaskOutcome.FAILED
    assert len(messages) == 1
    assert "secret" not in messages[0] and "private" not in messages[0]


@pytest.mark.asyncio
async def test_terminal_cas_miss_discards_without_failed_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    patch_claim_and_context(monkeypatch, events)
    monkeypatch.setattr(job_parse_worker, "parse_job_description", lambda *args: parsed_result())

    async def terminal_miss(*args: Any, **kwargs: Any) -> bool:
        del args, kwargs
        return False

    async def unexpected_failure(*args: Any, **kwargs: Any) -> JobParseTaskOutcome:
        del args, kwargs
        raise AssertionError("terminal CAS miss must not be marked FAILED")

    monkeypatch.setattr(job_parse_worker, "_persist_success", terminal_miss)
    monkeypatch.setattr(job_parse_worker, "_mark_failed", unexpected_failure)

    outcome = await process_job_parse_task(
        uuid.uuid4(),
        1,
        session_factory=session_factory,  # type: ignore[arg-type]
        embedding_provider=FakeEmbeddingProvider(events),
    )

    assert outcome is JobParseTaskOutcome.DISCARDED
