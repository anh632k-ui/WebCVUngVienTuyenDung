from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings, get_settings
from app.core.database import get_db_session
from app.core.exceptions import APIError
from app.core.idempotency import (
    APP_IDEMPOTENCY_NAMESPACE,
    JOB_CREATE_ROUTE,
    derive_idempotent_resource_id,
)
from app.core.security import create_access_token
from app.models.job import JobDescription
from app.models.user import User
from app.schemas.job_schema import JobCreateRequest
from app.services.job_create_payload import canonical_job_create_json, job_create_fingerprint
from app.services.job_dispatcher import get_job_parse_dispatcher
from app.services.job_service import create_job
from main import app

TEST_SECRET = "job-create-unit-test-jwt-secret-value"
DEFAULT_PAYLOAD = {
    "title": "Backend Engineer",
    "job_level": "Senior",
    "location": "Hanoi",
    "raw_content": "Build reliable Python APIs.",
}


def make_user(role: str = "HR", label: str = "default") -> User:
    now = datetime.now(UTC)
    return User(
        id=uuid.uuid5(uuid.NAMESPACE_URL, f"job-create-{label}"),
        email=f"job.create.{label}@example.com",
        password_hash="not-used",
        full_name="Job Creator",
        phone_number=None,
        role=role,
        is_active=True,
        created_at=now,
        updated_at=now,
    )


class FakeAuthResult:
    def __init__(self, actor: User) -> None:
        self.actor = actor

    def scalar_one_or_none(self) -> User:
        return self.actor


class MemorySession:
    def __init__(self, actor: User) -> None:
        self.actor = actor
        self.rows: dict[uuid.UUID, JobDescription] = {}
        self.pending: JobDescription | None = None
        self.events: list[str] = []
        self.commit_error: Exception | None = None
        self.race_winner: JobDescription | None = None

    async def execute(self, _: Any) -> FakeAuthResult:
        return FakeAuthResult(self.actor)

    async def get(
        self,
        _: type[JobDescription],
        job_id: uuid.UUID,
        **__: Any,
    ) -> JobDescription | None:
        return self.rows.get(job_id)

    def add(self, job: JobDescription) -> None:
        self.pending = job
        self.events.append("add")

    async def commit(self) -> None:
        self.events.append("commit")
        if self.commit_error is not None:
            error = self.commit_error
            self.commit_error = None
            raise error
        assert self.pending is not None
        now = datetime.now(UTC)
        self.pending.created_at = now
        self.pending.updated_at = now
        self.rows[self.pending.id] = self.pending
        self.pending = None

    async def rollback(self) -> None:
        self.events.append("rollback")
        self.pending = None
        if self.race_winner is not None:
            self.rows[self.race_winner.id] = self.race_winner


class RecordingDispatcher:
    def __init__(self, session: MemorySession, *, fail: bool = False) -> None:
        self.session = session
        self.fail = fail
        self.calls: list[tuple[uuid.UUID, int]] = []

    async def dispatch(self, job_id: uuid.UUID, revision: int) -> None:
        assert job_id in self.session.rows
        self.calls.append((job_id, revision))
        self.session.events.append("dispatch")
        if self.fail:
            raise RuntimeError("redis://queue-user:sensitive-password@localhost")


class JobCreateAPI:
    def __init__(
        self,
        client: AsyncClient,
        actor: User,
        session: MemorySession,
        dispatcher: RecordingDispatcher,
        settings: Settings,
    ) -> None:
        self.client = client
        self.actor = actor
        self.session = session
        self.dispatcher = dispatcher
        self.settings = settings

    def headers(self, key: uuid.UUID | None = None) -> dict[str, str]:
        token = create_access_token(self.actor.id, self.settings)
        result = {"Authorization": f"Bearer {token}"}
        if key is not None:
            result["Idempotency-Key"] = str(key)
        return result


@pytest_asyncio.fixture
async def job_create_api() -> AsyncIterator[JobCreateAPI]:
    actor = make_user()
    session = MemorySession(actor)
    dispatcher = RecordingDispatcher(session)
    settings = Settings(_env_file=None, app_env="test", jwt_secret_key=TEST_SECRET)

    async def override_session() -> AsyncIterator[MemorySession]:
        yield session

    def override_settings() -> Settings:
        return settings

    def override_dispatcher() -> RecordingDispatcher:
        return dispatcher

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_settings] = override_settings
    app.dependency_overrides[get_job_parse_dispatcher] = override_dispatcher
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield JobCreateAPI(client, actor, session, dispatcher, settings)
    finally:
        app.dependency_overrides.clear()


def test_exact_canonical_json_and_fingerprint_vector() -> None:
    payload = JobCreateRequest(
        title=" Backend Engineer ",
        job_level=" Senior ",
        location="   ",
        raw_content="Build APIs\r\nCafe\u0301",
    )
    expected_json = (
        '{"title":"Backend Engineer","job_level":"Senior","location":null,'
        '"raw_content":"Build APIs\\nCafé","w_skill":0.500,'
        '"w_semantic":0.300,"w_experience":0.200}'
    )

    assert canonical_job_create_json(payload) == expected_json
    assert expected_json.encode("utf-8").hex().endswith("302e3230307d")
    assert job_create_fingerprint(payload) == (
        "692e69e52448fe55a791a0c7cf5f2c629695ac91efaca4a42b0025ff0b719434"
    )


def test_negative_zero_weight_uses_same_canonical_server_representation() -> None:
    positive = JobCreateRequest(
        title="Engineer",
        job_level="Senior",
        raw_content="Build APIs",
        w_skill=0,
        w_semantic=0.8,
        w_experience=0.2,
    )
    negative = JobCreateRequest(
        title="Engineer",
        job_level="Senior",
        raw_content="Build APIs",
        w_skill=-0.0,
        w_semantic=0.8,
        w_experience=0.2,
    )

    assert canonical_job_create_json(positive) == canonical_job_create_json(negative)
    assert '"w_skill":0.000' in canonical_job_create_json(negative)


@pytest.mark.asyncio
async def test_hr_create_persists_deterministic_id_initial_state_and_dispatch_order(
    job_create_api: JobCreateAPI,
) -> None:
    key = uuid.UUID("D055AECB-52F8-4F3D-AB55-8DC972B20887")
    response = await job_create_api.client.post(
        "/api/v1/jobs",
        headers=job_create_api.headers(key),
        json=DEFAULT_PAYLOAD,
    )
    expected_id = uuid.uuid5(
        APP_IDEMPOTENCY_NAMESPACE,
        f"{job_create_api.actor.id}\n{JOB_CREATE_ROUTE}\n{str(key).lower()}",
    )

    assert response.status_code == 201
    assert (
        derive_idempotent_resource_id(job_create_api.actor.id, JOB_CREATE_ROUTE, key) == expected_id
    )
    assert response.json()["data"]["id"] == str(expected_id)
    assert response.json()["data"]["recruiter_id"] == str(job_create_api.actor.id)
    assert response.json()["data"]["revision"] == 1
    assert response.json()["data"]["status"] == "DRAFT"
    assert response.json()["data"]["parsing_status"] == "PENDING"
    assert response.json()["data"]["is_criteria_verified"] is False
    assert "create_request_fingerprint" not in response.text
    assert "job_embedding" not in response.text

    row = job_create_api.session.rows[expected_id]
    assert row.create_request_fingerprint == job_create_fingerprint(
        JobCreateRequest(**DEFAULT_PAYLOAD)
    )
    assert row.min_experience_years == Decimal("0.0")
    assert row.education_requirement is None
    assert row.job_embedding is None
    assert row.embedding_model is None
    assert row.embedding_preprocessing_version is None
    assert row.parsing_error_message is None
    assert row.parsed_at is None
    assert row.is_deleted is False and row.deleted_at is None
    assert (row.w_skill, row.w_semantic, row.w_experience) == (
        Decimal("0.500"),
        Decimal("0.300"),
        Decimal("0.200"),
    )
    assert job_create_api.session.events == ["add", "commit", "dispatch"]


@pytest.mark.asyncio
async def test_canonical_equivalents_return_same_persisted_job(
    job_create_api: JobCreateAPI,
) -> None:
    key = uuid.uuid4()
    first_payload = {
        "title": " Cafe\u0301 Engineer ",
        "job_level": " Senior ",
        "location": "   ",
        "raw_content": "Build Cafe\u0301 APIs\r\nSafely",
    }
    second_payload = {
        "title": "Café Engineer",
        "job_level": "Senior",
        "location": None,
        "raw_content": "Build Café APIs\nSafely",
        "w_skill": 0.500,
        "w_semantic": 0.300,
        "w_experience": 0.200,
    }

    first = await job_create_api.client.post(
        "/api/v1/jobs", headers=job_create_api.headers(key), json=first_payload
    )
    second = await job_create_api.client.post(
        "/api/v1/jobs", headers=job_create_api.headers(key), json=second_payload
    )

    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert len(job_create_api.session.rows) == 1
    row = next(iter(job_create_api.session.rows.values()))
    assert row.title == "Café Engineer"
    assert row.job_level == "Senior"
    assert row.location is None
    assert row.raw_content == "Build Café APIs\nSafely"
    assert job_create_api.dispatcher.calls == [(row.id, 1), (row.id, 1)]


@pytest.mark.asyncio
async def test_retry_returns_current_persisted_state_without_resetting_revision(
    job_create_api: JobCreateAPI,
) -> None:
    key = uuid.uuid4()
    first = await job_create_api.client.post(
        "/api/v1/jobs", headers=job_create_api.headers(key), json=DEFAULT_PAYLOAD
    )
    job_id = uuid.UUID(first.json()["data"]["id"])
    row = job_create_api.session.rows[job_id]
    row.revision = 7
    row.parsing_status = "PARSED"
    row.parsed_at = datetime.now(UTC)

    retry = await job_create_api.client.post(
        "/api/v1/jobs",
        headers=job_create_api.headers(key),
        json=DEFAULT_PAYLOAD | {"title": " Backend Engineer "},
    )

    assert retry.status_code == 201
    assert retry.json()["data"]["revision"] == 7
    assert retry.json()["data"]["parsing_status"] == "PARSED"
    assert len(job_create_api.session.rows) == 1
    assert job_create_api.dispatcher.calls == [(job_id, 1)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"title": "Platform Engineer"},
        {"job_level": "Lead"},
        {"location": "Remote"},
        {"raw_content": "Different responsibilities"},
        {"w_skill": 0.4, "w_semantic": 0.4, "w_experience": 0.2},
    ],
    ids=["title", "job-level", "location", "raw-content", "weights"],
)
async def test_same_key_materially_different_payload_conflicts(
    job_create_api: JobCreateAPI,
    changes: dict[str, object],
) -> None:
    key = uuid.uuid4()
    first = await job_create_api.client.post(
        "/api/v1/jobs", headers=job_create_api.headers(key), json=DEFAULT_PAYLOAD
    )
    conflict = await job_create_api.client.post(
        "/api/v1/jobs",
        headers=job_create_api.headers(key),
        json=DEFAULT_PAYLOAD | changes,
    )

    assert first.status_code == 201
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert len(job_create_api.session.rows) == 1


@pytest.mark.asyncio
async def test_create_role_authentication_and_idempotency_header(
    job_create_api: JobCreateAPI,
) -> None:
    unauthenticated = await job_create_api.client.post(
        "/api/v1/jobs",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json=DEFAULT_PAYLOAD,
    )
    missing_key = await job_create_api.client.post(
        "/api/v1/jobs", headers=job_create_api.headers(), json=DEFAULT_PAYLOAD
    )
    invalid_key = await job_create_api.client.post(
        "/api/v1/jobs",
        headers=job_create_api.headers() | {"Idempotency-Key": "invalid"},
        json=DEFAULT_PAYLOAD,
    )
    job_create_api.actor.role = "CANDIDATE"
    candidate = await job_create_api.client.post(
        "/api/v1/jobs", headers=job_create_api.headers(uuid.uuid4()), json=DEFAULT_PAYLOAD
    )
    job_create_api.actor.role = "ADMIN"
    admin = await job_create_api.client.post(
        "/api/v1/jobs", headers=job_create_api.headers(uuid.uuid4()), json=DEFAULT_PAYLOAD
    )

    assert unauthenticated.status_code == 401
    assert missing_key.status_code == invalid_key.status_code == 422
    assert candidate.status_code == admin.status_code == 403
    assert job_create_api.session.rows == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        DEFAULT_PAYLOAD | {"extra": "forbidden"},
        DEFAULT_PAYLOAD | {"title": "   "},
        DEFAULT_PAYLOAD | {"job_level": "   "},
        DEFAULT_PAYLOAD | {"raw_content": ""},
        DEFAULT_PAYLOAD | {"title": "x" * 201},
        DEFAULT_PAYLOAD | {"job_level": "x" * 51},
        DEFAULT_PAYLOAD | {"location": "x" * 151},
        DEFAULT_PAYLOAD | {"w_skill": 0.6},
        DEFAULT_PAYLOAD | {"w_skill": 1.1, "w_semantic": -0.1, "w_experience": 0},
        DEFAULT_PAYLOAD | {"w_skill": 0.5004, "w_semantic": 0.2996},
        DEFAULT_PAYLOAD | {"w_skill": "0.500"},
    ],
)
async def test_request_rejects_extra_empty_limits_invalid_weights_and_db_rounding(
    job_create_api: JobCreateAPI,
    payload: dict[str, object],
) -> None:
    response = await job_create_api.client.post(
        "/api/v1/jobs",
        headers=job_create_api.headers(uuid.uuid4()),
        json=payload,
    )

    assert response.status_code == 422
    assert job_create_api.session.rows == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("title", "Backend\x00Engineer"),
        ("job_level", "Senior\x00"),
        ("location", "Ha\x00noi"),
        ("raw_content", "Build\x00 APIs"),
        ("title", "Backend\ud800 Engineer"),
        ("raw_content", "Build\ud800 APIs"),
        ("job_level", "Senior\udfff"),
    ],
    ids=[
        "title-nul",
        "job-level-nul",
        "location-nul",
        "raw-content-nul",
        "title-high-surrogate",
        "raw-content-high-surrogate",
        "job-level-low-surrogate",
    ],
)
async def test_request_rejects_non_postgresql_and_non_utf8_text_before_side_effects(
    job_create_api: JobCreateAPI,
    field: str,
    invalid_value: str,
) -> None:
    payload = DEFAULT_PAYLOAD | {field: invalid_value}
    response = await job_create_api.client.post(
        "/api/v1/jobs",
        headers=job_create_api.headers(uuid.uuid4()) | {"Content-Type": "application/json"},
        content=json.dumps(payload, ensure_ascii=True).encode("ascii"),
    )

    assert response.status_code == 422
    assert job_create_api.session.rows == {}
    assert job_create_api.dispatcher.calls == []
    assert job_create_api.session.events == []


@pytest.mark.asyncio
async def test_valid_vietnamese_controls_and_normalization_remain_accepted(
    job_create_api: JobCreateAPI,
) -> None:
    payload = DEFAULT_PAYLOAD | {
        "title": " Ky\u0303 su\u031b ",
        "job_level": " Cao ca\u0302\u0301p ",
        "location": " Ha\u0300 No\u0302\u0323i ",
        "raw_content": "Mo\u0302 ta\u0309:\r\n\tKy\u0303 na\u0306ng",
    }
    response = await job_create_api.client.post(
        "/api/v1/jobs",
        headers=job_create_api.headers(uuid.uuid4()),
        json=payload,
    )

    assert response.status_code == 201
    row = next(iter(job_create_api.session.rows.values()))
    assert row.title == "Kỹ sư"
    assert row.job_level == "Cao cấp"
    assert row.location == "Hà Nội"
    assert row.raw_content == "Mô tả:\n\tKỹ năng"
    for value in (row.title, row.job_level, row.location, row.raw_content):
        assert value is not None
        value.encode("utf-8")
    assert job_create_api.dispatcher.calls == [(row.id, 1)]


@pytest.mark.asyncio
async def test_different_users_with_same_key_receive_different_ids() -> None:
    key = uuid.uuid4()
    first_user = make_user(label="first")
    second_user = make_user(label="second")
    first_session = MemorySession(first_user)
    second_session = MemorySession(second_user)
    payload = JobCreateRequest(**DEFAULT_PAYLOAD)

    first = await create_job(
        first_session,  # type: ignore[arg-type]
        current_user=first_user,
        idempotency_key=key,
        payload=payload,
        dispatcher=RecordingDispatcher(first_session),
    )
    second = await create_job(
        second_session,  # type: ignore[arg-type]
        current_user=second_user,
        idempotency_key=key,
        payload=payload,
        dispatcher=RecordingDispatcher(second_session),
    )

    assert first.id != second.id


@pytest.mark.asyncio
async def test_dispatch_failure_keeps_committed_pending_and_retry_succeeds() -> None:
    user = make_user()
    session = MemorySession(user)
    dispatcher = RecordingDispatcher(session, fail=True)
    key = uuid.uuid4()
    payload = JobCreateRequest(**DEFAULT_PAYLOAD)

    first = await create_job(
        session,  # type: ignore[arg-type]
        current_user=user,
        idempotency_key=key,
        payload=payload,
        dispatcher=dispatcher,
    )
    second = await create_job(
        session,  # type: ignore[arg-type]
        current_user=user,
        idempotency_key=key,
        payload=payload,
        dispatcher=dispatcher,
    )

    assert first is second
    assert first.parsing_status == "PENDING"
    assert session.events == ["add", "commit", "dispatch", "dispatch"]
    assert "sensitive-password" not in str(first)


@pytest.mark.asyncio
async def test_dispatch_failure_endpoint_still_returns_201_without_sensitive_details(
    job_create_api: JobCreateAPI,
) -> None:
    job_create_api.dispatcher.fail = True

    response = await job_create_api.client.post(
        "/api/v1/jobs",
        headers=job_create_api.headers(uuid.uuid4()),
        json=DEFAULT_PAYLOAD,
    )

    assert response.status_code == 201
    assert response.json()["data"]["parsing_status"] == "PENDING"
    assert "sensitive-password" not in response.text
    assert len(job_create_api.session.rows) == 1
    assert "rollback" not in job_create_api.session.events


@pytest.mark.asyncio
async def test_deterministic_primary_key_race_recovers_winner_or_conflicts() -> None:
    user = make_user()
    key = uuid.uuid4()
    payload = JobCreateRequest(**DEFAULT_PAYLOAD)
    winning_session = MemorySession(user)
    winner = await create_job(
        winning_session,  # type: ignore[arg-type]
        current_user=user,
        idempotency_key=key,
        payload=payload,
        dispatcher=RecordingDispatcher(winning_session),
    )

    losing_session = MemorySession(user)
    losing_session.commit_error = IntegrityError("INSERT", {}, RuntimeError("duplicate key"))
    losing_session.race_winner = winner
    recovered = await create_job(
        losing_session,  # type: ignore[arg-type]
        current_user=user,
        idempotency_key=key,
        payload=payload,
        dispatcher=RecordingDispatcher(losing_session),
    )
    assert recovered is winner
    assert losing_session.events == ["add", "commit", "rollback", "dispatch"]

    conflicting = MemorySession(user)
    conflicting.commit_error = IntegrityError("INSERT", {}, RuntimeError("duplicate key"))
    conflicting.race_winner = winner
    with pytest.raises(APIError) as raised:
        await create_job(
            conflicting,  # type: ignore[arg-type]
            current_user=user,
            idempotency_key=key,
            payload=JobCreateRequest(**(DEFAULT_PAYLOAD | {"title": "Different"})),
            dispatcher=RecordingDispatcher(conflicting),
        )
    assert raised.value.code == "IDEMPOTENCY_KEY_REUSED"

    unrelated = MemorySession(user)
    unrelated.commit_error = IntegrityError("INSERT", {}, RuntimeError("foreign key"))
    with pytest.raises(IntegrityError):
        await create_job(
            unrelated,  # type: ignore[arg-type]
            current_user=user,
            idempotency_key=uuid.uuid4(),
            payload=payload,
            dispatcher=RecordingDispatcher(unrelated),
        )
    assert unrelated.events == ["add", "commit", "rollback"]


def test_generated_openapi_matches_canonical_create_contract() -> None:
    schema = app.openapi()
    operation = schema["paths"]["/api/v1/jobs"]["post"]
    request_schema = schema["components"]["schemas"]["JobCreateRequest"]

    assert operation["responses"].keys() >= {"201", "422"}
    assert any(parameter["name"] == "Idempotency-Key" for parameter in operation["parameters"])
    assert request_schema["required"] == ["title", "job_level", "raw_content"]
    assert request_schema["additionalProperties"] is False
    for name, default in (("w_skill", 0.5), ("w_semantic", 0.3), ("w_experience", 0.2)):
        weight_schema = request_schema["properties"][name]
        assert weight_schema["type"] == "number"
        assert weight_schema["minimum"] == 0
        assert weight_schema["maximum"] == 1
        assert weight_schema["default"] == default
    assert set(schema["paths"]["/api/v1/jobs"]) == {"get", "post"}
    assert set(schema["paths"]["/api/v1/jobs/{id}"]) == {"get", "delete"}
    assert set(schema["paths"]["/api/v1/jobs/{id}/status"]) == {"patch"}
