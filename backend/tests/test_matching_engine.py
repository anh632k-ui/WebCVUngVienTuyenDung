from __future__ import annotations

import math
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from app.ai.matching_engine import (
    ALGORITHM_VERSION,
    EMBEDDING_DIMENSION,
    MatchComputationError,
    MatchComputationErrorKind,
    compute_match,
)
from app.ai.matching_schemas import (
    CandidateExperience,
    CandidateSkill,
    EmbeddingInput,
    JobSkillRequirement,
    MatchComputationResult,
    MatchWeights,
    SkillEvidenceStatus,
    SkillGapSeverity,
    SkillImportance,
)

ZERO = [0.0] * EMBEDDING_DIMENSION
UNIT_X = [1.0, *ZERO[1:]]
UNIT_Y = [0.0, 1.0, *ZERO[2:]]
DEFAULT_WEIGHTS = MatchWeights(
    skill=Decimal("0.500"),
    semantic=Decimal("0.300"),
    experience=Decimal("0.200"),
)


def requirement(
    skill_id: int,
    importance: SkillImportance = SkillImportance.MANDATORY,
    years: str = "0",
) -> JobSkillRequirement:
    return JobSkillRequirement(
        skill_id=skill_id,
        name=f"Skill {skill_id}",
        importance=importance,
        min_years_required=Decimal(years),
    )


def candidate(skill_id: int, years: str | None = None) -> CandidateSkill:
    return CandidateSkill(
        skill_id=skill_id,
        name=f"Skill {skill_id}",
        years_of_experience=Decimal(years) if years is not None else None,
    )


def embedding_input(
    resume: Any = UNIT_X,
    job: Any = UNIT_X,
    *,
    resume_model: str = "BAAI/bge-m3",
    job_model: str = "BAAI/bge-m3",
    resume_preprocessing: str = "semantic-v1",
    job_preprocessing: str = "semantic-v1",
) -> EmbeddingInput:
    return EmbeddingInput(
        resume_vector=resume,
        job_vector=job,
        resume_model=resume_model,
        job_model=job_model,
        resume_preprocessing_version=resume_preprocessing,
        job_preprocessing_version=job_preprocessing,
    )


def calculate(
    *,
    candidate_skills: tuple[CandidateSkill, ...] = (candidate(1),),
    job_skills: tuple[JobSkillRequirement, ...] = (requirement(1),),
    experiences: tuple[CandidateExperience, ...] = (),
    required_experience: Decimal = Decimal(0),
    weights: MatchWeights = DEFAULT_WEIGHTS,
    embeddings: EmbeddingInput | None = None,
    as_of_date: date = date(2025, 1, 1),
) -> MatchComputationResult:
    return compute_match(
        candidate_skills=candidate_skills,
        job_skills=job_skills,
        candidate_experiences=experiences,
        min_experience_years=required_experience,
        weights=weights,
        embeddings=embeddings or embedding_input(),
        as_of_date=as_of_date,
    )


def component_weights(component: str) -> MatchWeights:
    return MatchWeights(
        skill=Decimal(1 if component == "skill" else 0),
        semantic=Decimal(1 if component == "semantic" else 0),
        experience=Decimal(1 if component == "experience" else 0),
    )


def test_all_skills_matched_scores_100() -> None:
    result = calculate(
        candidate_skills=(candidate(1), candidate(2)),
        job_skills=(requirement(1), requirement(2, SkillImportance.OPTIONAL)),
        weights=component_weights("skill"),
    )
    assert result.skill_score == Decimal("100.00")
    assert result.overall_score == Decimal("100.00")


def test_no_skills_matched_scores_zero() -> None:
    result = calculate(
        candidate_skills=(),
        job_skills=(requirement(1), requirement(2, SkillImportance.OPTIONAL)),
        weights=component_weights("skill"),
    )
    assert result.skill_score == Decimal("0.00")


def test_mandatory_weight_two_optional_weight_one_and_mixed_score() -> None:
    result = calculate(
        candidate_skills=(candidate(1),),
        job_skills=(requirement(1), requirement(2, SkillImportance.OPTIONAL)),
        weights=component_weights("skill"),
    )
    assert result.skill_score == Decimal("66.67")


def test_partial_years_still_count_as_present() -> None:
    result = calculate(
        candidate_skills=(candidate(1, "1.5"),),
        job_skills=(requirement(1, years="3.0"),),
        weights=component_weights("skill"),
    )
    assert result.skill_score == Decimal("100.00")
    assert result.matched_skills[0].status is SkillEvidenceStatus.PARTIAL
    assert result.matched_skills[0].candidate_years == Decimal("1.5")
    assert result.matched_skills[0].severity is None


def test_missing_candidate_years_against_positive_requirement_is_partial() -> None:
    result = calculate(
        candidate_skills=(candidate(1),),
        job_skills=(requirement(1, years="2.0"),),
    )
    assert result.matched_skills[0].status is SkillEvidenceStatus.PARTIAL


def test_missing_skill_severity_depends_on_importance() -> None:
    result = calculate(
        candidate_skills=(),
        job_skills=(requirement(2, SkillImportance.OPTIONAL), requirement(1)),
    )
    assert [item.skill_id for item in result.missing_skills] == [1, 2]
    assert [item.severity for item in result.missing_skills] == [
        SkillGapSeverity.CRITICAL,
        SkillGapSeverity.MINOR,
    ]


@pytest.mark.parametrize(
    ("candidate_skills", "job_skills", "message"),
    [
        ((candidate(1), candidate(1)), (requirement(1),), "Candidate skill IDs"),
        ((candidate(1),), (requirement(1), requirement(1)), "Job skill IDs"),
        ((candidate(1),), (), "At least one Job skill"),
    ],
)
def test_invalid_skill_collections_are_controlled(
    candidate_skills: tuple[CandidateSkill, ...],
    job_skills: tuple[JobSkillRequirement, ...],
    message: str,
) -> None:
    with pytest.raises(MatchComputationError, match=message) as raised:
        calculate(candidate_skills=candidate_skills, job_skills=job_skills)
    assert raised.value.kind is MatchComputationErrorKind.INVALID_SKILLS


@pytest.mark.parametrize(
    ("candidate_skills", "job_skills"),
    [
        ((CandidateSkill(1, "Python", Decimal("NaN")),), (requirement(1),)),
        ((candidate(1),), (requirement(1, years="Infinity"),)),
        ((candidate(1, "-1"),), (requirement(1),)),
    ],
)
def test_invalid_skill_years_are_controlled(
    candidate_skills: tuple[CandidateSkill, ...],
    job_skills: tuple[JobSkillRequirement, ...],
) -> None:
    with pytest.raises(MatchComputationError) as raised:
        calculate(candidate_skills=candidate_skills, job_skills=job_skills)
    assert raised.value.kind is MatchComputationErrorKind.INVALID_SKILLS


def test_noncanonical_importance_is_rejected() -> None:
    invalid = JobSkillRequirement(1, "Python", "REQUIRED", Decimal(0))  # type: ignore[arg-type]
    with pytest.raises(MatchComputationError) as raised:
        calculate(job_skills=(invalid,))
    assert raised.value.kind is MatchComputationErrorKind.INVALID_SKILLS


def test_evidence_is_stably_ordered_by_skill_id() -> None:
    result = calculate(
        candidate_skills=(candidate(3), candidate(1)),
        job_skills=(requirement(3), requirement(2), requirement(1)),
    )
    assert [item.skill_id for item in result.matched_skills] == [1, 3]
    assert [item.skill_id for item in result.missing_skills] == [2]


@pytest.mark.parametrize(
    ("resume", "job", "expected"),
    [
        (UNIT_X, UNIT_X, Decimal("100.00")),
        (UNIT_X, UNIT_Y, Decimal("0.00")),
        (UNIT_X, [-1.0, *ZERO[1:]], Decimal("0.00")),
    ],
    ids=["identical", "orthogonal", "negative"],
)
def test_semantic_score_canonical_cases(
    resume: list[float], job: list[float], expected: Decimal
) -> None:
    result = calculate(
        weights=component_weights("semantic"),
        embeddings=embedding_input(resume, job),
    )
    assert result.semantic_score == expected


def test_semantic_score_known_cosine() -> None:
    diagonal = [1.0, 1.0, *ZERO[2:]]
    result = calculate(
        weights=component_weights("semantic"),
        embeddings=embedding_input(diagonal, UNIT_X),
    )
    assert result.semantic_score == Decimal("70.71")


@pytest.mark.parametrize(
    "embeddings",
    [
        embedding_input(None, UNIT_X),
        embedding_input(UNIT_X, None),
        embedding_input(UNIT_X[:-1], UNIT_X),
        embedding_input(UNIT_X, UNIT_X[:-1]),
        embedding_input([math.nan, *ZERO[1:]], UNIT_X),
        embedding_input([math.inf, *ZERO[1:]], UNIT_X),
        embedding_input(ZERO, UNIT_X),
        embedding_input(UNIT_X, ZERO),
    ],
    ids=[
        "missing-resume",
        "missing-job",
        "resume-dimension",
        "job-dimension",
        "nan",
        "infinite",
        "resume-zero",
        "job-zero",
    ],
)
def test_invalid_embedding_vectors_are_controlled(embeddings: EmbeddingInput) -> None:
    with pytest.raises(MatchComputationError):
        calculate(embeddings=embeddings)


def test_nonnumeric_embedding_is_rejected() -> None:
    invalid = ["secret-value", *ZERO[1:]]
    with pytest.raises(MatchComputationError) as raised:
        calculate(embeddings=embedding_input(invalid, UNIT_X))
    assert raised.value.kind is MatchComputationErrorKind.INVALID_INPUT
    assert "secret-value" not in str(raised.value)


def test_large_finite_embedding_values_do_not_overflow() -> None:
    large = [1e308, *ZERO[1:]]
    result = calculate(embeddings=embedding_input(large, large))
    assert result.semantic_score == Decimal("100.00")


@pytest.mark.parametrize(
    "embeddings",
    [
        embedding_input(resume_model="model-a", job_model="model-b"),
        embedding_input(resume_preprocessing="v1", job_preprocessing="v2"),
        embedding_input(resume_model=" "),
        embedding_input(job_preprocessing=""),
    ],
    ids=["model-mismatch", "preprocessing-mismatch", "blank-model", "blank-preprocessing"],
)
def test_embedding_provenance_must_be_compatible(embeddings: EmbeddingInput) -> None:
    with pytest.raises(MatchComputationError) as raised:
        calculate(embeddings=embeddings)
    assert raised.value.kind is MatchComputationErrorKind.INCOMPATIBLE_EMBEDDINGS


def experience(
    start: date | None, end: date | None, *, current: bool = False
) -> CandidateExperience:
    return CandidateExperience(start_date=start, end_date=end, is_current=current)


def experience_result(
    experiences: tuple[CandidateExperience, ...],
    required: str,
    *,
    as_of_date: date = date(2025, 1, 1),
) -> Decimal:
    return calculate(
        experiences=experiences,
        required_experience=Decimal(required),
        weights=component_weights("experience"),
        embeddings=embedding_input(),
        as_of_date=as_of_date,
    ).experience_score


def test_nonpositive_required_experience_scores_100() -> None:
    assert experience_result((), "0") == Decimal("100.00")


def test_no_quantifiable_experience_scores_zero() -> None:
    assert experience_result((), "1") == Decimal("0.00")


def test_365_25_day_conversion_below_exact_and_above_requirement() -> None:
    below = (experience(date(2021, 1, 1), date(2022, 1, 1)),)
    exact = (experience(date(2020, 1, 1), date(2024, 1, 1)),)
    assert experience_result(below, "1") == Decimal("99.93")
    assert experience_result(exact, "4") == Decimal("100.00")
    assert experience_result(exact, "3") == Decimal("100.00")


def test_overlapping_intervals_merge_without_double_counting() -> None:
    intervals = (
        experience(date(2024, 1, 1), date(2024, 1, 11)),
        experience(date(2024, 1, 5), date(2024, 1, 20)),
    )
    assert experience_result(intervals, "1") == Decimal("5.20")


def test_fully_nested_intervals_merge() -> None:
    intervals = (
        experience(date(2024, 1, 1), date(2024, 1, 21)),
        experience(date(2024, 1, 5), date(2024, 1, 10)),
    )
    assert experience_result(intervals, "1") == Decimal("5.48")


def test_disjoint_intervals_add() -> None:
    intervals = (
        experience(date(2024, 1, 1), date(2024, 1, 11)),
        experience(date(2024, 2, 1), date(2024, 2, 11)),
    )
    assert experience_result(intervals, "1") == Decimal("5.48")


def test_duplicate_intervals_do_not_double_count() -> None:
    interval = experience(date(2024, 1, 1), date(2024, 1, 11))
    assert experience_result((interval, interval), "1") == Decimal("2.74")


def test_current_role_uses_injected_as_of_date() -> None:
    current = (experience(date(2024, 1, 1), None, current=True),)
    assert experience_result(current, "2", as_of_date=date(2025, 1, 1)) == Decimal("50.10")


def test_unquantifiable_and_invalid_intervals_are_excluded() -> None:
    invalid = (
        experience(None, date(2024, 1, 1)),
        experience(date(2024, 1, 1), None),
        experience(date(2024, 2, 1), date(2024, 1, 1)),
    )
    assert experience_result(invalid, "1") == Decimal("0.00")


def test_experience_is_deterministic_for_same_explicit_date() -> None:
    current = (experience(date(2023, 6, 1), None, current=True),)
    first = experience_result(current, "3", as_of_date=date(2025, 1, 1))
    second = experience_result(current, "3", as_of_date=date(2025, 1, 1))
    assert second == first


@pytest.mark.parametrize("required", [Decimal("-1"), Decimal("NaN"), Decimal("Infinity")])
def test_invalid_minimum_experience_is_controlled(required: Decimal) -> None:
    with pytest.raises(MatchComputationError) as raised:
        calculate(required_experience=required)
    assert raised.value.kind is MatchComputationErrorKind.INVALID_INPUT


def test_canonical_weight_combination_uses_all_components() -> None:
    result = calculate(
        candidate_skills=(candidate(1),),
        job_skills=(requirement(1), requirement(2, SkillImportance.OPTIONAL)),
        experiences=(experience(date(2020, 1, 1), date(2024, 1, 1)),),
        required_experience=Decimal("8"),
        weights=DEFAULT_WEIGHTS,
    )
    assert result.skill_score == Decimal("66.67")
    assert result.semantic_score == Decimal("100.00")
    assert result.experience_score == Decimal("50.00")
    assert result.overall_score == Decimal("73.33")


def test_custom_valid_weights_are_used() -> None:
    result = calculate(
        candidate_skills=(),
        weights=MatchWeights(Decimal("0.2"), Decimal("0.7"), Decimal("0.1")),
    )
    assert result.overall_score == Decimal("80.00")


@pytest.mark.parametrize(
    "weights",
    [
        MatchWeights(Decimal("-0.1"), Decimal("0.9"), Decimal("0.2")),
        MatchWeights(Decimal("1.1"), Decimal("-0.1"), Decimal("0")),
        MatchWeights(Decimal("0.5"), Decimal("0.3"), Decimal("0.1")),
        MatchWeights(Decimal("NaN"), Decimal("0.5"), Decimal("0.5")),
        MatchWeights(0.5, Decimal("0.3"), Decimal("0.2")),  # type: ignore[arg-type]
    ],
    ids=["negative", "above-one", "wrong-total", "non-finite", "non-decimal"],
)
def test_invalid_weights_are_controlled(weights: MatchWeights) -> None:
    with pytest.raises(MatchComputationError) as raised:
        calculate(weights=weights)
    assert raised.value.kind is MatchComputationErrorKind.INVALID_WEIGHTS


def test_scores_round_half_up_to_two_places() -> None:
    result = calculate(
        candidate_skills=(candidate(1),),
        job_skills=(requirement(1), requirement(2, SkillImportance.OPTIONAL)),
        weights=MatchWeights(Decimal("0.185175"), Decimal("0.814825"), Decimal(0)),
        embeddings=embedding_input(UNIT_X, UNIT_Y),
    )
    assert result.overall_score == Decimal("12.35")


def test_overall_uses_unrounded_components() -> None:
    result = calculate(
        candidate_skills=(candidate(1),),
        job_skills=(requirement(1), requirement(2, SkillImportance.OPTIONAL)),
        weights=MatchWeights(Decimal("0.5"), Decimal("0.5"), Decimal(0)),
        embeddings=embedding_input(UNIT_X, UNIT_Y),
    )
    assert result.skill_score == Decimal("66.67")
    assert result.overall_score == Decimal("33.33")


def test_complete_result_is_deterministic_and_bounded() -> None:
    kwargs = {
        "candidate_skills": (candidate(3, "2"), candidate(1, "4")),
        "job_skills": (
            requirement(3, SkillImportance.OPTIONAL, "3"),
            requirement(2),
            requirement(1, years="4"),
        ),
        "experiences": (experience(date(2020, 1, 1), None, current=True),),
        "required_experience": Decimal("6"),
        "as_of_date": date(2025, 1, 1),
    }
    first = calculate(**kwargs)  # type: ignore[arg-type]
    second = calculate(**kwargs)  # type: ignore[arg-type]
    assert second == first
    assert first.algorithm_version == ALGORITHM_VERSION == "hybrid-v1"
    assert all(
        Decimal(0) <= score <= Decimal(100)
        for score in (
            first.overall_score,
            first.skill_score,
            first.semantic_score,
            first.experience_score,
        )
    )
