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
