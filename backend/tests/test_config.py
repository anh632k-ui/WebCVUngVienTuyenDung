import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_defaults_do_not_require_database_credentials() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_env == "development"
    assert settings.database_url is None
    assert settings.database_connect_timeout_seconds == 5.0
    assert settings.jwt_secret_key is None
    assert settings.jwt_algorithm == "HS256"
    assert settings.access_token_expire_minutes == 15
    assert settings.celery_broker_url is None
    assert settings.resume_recovery_grace_seconds == 300
    assert settings.resume_recovery_interval_seconds == 60
    assert settings.resume_recovery_batch_size == 100
    assert settings.job_recovery_grace_seconds == 300
    assert settings.job_recovery_interval_seconds == 60
    assert settings.job_recovery_batch_size == 100


def test_accepts_canonical_async_database_url() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://user:placeholder@localhost:5432/webcv_ungvien",
    )

    assert settings.database_url is not None


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql://user:placeholder@localhost:5432/webcv_ungvien",
        "postgresql+asyncpg://user:placeholder@localhost:5432/another_database",
    ],
)
def test_rejects_noncanonical_database_url(database_url: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=database_url)


def test_queue_configuration_is_optional_bounded_and_secret() -> None:
    settings = Settings(
        _env_file=None,
        celery_broker_url="redis://queue-user:queue-password@localhost:6379/0",
        resume_recovery_grace_seconds=1,
        resume_recovery_interval_seconds=1,
        resume_recovery_batch_size=1,
        job_recovery_grace_seconds=1,
        job_recovery_interval_seconds=1,
        job_recovery_batch_size=1,
    )

    assert settings.celery_broker_url is not None
    assert "queue-password" not in repr(settings.celery_broker_url)
    assert Settings(_env_file=None, celery_broker_url=" ").celery_broker_url is None
    with pytest.raises(ValidationError):
        Settings(_env_file=None, resume_recovery_batch_size=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, job_recovery_batch_size=0)
