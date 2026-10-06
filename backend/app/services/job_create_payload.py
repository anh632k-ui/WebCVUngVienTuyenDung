from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from app.schemas.job_schema import JobCreateRequest

JOB_CREATE_CANONICAL_FIELDS = (
    "title",
    "job_level",
    "location",
    "raw_content",
    "w_skill",
    "w_semantic",
    "w_experience",
)


def _json_text(value: str | None) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _fixed_weight(value: Decimal) -> str:
    canonical_value = Decimal("0") if value == 0 else value
    return format(canonical_value, ".3f")


def canonical_job_create_json(payload: JobCreateRequest) -> str:
    """Serialize canonical payload v1 with explicit order and numeric weight tokens."""

    values = (
        _json_text(payload.title),
        _json_text(payload.job_level),
        _json_text(payload.location),
        _json_text(payload.raw_content),
        _fixed_weight(payload.w_skill),
        _fixed_weight(payload.w_semantic),
        _fixed_weight(payload.w_experience),
    )
    fields = (
        f"{json.dumps(key)}:{value}"
        for key, value in zip(JOB_CREATE_CANONICAL_FIELDS, values, strict=True)
    )
    return "{" + ",".join(fields) + "}"


def job_create_fingerprint(payload: JobCreateRequest) -> str:
    canonical_bytes = canonical_job_create_json(payload).encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest()
