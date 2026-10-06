from __future__ import annotations

from decimal import Decimal

import pytest

from app.ai.job_errors import JobParseError, JobParseErrorKind
from app.ai.job_parser import parse_job_description
from app.ai.job_schemas import ParsedJobResult
from app.ai.job_sections import JobSection, split_job_heading
from app.ai.job_text_processing import MAX_JOB_TEXT_CHARS, normalize_job_text
from app.ai.schemas import TaxonomySkill


@pytest.fixture
def taxonomy() -> tuple[TaxonomySkill, ...]:
    values = (
        (11, "JavaScript", "javascript"),
        (4, "Python", "python"),
        (3, "Java", "java"),
        (20, "C#", "csharp"),
        (21, "C++", "cpp"),
        (30, "Next.js", "nextjs"),
        (31, "Node.js", "nodejs"),
        (32, "ASP.NET Core", "aspnet-core"),
        (40, "CI/CD", "cicd"),
        (50, "FastAPI", "fastapi"),
        (51, "Docker", "docker"),
        (52, "Redis", "redis"),
        (53, "AWS", "aws"),
        (54, "PostgreSQL", "postgresql"),
        (60, "SQL", "sql"),
    )
    return tuple(
        TaxonomySkill(
            id=skill_id,
            name=name,
            normalized_name=normalized_name,
            skill_kind="HARD",
            category="Test",
        )
        for skill_id, name, normalized_name in values
    )


def skill_map(result: ParsedJobResult) -> dict[str, tuple[str, Decimal]]:
    return {skill.name: (skill.importance, skill.min_years_required) for skill in result.skills}


def test_text_normalization_preserves_vietnamese_and_is_deterministic() -> None:
    source = (
        "  Ky\u0303 su\u031b\tpha\u0302\u0300n  me\u0302\u0300m\r\n"
        "\x00Ha\u0300 No\u0323\u0302i\r\n\r\n\r\nPython  "
    )
    expected = "Kỹ sư phần mềm\nHà Nội\n\nPython"

    assert normalize_job_text(source) == expected
    assert normalize_job_text(source) == normalize_job_text(source)


@pytest.mark.parametrize("source", ["", " \t\r\n ", "--- !!! •••"])
def test_blank_or_punctuation_only_text_is_controlled(source: str) -> None:
    with pytest.raises(JobParseError) as raised:
        normalize_job_text(source)
    assert raised.value.kind is JobParseErrorKind.NO_MEANINGFUL_TEXT


def test_text_limit_and_invalid_unicode_are_controlled() -> None:
    with pytest.raises(JobParseError) as too_large:
        normalize_job_text("x" * (MAX_JOB_TEXT_CHARS + 1))
    assert too_large.value.kind is JobParseErrorKind.TEXT_LIMIT_EXCEEDED

    with pytest.raises(JobParseError) as invalid:
        normalize_job_text("Job\ud800 text")
    assert invalid.value.kind is JobParseErrorKind.INVALID_TEXT


@pytest.mark.parametrize(
    ("heading", "section", "remainder"),
    [
        ("Requirements", JobSection.GENERAL, ""),
        ("Yêu cầu ứng viên:", JobSection.GENERAL, ""),
        ("Required Skills: Python", JobSection.MANDATORY, "Python"),
        ("Bắt buộc: SQL", JobSection.MANDATORY, "SQL"),
        ("Nice to Have - Redis", JobSection.OPTIONAL, "Redis"),
        ("Trình độ học vấn", JobSection.EDUCATION, ""),
        ("Yêu cầu kinh nghiệm: 3 năm", JobSection.EXPERIENCE, "3 năm"),
    ],
)
def test_exact_english_and_vietnamese_headings(
    heading: str,
    section: JobSection,
    remainder: str,
) -> None:
    assert split_job_heading(heading) == (section, remainder)


def test_arbitrary_prose_is_not_a_heading() -> None:
    assert split_job_heading("Our requirements evolve with the team") is None
    assert split_job_heading("You are required to communicate clearly") is None


def test_skill_matching_handles_collisions_punctuation_dedup_and_order(
    taxonomy: tuple[TaxonomySkill, ...],
) -> None:
    before = tuple(taxonomy)
    result = parse_job_description(
        "JavaScript, C#, C++, Next.js, Node.js, ASP.NET Core, CI/CD, Python and Python. "
        "UnknownQuantumTool.",
        taxonomy,
    )

    assert [skill.name for skill in result.skills] == [
        "Python",
        "JavaScript",
        "C#",
        "C++",
        "Next.js",
        "Node.js",
        "ASP.NET Core",
        "CI/CD",
    ]
    assert "Java" not in {skill.name for skill in result.skills}
    assert taxonomy == before


@pytest.mark.parametrize(
    ("text", "skill", "importance"),
    [
        ("Required Skills: Python", "Python", "MANDATORY"),
        ("Must have Java", "Java", "MANDATORY"),
        ("Python is mandatory", "Python", "MANDATORY"),
        ("Bắt buộc: SQL", "SQL", "MANDATORY"),
        ("Phải có kinh nghiệm FastAPI", "FastAPI", "MANDATORY"),
        ("Preferred: Docker", "Docker", "OPTIONAL"),
        ("Nice to have Redis", "Redis", "OPTIONAL"),
        ("AWS is a plus", "AWS", "OPTIONAL"),
        ("Ưu tiên PostgreSQL", "PostgreSQL", "OPTIONAL"),
        ("CI/CD là lợi thế", "CI/CD", "OPTIONAL"),
    ],
)
def test_explicit_importance_evidence(
    taxonomy: tuple[TaxonomySkill, ...],
    text: str,
    skill: str,
    importance: str,
) -> None:
    assert skill_map(parse_job_description(text, taxonomy))[skill][0] == importance


def test_unrelated_required_evidence_does_not_contaminate_other_skills(
    taxonomy: tuple[TaxonomySkill, ...],
) -> None:
    result = parse_job_description(
        "Qualifications\nA Bachelor's degree is required.\nPython preferred.\nJavaScript",
        taxonomy,
    )

    assert skill_map(result)["Python"][0] == "OPTIONAL"
    assert skill_map(result)["JavaScript"][0] == "OPTIONAL"


def test_mandatory_evidence_wins_over_optional_for_same_skill(
    taxonomy: tuple[TaxonomySkill, ...],
) -> None:
    result = parse_job_description(
        "Preferred Skills\nPython\nRequired Skills\nPython",
        taxonomy,
    )
    assert skill_map(result)["Python"][0] == "MANDATORY"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("3+ years of experience", Decimal("3.0")),
        ("at least 2 years experience", Decimal("2.0")),
        ("2-4 years of experience", Decimal("2.0")),
        ("tối thiểu 3 năm kinh nghiệm", Decimal("3.0")),
        ("ít nhất 2 năm kinh nghiệm", Decimal("2.0")),
        ("kinh nghiệm từ 2 năm", Decimal("2.0")),
        ("Senior developer", Decimal("0.0")),
        ("Minimum -3 years of experience", Decimal("0.0")),
        ("Minimum 100 years of experience", Decimal("0.0")),
        ("Our CTO has 20 years of experience", Decimal("0.0")),
    ],
)
def test_overall_experience_is_explicit_only(
    taxonomy: tuple[TaxonomySkill, ...],
    text: str,
    expected: Decimal,
) -> None:
    assert parse_job_description(text, taxonomy).min_experience_years == expected


def test_highest_explicit_global_minimum_is_selected(
    taxonomy: tuple[TaxonomySkill, ...],
) -> None:
    result = parse_job_description(
        "At least 2 years experience.\nMinimum 4 years of professional experience.",
        taxonomy,
    )
    assert result.min_experience_years == Decimal("4.0")


def test_skill_years_are_explicit_and_separate_from_global_experience(
    taxonomy: tuple[TaxonomySkill, ...],
) -> None:
    result = parse_job_description(
        "Minimum 5 years of professional experience.\n"
        "Python: 3+ years\n"
        "at least 2 years with Java\n"
        "JavaScript",
        taxonomy,
    )
    skills = skill_map(result)

    assert result.min_experience_years == Decimal("5.0")
    assert skills["Python"][1] == Decimal("3.0")
    assert skills["Java"][1] == Decimal("2.0")
    assert skills["JavaScript"][1] == Decimal("0.0")


@pytest.mark.parametrize(
    ("text", "skill", "expected"),
    [
        ("Python - 3 years experience", "Python", Decimal("3.0")),
        ("3 years of Python", "Python", Decimal("3.0")),
        ("Python: 2.5 years", "Python", Decimal("2.5")),
        ("tối thiểu 2 năm Java", "Java", Decimal("2.0")),
    ],
)
def test_supported_skill_specific_year_phrasings(
    taxonomy: tuple[TaxonomySkill, ...],
    text: str,
    skill: str,
    expected: Decimal,
) -> None:
    assert skill_map(parse_job_description(text, taxonomy))[skill][1] == expected


def test_skill_specific_experience_does_not_become_global(
    taxonomy: tuple[TaxonomySkill, ...],
) -> None:
    result = parse_job_description("2-4 years of Python", taxonomy)
    assert result.min_experience_years == Decimal("0.0")
    assert skill_map(result)["Python"][1] == Decimal("2.0")


@pytest.mark.parametrize("text", ["Python: -3 years", "Python: 100 years"])
def test_nonsensical_skill_years_are_ignored(
    taxonomy: tuple[TaxonomySkill, ...],
    text: str,
) -> None:
    result = parse_job_description(text, taxonomy)
    assert skill_map(result)["Python"][1] == Decimal("0.0")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Bachelor's degree in Computer Science", "Bachelor's degree in Computer Science"),
        (
            "BS/BA in Computer Science or related field",
            "BS/BA in Computer Science or related field",
        ),
        ("University degree in IT", "University degree in IT"),
        (
            "Tốt nghiệp đại học ngành Công nghệ thông tin",
            "Tốt nghiệp đại học ngành Công nghệ thông tin",
        ),
        ("Cử nhân Khoa học máy tính", "Cử nhân Khoa học máy tính"),
        ("Trình độ: Đại học", "Trình độ: Đại học"),
    ],
)
def test_education_preserves_explicit_evidence(
    taxonomy: tuple[TaxonomySkill, ...],
    text: str,
    expected: str,
) -> None:
    assert parse_job_description(text, taxonomy).education_requirement == expected


def test_education_is_not_fabricated() -> None:
    taxonomy = (TaxonomySkill(1, "Python", "python", "HARD", "Language"),)
    assert parse_job_description("Python developer", taxonomy).education_requirement is None
    assert (
        parse_job_description("Worked on a school platform", taxonomy).education_requirement is None
    )
    assert (
        parse_job_description("Ba systems are currently deployed", taxonomy).education_requirement
        is None
    )


def test_realistic_english_job_description_end_to_end(
    taxonomy: tuple[TaxonomySkill, ...],
) -> None:
    text = (
        "Backend Engineer\r\n"
        "Requirements:\r\n"
        "Minimum 5 years of professional experience.\r\n"
        "Bachelor's degree in Computer Science or related field.\r\n"
        "Required Skills:\r\n"
        "Python: 3+ years\r\n"
        "Java\r\n"
        "Preferred Skills:\r\n"
        "Docker\r\n"
        "AWS is a plus"
    )
    first = parse_job_description(text, taxonomy)
    second = parse_job_description(text, taxonomy)

    assert first == second
    assert first.raw_text.startswith("Backend Engineer\nRequirements:")
    assert first.min_experience_years == Decimal("5.0")
    assert first.education_requirement == "Bachelor's degree in Computer Science or related field."
    assert skill_map(first) == {
        "Python": ("MANDATORY", Decimal("3.0")),
        "Java": ("MANDATORY", Decimal("0.0")),
        "Docker": ("OPTIONAL", Decimal("0.0")),
        "AWS": ("OPTIONAL", Decimal("0.0")),
    }


def test_realistic_vietnamese_job_description_end_to_end(
    taxonomy: tuple[TaxonomySkill, ...],
) -> None:
    text = (
        "Kỹ sư Backend\n"
        "Yêu cầu ứng viên:\n"
        "Tối thiểu 3 năm kinh nghiệm.\n"
        "Tốt nghiệp đại học ngành Công nghệ thông tin.\n"
        "Bắt buộc:\n"
        "Python - 2 năm kinh nghiệm\n"
        "Phải có kinh nghiệm FastAPI\n"
        "Ưu tiên:\n"
        "PostgreSQL\n"
        "CI/CD là lợi thế"
    )
    result = parse_job_description(text, taxonomy)

    assert "Kỹ sư Backend" in result.raw_text
    assert result.min_experience_years == Decimal("3.0")
    assert result.education_requirement == "Tốt nghiệp đại học ngành Công nghệ thông tin."
    assert skill_map(result) == {
        "Python": ("MANDATORY", Decimal("2.0")),
        "CI/CD": ("OPTIONAL", Decimal("0.0")),
        "FastAPI": ("MANDATORY", Decimal("0.0")),
        "PostgreSQL": ("OPTIONAL", Decimal("0.0")),
    }
