import os

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_defaults_do_not_require_database_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    # Defaults are independent of the runtime configuration used by integration
    # tests. _env_file=None disables dotenv, but still reads process variables.
    for name in tuple(os.environ):
        if name.lower() in Settings.model_fields:
            monkeypatch.delenv(name)
    settings = Settings(_env_file=None)

    assert settings.app_env == "development"
    assert settings.database_url is None
    assert settings.test_database_name is None
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
    assert settings.match_recovery_grace_seconds == 300
    assert settings.match_recovery_interval_seconds == 60
    assert settings.match_recovery_batch_size == 100


def test_accepts_canonical_async_database_url() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://user:placeholder@localhost:5432/webcv_ungvien",
        test_database_name=None,
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
        Settings(_env_file=None, database_url=database_url, test_database_name=None)


QA_URL = "postgresql+asyncpg://user:placeholder@127.0.0.1:55432/webcv_task6b_clean"
QA_NAME = "webcv_task6b_clean"


def test_accepts_explicit_test_database_only_on_qa_loopback() -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        test_database_name=QA_NAME,
        database_url=QA_URL,
    )
    assert settings.database_url is not None
    assert settings.database_url.get_secret_value() == QA_URL
    assert settings.test_database_name == QA_NAME


def test_test_database_opt_in_is_loaded_from_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("TEST_DATABASE_NAME", QA_NAME)
    monkeypatch.setenv("DATABASE_URL", QA_URL)
    settings = Settings(_env_file=None)
    assert settings.app_env == "test"
    assert settings.test_database_name == QA_NAME
    assert settings.database_url is not None


@pytest.mark.parametrize("app_env", ["development", "production"])
def test_test_database_opt_in_forbidden_outside_test(app_env: str) -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_env=app_env,
            test_database_name=QA_NAME,
            database_url=QA_URL,
        )


@pytest.mark.parametrize(
    ("url", "allowed_name"),
    [
        (QA_URL, None),
        (QA_URL, "webcv_task_other"),
        (
            "postgresql+asyncpg://user:placeholder@127.0.0.1:5432/webcv_task6b_clean",
            QA_NAME,
        ),
        (
            "postgresql+asyncpg://user:placeholder@db.example.org:55432/webcv_task6b_clean",
            QA_NAME,
        ),
        (
            "postgresql+asyncpg://user:placeholder@127.0.0.1:55432/webcv_task6b_clean?host=elsewhere",
            QA_NAME,
        ),
        (
            "postgresql+asyncpg://user:placeholder@127.0.0.1:55432/webcv_task6b_clean?database=other",
            QA_NAME,
        ),
        (
            "postgresql+asyncpg://user:placeholder@127.0.0.1:55432/webcv_ungvien",
            QA_NAME,
        ),
    ],
)
def test_test_database_requires_exact_name_host_port_and_no_query_overrides(
    url: str,
    allowed_name: str | None,
) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="test", test_database_name=allowed_name, database_url=url)


@pytest.mark.parametrize(
    "name",
    ["other_database", "webcv_ungvien", "webcv_task_bad-name", "../webcv_task6b_clean"],
)
def test_invalid_test_database_opt_in_name_is_rejected(name: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="test", test_database_name=name, database_url=QA_URL)


def test_test_database_opt_in_does_not_force_database_for_unit_harness() -> None:
    settings = Settings(
        _env_file=None, app_env="test", test_database_name=QA_NAME, database_url=None
    )
    assert settings.database_url is None


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
        match_recovery_grace_seconds=1,
        match_recovery_interval_seconds=1,
        match_recovery_batch_size=1,
    )

    assert settings.celery_broker_url is not None
    assert "queue-password" not in repr(settings.celery_broker_url)
    assert Settings(_env_file=None, celery_broker_url=" ").celery_broker_url is None
    with pytest.raises(ValidationError):
        Settings(_env_file=None, resume_recovery_batch_size=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, job_recovery_batch_size=0)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("match_recovery_grace_seconds", 0),
        ("match_recovery_grace_seconds", 86_401),
        ("match_recovery_interval_seconds", 0),
        ("match_recovery_interval_seconds", 3_601),
        ("match_recovery_batch_size", 0),
        ("match_recovery_batch_size", 1_001),
    ],
)
def test_match_recovery_settings_are_bounded(name: str, value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{name: value})
