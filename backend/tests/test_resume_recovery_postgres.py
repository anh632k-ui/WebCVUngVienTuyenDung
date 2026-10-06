from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, AsyncTransaction

from app.ai.resume_parser import RESUME_TEXT_PREPROCESSING_VERSION
from app.ai.vector_embedding import BGE_M3_EMBEDDING_DIMENSION, BGE_M3_MODEL_NAME
from app.core.config import Settings
from app.core.database import create_engine
from app.models.resume import Resume
from app.models.user import User
from app.services.resume_recovery import (
    recover_pending_resumes,
    select_resume_recovery_candidates,
)


class RecordingDispatcher:
    def __init__(self, *, fail_id: uuid.UUID | None = None) -> None:
        self.calls: list[tuple[uuid.UUID, int]] = []
        self.fail_id = fail_id

    async def dispatch(self, resume_id: uuid.UUID, revision: int) -> None:
        self.calls.append((resume_id, revision))
        if resume_id == self.fail_id:
            raise RuntimeError("broker unavailable")


@dataclass
class RecoveryHarness:
    connection: AsyncConnection
    transaction: AsyncTransaction
    setup_session: AsyncSession
    session_factory: object
    user: User

    async def create_resume(
        self,
        *,
        updated_at: datetime,
        status: str = "PENDING",
        revision: int = 1,
        deleted: bool = False,
    ) -> Resume:
        resume_id = uuid.uuid4()
        source = f"recovery-{resume_id}".encode()
        parsed = status == "PARSED"
        resume = Resume(
            id=resume_id,
            owner_user_id=self.user.id,
            file_name="recovery.pdf",
            storage_key=f"recovery-tests/{resume_id}/source",
            file_size=len(source),
            mime_type="application/pdf",
            create_request_fingerprint=hashlib.sha256(source).hexdigest(),
            revision=revision,
            parsing_status=status,
            raw_text="parsed" if parsed else None,
            resume_embedding=[0.0] * BGE_M3_EMBEDDING_DIMENSION if parsed else None,
            embedding_model=BGE_M3_MODEL_NAME if parsed else None,
            embedding_preprocessing_version=(RESUME_TEXT_PREPROCESSING_VERSION if parsed else None),
            error_message="controlled failure" if status == "FAILED" else None,
            is_deleted=deleted,
            deleted_at=updated_at if deleted else None,
            created_at=updated_at,
            updated_at=updated_at,
            parsed_at=updated_at if parsed else None,
        )
        self.setup_session.add(resume)
        await self.setup_session.commit()
        return resume


def integration_database_settings() -> Settings:
    configured = Settings()
    if configured.database_url is None:
        pytest.skip("DATABASE_URL is not configured")
    return Settings(
        _env_file=None,
        app_env="test",
        database_url=configured.database_url,
        database_connect_timeout_seconds=configured.database_connect_timeout_seconds,
    )


@pytest_asyncio.fixture
async def recovery_harness() -> AsyncIterator[RecoveryHarness]:
    engine = create_engine(integration_database_settings())
    assert engine is not None
    try:
        connection = await engine.connect()
    except Exception:  # noqa: BLE001 - do not expose configured database credentials
        await engine.dispose()
        pytest.fail("Configured PostgreSQL database is unavailable", pytrace=False)
    transaction = await connection.begin()

    def session_factory() -> AsyncSession:
        return AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )

    setup_session = session_factory()
    now = datetime.now(UTC)
    user = User(
        id=uuid.uuid4(),
        email=f"resume.recovery.{uuid.uuid4().hex}@example.com",
        password_hash="not-used-by-recovery-integration",
        full_name="Resume Recovery Integration",
        phone_number=None,
        role="CANDIDATE",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    setup_session.add(user)
    await setup_session.commit()
    user_id = user.id
    try:
        yield RecoveryHarness(connection, transaction, setup_session, session_factory, user)
    finally:
        await setup_session.close()
        if transaction.is_active:
            await transaction.rollback()
        remaining = await connection.scalar(
            select(func.count()).select_from(User).where(User.id == user_id)
        )
        if connection.in_transaction():
            await connection.rollback()
        await connection.close()
        await engine.dispose()
        assert remaining == 0


@pytest.mark.asyncio
async def test_real_postgres_selection_is_stale_pending_active_ordered_and_bounded(
    recovery_harness: RecoveryHarness,
) -> None:
    harness = recovery_harness
    now = datetime.now(UTC)
    oldest = await harness.create_resume(updated_at=now - timedelta(minutes=20), revision=2)
    middle = await harness.create_resume(updated_at=now - timedelta(minutes=15), revision=5)
    newest = await harness.create_resume(updated_at=now - timedelta(minutes=10), revision=8)
    await harness.create_resume(updated_at=now - timedelta(seconds=30))
    await harness.create_resume(updated_at=now - timedelta(minutes=20), status="PROCESSING")
    await harness.create_resume(updated_at=now - timedelta(minutes=20), status="PARSED")
    await harness.create_resume(updated_at=now - timedelta(minutes=20), status="FAILED")
    await harness.create_resume(updated_at=now - timedelta(minutes=20), deleted=True)

    candidates = await select_resume_recovery_candidates(
        harness.session_factory,  # type: ignore[arg-type]
        cutoff=now - timedelta(minutes=5),
        batch_size=2,
    )

    assert [(item.resume_id, item.revision) for item in candidates] == [
        (oldest.id, 2),
        (middle.id, 5),
    ]
    assert newest.id not in {item.resume_id for item in candidates}


@pytest.mark.asyncio
async def test_real_postgres_recovery_dispatches_current_revision_without_mutation(
    recovery_harness: RecoveryHarness,
) -> None:
    harness = recovery_harness
    now = datetime.now(UTC)
    first = await harness.create_resume(updated_at=now - timedelta(minutes=20), revision=3)
    second = await harness.create_resume(updated_at=now - timedelta(minutes=10), revision=9)
    first_id, second_id = first.id, second.id
    original = {first_id: first.updated_at, second_id: second.updated_at}
    dispatcher = RecordingDispatcher(fail_id=first_id)

    result = await recover_pending_resumes(
        harness.session_factory,  # type: ignore[arg-type]
        dispatcher,
        grace_seconds=300,
        batch_size=10,
        now=now,
    )

    assert dispatcher.calls == [(first_id, 3), (second_id, 9)]
    assert (result.selected, result.dispatched, result.failed) == (2, 1, 1)
    harness.setup_session.expire_all()
    persisted = list(
        (
            await harness.setup_session.scalars(
                select(Resume).where(Resume.id.in_([first_id, second_id])).order_by(Resume.id)
            )
        ).all()
    )
    assert all(resume.parsing_status == "PENDING" for resume in persisted)
    assert all(resume.updated_at == original[resume.id] for resume in persisted)
