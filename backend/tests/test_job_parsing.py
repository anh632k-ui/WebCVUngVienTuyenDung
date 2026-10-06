from __future__ import annotations

from decimal import Decimal

import pytest

from app.ai.job_parser import (
    MAX_JOB_TEXT_CHARS,
    JobParseError,
    JobParseErrorKind,
    parse_job_description,
)
from app.ai.job_schemas import ParsedJobResult
from app.ai.job_sections import JobSection, split_job_heading
from app.ai.schemas import TaxonomySkill
from app.ai.text_processing import normalize_parser_text


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
        (41, "NLP", "nlp"),
        (50, "FastAPI", "fastapi"),
        (51, "Docker", "docker"),
        (52, "AWS", "aws"),
        (53, "PostgreSQL", "postgresql"),
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


def test_normalization_uses_shared_semantic_family_and_preserves_vietnamese() -> None:
    source = (
        "  Ky\u0303 su\u031b\tpha\u0302\u0300n  me\u0302\u0300m\r\n"
        "\x00Ha\u0300 No\u0323\u0302i\r\n\r\n\r\nPython  "
    )
    expected = "Kỹ sư phần mềm\nHà Nội\n\nPython"
    result = parse_job_description(source, ())

    assert result.normalized_text == expected
    assert result.normalized_text == normalize_parser_text(source)
    assert parse_job_description(source, ()) == result


@pytest.mark.parametrize("source", ["", " \t\r\n ", "--- !!! •••"])
def test_empty_or_punctuation_only_input_is_controlled(source: str) -> None:
    with pytest.raises(JobParseError) as raised:
        parse_job_description(source, ())
    assert raised.value.kind is JobParseErrorKind.NO_MEANINGFUL_TEXT


def test_over_limit_and_invalid_unicode_are_controlled() -> None:
    with pytest.raises(JobParseError) as too_large:
        parse_job_description("x" * (MAX_JOB_TEXT_CHARS + 1), ())
    assert too_large.value.kind is JobParseErrorKind.TEXT_LIMIT_EXCEEDED

    with pytest.raises(JobParseError) as invalid:
        parse_job_description("Job\ud800 content", ())
    assert invalid.value.kind is JobParseErrorKind.INVALID_INPUT


@pytest.mark.parametrize(
    ("heading", "section", "remainder"),
    [
        ("Job Description", JobSection.DESCRIPTION, ""),
        ("Mô tả công việc:", JobSection.DESCRIPTION, ""),
        ("Requirements: Python", JobSection.MANDATORY, "Python"),
        ("Minimum Qualifications", JobSection.MANDATORY, ""),
        ("Kỹ năng bắt buộc: SQL", JobSection.MANDATORY, "SQL"),
        ("Preferred Qualifications", JobSection.OPTIONAL, ""),
        ("Không bắt buộc: Docker", JobSection.OPTIONAL, "Docker"),
        ("Trình độ học vấn", JobSection.EDUCATION, ""),
        ("Kinh nghiệm: 3 năm", JobSection.EXPERIENCE, "3 năm"),
    ],
)
def test_conservative_bilingual_heading_detection(
    heading: str,
    section: JobSection,
    remainder: str,
) -> None:
    assert split_job_heading(heading) == (section, remainder)


def test_arbitrary_sentences_are_not_headings() -> None:
    assert split_job_heading("Our requirements evolve every quarter") is None
    assert split_job_heading("Experience improves our product") is None


def test_required_optional_and_neutral_skill_context(
    taxonomy: tuple[TaxonomySkill, ...],
) -> None:
    result = parse_job_description(
        "We build products using Python.\n"
        "Requirements\nJavaScript\nPython required\n"
        "Preferred Qualifications\nDocker\nJava preferred",
        taxonomy,
    )

    assert skill_map(result) == {
        "Python": ("MANDATORY", Decimal("0.0")),
        "Java": ("OPTIONAL", Decimal("0.0")),
        "JavaScript": ("MANDATORY", Decimal("0.0")),
        "Docker": ("OPTIONAL", Decimal("0.0")),
    }


def test_neutral_descriptive_skill_mention_is_omitted(
    taxonomy: tuple[TaxonomySkill, ...],
) -> None:
    result = parse_job_description("We build products using Python and PostgreSQL.", taxonomy)
    assert result.skills == ()


def test_explicit_optional_overrides_requirements_section(
    taxonomy: tuple[TaxonomySkill, ...],
) -> None:
    result = parse_job_description("Requirements\nPython preferred", taxonomy)
    assert skill_map(result)["Python"][0] == "OPTIONAL"


def test_each_skill_uses_its_nearest_explicit_importance_cue(
    taxonomy: tuple[TaxonomySkill, ...],
) -> None:
    result = parse_job_description("Python required and Java preferred", taxonomy)
    assert skill_map(result) == {
        "Java": ("OPTIONAL", Decimal("0.0")),
        "Python": ("MANDATORY", Decimal("0.0")),
    }


def test_mandatory_evidence_wins_across_repeated_mentions(
    taxonomy: tuple[TaxonomySkill, ...],
) -> None:
    result = parse_job_description(
        "Preferred Skills: Python\nRequired Skills: Python\nPython required",
        taxonomy,
    )
    assert len(result.skills) == 1
    assert skill_map(result)["Python"][0] == "MANDATORY"


def test_boundary_matching_handles_collisions_punctuation_and_stable_order(
    taxonomy: tuple[TaxonomySkill, ...],
) -> None:
    before = tuple(taxonomy)
    result = parse_job_description(
        "Required Skills: JavaScript, C#, C++, Next.js, Node.js, ASP.NET Core, CI/CD, NLP, "
        "UnknownQuantumTool",
        taxonomy,
    )

    assert [skill.name for skill in result.skills] == [
        "JavaScript",
        "C#",
        "C++",
        "Next.js",
        "Node.js",
        "ASP.NET Core",
        "CI/CD",
        "NLP",
    ]
    assert "Java" not in {skill.name for skill in result.skills}
    assert taxonomy == before

    java_only = parse_job_description("Required Skills: Java", taxonomy)
    assert [skill.name for skill in java_only.skills] == ["Java"]


@pytest.mark.parametrize(
    ("text", "skill", "importance"),
    [
        ("Python required", "Python", "MANDATORY"),
        ("Python preferred", "Python", "OPTIONAL"),
        ("Bắt buộc: SQL", "SQL", "MANDATORY"),
        ("Yêu cầu FastAPI", "FastAPI", "MANDATORY"),
        ("Ưu tiên PostgreSQL", "PostgreSQL", "OPTIONAL"),
        ("CI/CD là lợi thế", "CI/CD", "OPTIONAL"),
        ("Docker không bắt buộc", "Docker", "OPTIONAL"),
    ],
)
def test_explicit_bilingual_importance(
    taxonomy: tuple[TaxonomySkill, ...],
    text: str,
    skill: str,
    importance: str,
) -> None:
    assert skill_map(parse_job_description(text, taxonomy))[skill][0] == importance


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("3+ years of experience", Decimal("3.0")),
        ("at least 2 years experience", Decimal("2.0")),
        ("tối thiểu 4 năm kinh nghiệm", Decimal("4.0")),
        ("yêu cầu 3 năm kinh nghiệm", Decimal("3.0")),
        ("Senior Backend Engineer", Decimal("0.0")),
        ("Salary 3000 USD, founded in 2018", Decimal("0.0")),
        ("Our company has 20+ years of experience", Decimal("0.0")),
    ],
)
def test_global_experience_requires_candidate_evidence(
    taxonomy: tuple[TaxonomySkill, ...],
    text: str,
    expected: Decimal,
) -> None:
    assert parse_job_description(text, taxonomy).min_experience_years == expected


def test_strongest_explicit_global_minimum_is_used(
    taxonomy: tuple[TaxonomySkill, ...],
) -> None:
    result = parse_job_description(
        "At least 2 years experience. Minimum 5 years of professional experience.",
        taxonomy,
    )
    assert result.min_experience_years == Decimal("5.0")


@pytest.mark.parametrize(
    ("text", "skill", "expected"),
    [
        ("3+ years of Python", "Python", Decimal("3.0")),
        ("at least 2 years with Java", "Java", Decimal("2.0")),
        ("tối thiểu 2 năm kinh nghiệm Java", "Java", Decimal("2.0")),
    ],
)
def test_skill_specific_years_map_only_to_evidenced_skill(
    taxonomy: tuple[TaxonomySkill, ...],
    text: str,
    skill: str,
    expected: Decimal,
) -> None:
    result = parse_job_description(text, taxonomy)
    assert result.min_experience_years == Decimal("0.0")
    assert skill_map(result)[skill] == ("MANDATORY", expected)


def test_generic_experience_is_not_attached_to_all_skills(
    taxonomy: tuple[TaxonomySkill, ...],
) -> None:
    result = parse_job_description(
        "Requirements\nMinimum 5 years of professional experience.\nPython\nJava",
        taxonomy,
    )
    assert result.min_experience_years == Decimal("5.0")
    assert all(skill.min_years_required == Decimal("0.0") for skill in result.skills)


@pytest.mark.parametrize("text", ["Python required: -3 years", "Python required: 100 years"])
def test_invalid_skill_years_are_not_fabricated(
    taxonomy: tuple[TaxonomySkill, ...],
    text: str,
) -> None:
    result = parse_job_description(text, taxonomy)
    assert skill_map(result)["Python"][1] == Decimal("0.0")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Bachelor's degree in Computer Science", "Bachelor's degree in Computer Science"),
        ("Bachelor degree or equivalent", "Bachelor degree or equivalent"),
        ("Master's degree preferred", "Master's degree preferred"),
        (
            "Tốt nghiệp Đại học ngành Công nghệ thông tin",
            "Tốt nghiệp Đại học ngành Công nghệ thông tin",
        ),
        ("Bằng cử nhân CNTT", "Bằng cử nhân CNTT"),
        ("Cao đẳng trở lên", "Cao đẳng trở lên"),
    ],
)
def test_explicit_education_evidence_is_preserved(
    text: str,
    expected: str,
) -> None:
    assert parse_job_description(text, ()).education_requirement == expected


def test_education_is_not_fabricated_from_title_or_university_project() -> None:
    assert parse_job_description("Senior Engineer", ()).education_requirement is None
    assert (
        parse_job_description("Built a university admissions project", ()).education_requirement
        is None
    )


def test_overlong_education_evidence_uses_exact_phrase_without_truncation() -> None:
    text = "Bachelor's degree " + "with extensive unrelated paragraph text " * 10
    result = parse_job_description(text, ())
    assert result.education_requirement == "Bachelor's degree"


def test_realistic_english_job_description_end_to_end(
    taxonomy: tuple[TaxonomySkill, ...],
) -> None:
    text = (
        "Backend Engineer\r\n"
        "Job Description\r\n"
        "We build internal products using NLP.\r\n"
        "Requirements\r\n"
        "- 3+ years of professional experience\r\n"
        "- Python required\r\n"
        "- PostgreSQL required\r\n"
        "Preferred Qualifications\r\n"
        "- Docker\r\n"
        "- AWS\r\n"
        "Education\r\n"
        "- Bachelor's degree in Computer Science"
    )
    first = parse_job_description(text, taxonomy)
    second = parse_job_description(text, taxonomy)

    assert first == second
    assert first.normalized_text.startswith("Backend Engineer\nJob Description")
    assert first.min_experience_years == Decimal("3.0")
    assert first.education_requirement == "Bachelor's degree in Computer Science"
    assert skill_map(first) == {
        "Python": ("MANDATORY", Decimal("0.0")),
        "Docker": ("OPTIONAL", Decimal("0.0")),
        "AWS": ("OPTIONAL", Decimal("0.0")),
        "PostgreSQL": ("MANDATORY", Decimal("0.0")),
    }


def test_realistic_vietnamese_job_description_end_to_end(
    taxonomy: tuple[TaxonomySkill, ...],
) -> None:
    text = (
        "Kỹ sư Backend\n"
        "Mô tả công việc\n"
        "Xây dựng sản phẩm sử dụng JavaScript.\n"
        "Yêu cầu công việc\n"
        "- Tối thiểu 4 năm kinh nghiệm\n"
        "- Bắt buộc Python\n"
        "- Yêu cầu FastAPI\n"
        "Ưu tiên\n"
        "- PostgreSQL\n"
        "- CI/CD là lợi thế\n"
        "Trình độ học vấn\n"
        "- Tốt nghiệp Đại học ngành Công nghệ thông tin"
    )
    result = parse_job_description(text, taxonomy)

    assert "Kỹ sư Backend" in result.normalized_text
    assert result.min_experience_years == Decimal("4.0")
    assert result.education_requirement == "Tốt nghiệp Đại học ngành Công nghệ thông tin"
    assert skill_map(result) == {
        "Python": ("MANDATORY", Decimal("0.0")),
        "CI/CD": ("OPTIONAL", Decimal("0.0")),
        "FastAPI": ("MANDATORY", Decimal("0.0")),
        "PostgreSQL": ("OPTIONAL", Decimal("0.0")),
    }
