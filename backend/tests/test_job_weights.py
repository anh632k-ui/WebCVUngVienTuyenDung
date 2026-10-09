from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.job_schema import JobWeightsRequest
from main import app


def test_weights_request_accepts_canonical_values_and_strict_flag() -> None:
    request = JobWeightsRequest.model_validate(
        {
            "w_skill": 0.333,
            "w_semantic": 0.333,
            "w_experience": 0.334,
            "recalculate": True,
        }
    )

    assert (request.w_skill, request.w_semantic, request.w_experience) == (
        Decimal("0.333"),
        Decimal("0.333"),
        Decimal("0.334"),
    )
    assert request.recalculate is True
    assert (
        JobWeightsRequest.model_validate(
            {"w_skill": 1, "w_semantic": 0, "w_experience": 0}
        ).recalculate
        is False
    )
    assert JobWeightsRequest.model_validate(
        {"w_skill": 0.5000, "w_semantic": 0.3000, "w_experience": 0.2000}
    ).w_skill == Decimal("0.5")


@pytest.mark.parametrize(
    "payload",
    [
        {"w_skill": 0.5, "w_semantic": 0.5},
        {"w_skill": -0.0001, "w_semantic": 0.8, "w_experience": 0.2001},
        {"w_skill": 1.1, "w_semantic": 0, "w_experience": 0},
        {"w_skill": 0.5, "w_semantic": 0.4, "w_experience": 0.2},
        {"w_skill": 0.1234, "w_semantic": 0.8766, "w_experience": 0},
        {"w_skill": "0.5", "w_semantic": 0.3, "w_experience": 0.2},
        {"w_skill": True, "w_semantic": 0, "w_experience": 0},
        {"w_skill": float("inf"), "w_semantic": 0, "w_experience": 0},
        {"w_skill": 0.5, "w_semantic": 0.3, "w_experience": 0.2, "recalculate": "false"},
        {"w_skill": 0.5, "w_semantic": 0.3, "w_experience": 0.2, "revision": 99},
    ],
)
def test_weights_request_rejects_noncanonical_payloads(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        JobWeightsRequest.model_validate(payload)


def test_runtime_openapi_exposes_canonical_weights_contract() -> None:
    specification = app.openapi()
    operation = specification["paths"]["/api/v1/jobs/{id}/weights"]["put"]
    schema_ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    assert schema_ref.endswith("/JobWeightsRequest")
    schema = specification["components"]["schemas"]["JobWeightsRequest"]
    assert schema["required"] == ["w_skill", "w_semantic", "w_experience"]
    assert schema["additionalProperties"] is False
    for field in ("w_skill", "w_semantic", "w_experience"):
        assert schema["properties"][field]["type"] == "number"
        assert schema["properties"][field]["minimum"] == 0
        assert schema["properties"][field]["maximum"] == 1
    assert schema["properties"]["recalculate"] == {
        "type": "boolean",
        "default": False,
        "title": "Recalculate",
    }
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/JobResponse"
    )
    assert "503" not in operation["responses"]
    assert "503" in specification["paths"]["/api/v1/matching/calculate"]["post"]["responses"]
