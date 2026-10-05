from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.ai.errors import ResumeParseError, ResumeParseErrorKind
from app.ai.schemas import ParsedResumeResult, TaxonomySkill
from app.ai.vector_embedding import BGE_M3_EMBEDDING_DIMENSION, BGE_M3_MODEL_NAME
from app.workers import resume_parse_worker
from app.workers.resume_parse_worker import (
    ResumeParseContext,
    ResumeParseTaskOutcome,
    process_resume_parse_task,
)


class DummySession:
    async def __aenter__(self) -> DummySession:
        return self

    async def __aexit__(self, *args: object) -> None:
        del args


class RecordingStorage:
    def __init__(self, events: list[str], *, fail: bool = False) -> None:
        self.events = events
        self.fail = fail

    async def read_bytes(self, key: str) -> bytes:
        self.events.append(f"storage:{key}")
        if self.fail:
            raise OSError("C:/private/storage/resume.pdf")
        return b"source"


class FakeEmbeddingProvider:
    model_name = BGE_M3_MODEL_NAME
    dimension = BGE_M3_EMBEDDING_DIMENSION

    def __init__(self, events: list[str], values: list[float] | None = None) -> None:
        self.events = events
        self.values = values or [0.5] * BGE_M3_EMBEDDING_DIMENSION

    async def embed(self, text: str) -> list[float]:
        self.events.append(f"embed:{text}")
        return self.values


def session_factory() -> DummySession:
    return DummySession()


def parse_context() -> ResumeParseContext:
    return ResumeParseContext(
        storage_key="resumes/test/source",
        mime_type="application/pdf",
        taxonomy=(TaxonomySkill(1, "Python", "python", "HARD", "Language"),),
    )


def patch_claim_and_context(
    monkeypatch: pytest.MonkeyPatch,
    events: list[str],
    *,
    claimed: bool = True,
) -> None:
    async def claim(*args: Any, **kwargs: Any) -> bool:
        del args, kwargs
        events.append("claim")
        return claimed

    async def load(*args: Any, **kwargs: Any) -> ResumeParseContext:
        del args, kwargs
        events.append("context")
        return parse_context()

    monkeypatch.setattr(resume_parse_worker, "claim_resume_revision", claim)
    monkeypatch.setattr(resume_parse_worker, "_load_parse_context", load)


@pytest.mark.asyncio
async def test_claim_miss_discards_without_storage_parser_or_embedding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    patch_claim_and_context(monkeypatch, events, claimed=False)

    outcome = await process_resume_parse_task(
        uuid.uuid4(),
        1,
        session_factory=session_factory,  # type: ignore[arg-type]
        storage=RecordingStorage(events),
        embedding_provider=FakeEmbeddingProvider(events),
    )

    assert outcome is ResumeParseTaskOutcome.DISCARDED
    assert events == ["claim"]


@pytest.mark.asyncio
async def test_worker_orders_claim_storage_parser_embedding_and_terminal_cas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    patch_claim_and_context(monkeypatch, events)

    def parse(*args: Any, **kwargs: Any) -> ParsedResumeResult:
        del args, kwargs
        events.append("parse")
        return ParsedResumeResult(raw_text="normalized text")

    async def persist(*args: Any, **kwargs: Any) -> bool:
        del args
        assert kwargs["expected_revision"] == 7
        assert len(kwargs["embedding"]) == BGE_M3_EMBEDDING_DIMENSION
        events.append("persist")
        return True

    monkeypatch.setattr(resume_parse_worker, "parse_resume", parse)
    monkeypatch.setattr(resume_parse_worker, "_persist_success", persist)

    outcome = await process_resume_parse_task(
        uuid.uuid4(),
        7,
        session_factory=session_factory,  # type: ignore[arg-type]
        storage=RecordingStorage(events),
        embedding_provider=FakeEmbeddingProvider(events),
    )

    assert outcome is ResumeParseTaskOutcome.PARSED
    assert events == [
        "claim",
        "context",
        "storage:resumes/test/source",
        "parse",
        "embed:normalized text",
        "persist",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["storage", "parser", "embedding"])
async def test_claimed_stage_failure_attempts_controlled_failed_cas(
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    events: list[str] = []
    patch_claim_and_context(monkeypatch, events)
    messages: list[str] = []

    def parse(*args: Any, **kwargs: Any) -> ParsedResumeResult:
        del args, kwargs
        if failure_stage == "parser":
            raise ResumeParseError(ResumeParseErrorKind.UNREADABLE_SOURCE, "private detail")
        return ParsedResumeResult(raw_text="normalized text")

    async def mark_failed(*args: Any, **kwargs: Any) -> ResumeParseTaskOutcome:
        del args
        messages.append(kwargs["message"])
        return ResumeParseTaskOutcome.FAILED

    provider_values = (
        [0.0] * (BGE_M3_EMBEDDING_DIMENSION - 1) if failure_stage == "embedding" else None
    )
    monkeypatch.setattr(resume_parse_worker, "parse_resume", parse)
    monkeypatch.setattr(resume_parse_worker, "_mark_failed", mark_failed)

    outcome = await process_resume_parse_task(
        uuid.uuid4(),
        1,
        session_factory=session_factory,  # type: ignore[arg-type]
        storage=RecordingStorage(events, fail=failure_stage == "storage"),
        embedding_provider=FakeEmbeddingProvider(events, provider_values),
    )

    assert outcome is ResumeParseTaskOutcome.FAILED
    assert len(messages) == 1
    assert "private" not in messages[0] and "C:/" not in messages[0]


@pytest.mark.asyncio
async def test_terminal_cas_miss_discards_without_failed_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    patch_claim_and_context(monkeypatch, events)

    monkeypatch.setattr(
        resume_parse_worker,
        "parse_resume",
        lambda *args, **kwargs: ParsedResumeResult(raw_text="normalized text"),
    )

    async def terminal_miss(*args: Any, **kwargs: Any) -> bool:
        del args, kwargs
        return False

    async def unexpected_failure(*args: Any, **kwargs: Any) -> ResumeParseTaskOutcome:
        del args, kwargs
        raise AssertionError("terminal CAS miss must not be marked FAILED")

    monkeypatch.setattr(resume_parse_worker, "_persist_success", terminal_miss)
    monkeypatch.setattr(resume_parse_worker, "_mark_failed", unexpected_failure)

    outcome = await process_resume_parse_task(
        uuid.uuid4(),
        1,
        session_factory=session_factory,  # type: ignore[arg-type]
        storage=RecordingStorage(events),
        embedding_provider=FakeEmbeddingProvider(events),
    )

    assert outcome is ResumeParseTaskOutcome.DISCARDED
