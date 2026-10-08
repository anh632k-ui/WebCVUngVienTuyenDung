from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.job_schema import JobUpdateRequest
from main import app


def test_job_update_normalizes_only_supplied_fields() -> None:
    payload = JobUpdateRequest.model_validate(
        {
            "title": "  Senior e\u0301ngineer  ",
            "job_level": "  ",
            "location": "   ",
            "raw_content": "Line 1\r\nLine 2\rLine 3",
        }
    )
    assert payload.title == "Senior éngineer"
    assert payload.job_level == ""
    assert payload.location is None
    assert payload.raw_content == "Line 1\nLine 2\nLine 3"
    assert payload.model_fields_set == {"title", "job_level", "location", "raw_content"}

    title_only = JobUpdateRequest.model_validate({"title": "Updated"})
    assert title_only.model_fields_set == {"title"}
    assert title_only.title == "Updated"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"title": None},
        {"job_level": None},
        {"raw_content": None},
        {"raw_content": ""},
        {"title": "x" * 201},
        {"job_level": "x" * 51},
        {"location": "x" * 151},
        {"title": "bad\x00title"},
        {"raw_content": "bad\ud800text"},
        {"revision": 4},
        {"status": "ACTIVE"},
        {"w_skill": 0.5},
    ],
)
def test_job_update_rejects_invalid_or_protected_payload(body: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        JobUpdateRequest.model_validate(body)


def test_job_update_accepts_physical_boundaries_and_nullable_location() -> None:
    payload = JobUpdateRequest.model_validate(
        {"title": "t" * 200, "job_level": "l" * 50, "location": None, "raw_content": "x"}
    )
    assert len(payload.title) == 200
    assert len(payload.job_level) == 50
    assert payload.location is None


def test_runtime_openapi_describes_canonical_partial_job_update() -> None:
    specification = app.openapi()
    operation = specification["paths"]["/api/v1/jobs/{id}"]["put"]
    schema_ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    assert schema_ref.endswith("/JobUpdateRequest")
    schema = specification["components"]["schemas"]["JobUpdateRequest"]
    assert schema["minProperties"] == 1
    assert schema["additionalProperties"] is False
    assert schema.get("required", []) == []
    assert schema["properties"]["title"]["type"] == "string"
    assert schema["properties"]["job_level"]["type"] == "string"
    assert schema["properties"]["raw_content"]["type"] == "string"
    assert schema["properties"]["raw_content"]["minLength"] == 1
    assert {item["type"] for item in schema["properties"]["location"]["anyOf"]} == {
        "string",
        "null",
    }
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/JobResponse"
    )
