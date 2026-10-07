from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from app.ai.matching_engine import (
    ALGORITHM_VERSION,
    MatchComputationError,
    MatchComputationErrorKind,
)
from app.ai.matching_schemas import (
    CandidateExperience,
    CandidateSkill,
    EmbeddingInput,
    JobSkillRequirement,
    MatchComputationResult,
    MatchWeights,
    SkillEvidence,
    SkillEvidenceStatus,
    SkillGapSeverity,
    SkillImportance,
)
from app.workers import match_worker
from app.workers.match_worker import (
    MatchComputationContext,
    MatchTaskOutcome,
    process_match_task,
)


class DummySession:
    async def __aenter__(self) -> DummySession:
        return self

    async def __aexit__(self, *args: object) -> None:
        del args


def session_factory() -> DummySession:
    return DummySession()


def computation_context() -> MatchComputationContext:
    return MatchComputationContext(
        resume_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        candidate_skills=(CandidateSkill(1, "Python", Decimal("3.0")),),
        job_skills=(JobSkillRequirement(1, "Python", SkillImportance.MANDATORY, Decimal("2.0")),),
        candidate_experiences=(CandidateExperience(date(2020, 1, 1), None, True),),
        min_experience_years=Decimal("1.0"),
        weights=MatchWeights(Decimal("0.5"), Decimal("0.3"), Decimal("0.2")),
        embeddings=EmbeddingInput(
            [1.0] * 1024,
            [1.0] * 1024,
            "BAAI/bge-m3",
            "BAAI/bge-m3",
            "text-v1",
            "text-v1",
        ),
    )


def computation_result() -> MatchComputationResult:
    return MatchComputationResult(
        overall_score=Decimal("100.00"),
        skill_score=Decimal("100.00"),
        semantic_score=Decimal("100.00"),
        experience_score=Decimal("100.00"),
        algorithm_version=ALGORITHM_VERSION,
        matched_skills=(
            SkillEvidence(
                skill_id=1,
                name="Python",
                importance=SkillImportance.MANDATORY,
                status=SkillEvidenceStatus.MATCHED,
                candidate_years=Decimal("3.0"),
                required_years=Decimal("2.0"),
            ),
        ),
        missing_skills=(
            SkillEvidence(
                skill_id=2,
                name="SQL",
                importance=SkillImportance.OPTIONAL,
                status=SkillEvidenceStatus.MISSING,
                candidate_years=None,
                required_years=Decimal("1.5"),
                severity=SkillGapSeverity.MINOR,
            ),
        ),
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

    async def load(*args: Any, **kwargs: Any) -> MatchComputationContext:
        del args, kwargs
        events.append("load")
        return computation_context()

    monkeypatch.setattr(match_worker, "claim_match_generation", claim)
    monkeypatch.setattr(match_worker, "_load_computation_context", load)


@pytest.mark.asyncio
async def test_unsupported_algorithm_discards_before_opening_session() -> None:
    opened = False

    def forbidden_factory() -> DummySession:
        nonlocal opened
        opened = True
        raise AssertionError

    outcome = await process_match_task(
        uuid.uuid4(),
        1,
        1,
        1,
        "future-v2",
        session_factory=forbidden_factory,  # type: ignore[arg-type]
    )

    assert outcome is MatchTaskOutcome.DISCARDED
    assert opened is False


@pytest.mark.asyncio
async def test_claim_miss_discards_without_loading_or_computing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    patch_claim_and_context(monkeypatch, events, claimed=False)
    monkeypatch.setattr(match_worker, "compute_match", lambda **kwargs: events.append("compute"))

    outcome = await process_match_task(
        uuid.uuid4(),
        3,
        4,
        5,
        ALGORITHM_VERSION,
        session_factory=session_factory,  # type: ignore[arg-type]
    )

    assert outcome is MatchTaskOutcome.DISCARDED
    assert events == ["claim"]


@pytest.mark.asyncio
async def test_success_orders_claim_load_compute_terminal_and_uses_date_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    patch_claim_and_context(monkeypatch, events)
    dates = 0

    def provide_date() -> date:
        nonlocal dates
        dates += 1
        events.append("date")
        return date(2026, 10, 7)

    def compute(**kwargs: Any) -> MatchComputationResult:
        assert kwargs["as_of_date"] == date(2026, 10, 7)
        events.append("compute")
        return computation_result()

    async def persist(*args: Any, **kwargs: Any) -> bool:
        del args
        assert kwargs["computed"] == computation_result()
        events.append("persist")
        return True

    monkeypatch.setattr(match_worker, "compute_match", compute)
    monkeypatch.setattr(match_worker, "_persist_success", persist)

    outcome = await process_match_task(
        uuid.uuid4(),
        3,
        4,
        5,
        ALGORITHM_VERSION,
        session_factory=session_factory,  # type: ignore[arg-type]
        date_provider=provide_date,
    )

    assert outcome is MatchTaskOutcome.COMPLETED
    assert dates == 1
    assert events == ["claim", "load", "date", "compute", "persist"]


@pytest.mark.asyncio
async def test_terminal_cas_miss_discards_without_failed_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    patch_claim_and_context(monkeypatch, events)
    monkeypatch.setattr(match_worker, "compute_match", lambda **kwargs: computation_result())

    async def terminal_miss(*args: Any, **kwargs: Any) -> bool:
        del args, kwargs
        return False

    async def forbidden_failure(*args: Any, **kwargs: Any) -> MatchTaskOutcome:
        del args, kwargs
        raise AssertionError("terminal miss must never race a FAILED write")

    monkeypatch.setattr(match_worker, "_persist_success", terminal_miss)
    monkeypatch.setattr(match_worker, "_mark_failed", forbidden_failure)

    outcome = await process_match_task(
        uuid.uuid4(),
        1,
        1,
        1,
        ALGORITHM_VERSION,
        session_factory=session_factory,  # type: ignore[arg-type]
    )

    assert outcome is MatchTaskOutcome.DISCARDED


@pytest.mark.asyncio
@pytest.mark.parametrize("controlled", [True, False])
async def test_compute_failure_uses_only_safe_generic_message(
    monkeypatch: pytest.MonkeyPatch,
    controlled: bool,
) -> None:
    events: list[str] = []
    messages: list[str] = []
    patch_claim_and_context(monkeypatch, events)

    def fail(**kwargs: Any) -> MatchComputationResult:
        del kwargs
        if controlled:
            raise MatchComputationError(
                MatchComputationErrorKind.INVALID_INPUT,
                "secret scoring payload",
            )
        raise RuntimeError("postgresql://private")

    async def mark_failed(*args: Any, **kwargs: Any) -> MatchTaskOutcome:
        del args
        messages.append(kwargs["message"])
        return MatchTaskOutcome.FAILED

    monkeypatch.setattr(match_worker, "compute_match", fail)
    monkeypatch.setattr(match_worker, "_mark_failed", mark_failed)

    outcome = await process_match_task(
        uuid.uuid4(),
        1,
        1,
        1,
        ALGORITHM_VERSION,
        session_factory=session_factory,  # type: ignore[arg-type]
    )

    assert outcome is MatchTaskOutcome.FAILED
    assert messages == [
        "Match computation failed: INVALID_INPUT" if controlled else "Match computation failed"
    ]
    assert "secret" not in messages[0] and "private" not in messages[0]


def test_evidence_serialization_is_complete_order_stable_and_json_numeric() -> None:
    result = computation_result()

    serialized = [
        match_worker._serialize_evidence(item)
        for item in (*result.matched_skills, *result.missing_skills)
    ]

    assert [item["skill_id"] for item in serialized] == [1, 2]
    assert serialized[0] == {
        "skill_id": 1,
        "name": "Python",
        "importance": "MANDATORY",
        "status": "MATCHED",
        "candidate_years": 3.0,
        "required_years": 2.0,
        "severity": None,
    }
    assert serialized[1]["candidate_years"] is None
    assert serialized[1]["severity"] == "MINOR"
