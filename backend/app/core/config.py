from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Application settings loaded from environment variables and backend/.env."""

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "WebCVUngVienTuyenDung API"
    app_version: str = "0.1.0"
    app_env: Literal["development", "test", "production"] = "development"
    log_level: Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"] = "INFO"
    database_url: SecretStr | None = None
    # Explicit opt-in for an isolated, loopback-only PostgreSQL QA database.
    test_database_name: str | None = Field(
        default=None,
        pattern=r"^webcv_(?:test|task|qa)[a-z0-9_]{1,50}$",
    )
    database_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    celery_broker_url: SecretStr | None = None
    resume_recovery_grace_seconds: int = Field(default=300, ge=1, le=86_400)
    resume_recovery_interval_seconds: int = Field(default=60, ge=1, le=3_600)
    resume_recovery_batch_size: int = Field(default=100, ge=1, le=1_000)
    job_recovery_grace_seconds: int = Field(default=300, ge=1, le=86_400)
    job_recovery_interval_seconds: int = Field(default=60, ge=1, le=3_600)
    job_recovery_batch_size: int = Field(default=100, ge=1, le=1_000)
    match_recovery_grace_seconds: int = Field(default=300, ge=1, le=86_400)
    match_recovery_interval_seconds: int = Field(default=60, ge=1, le=3_600)
    match_recovery_batch_size: int = Field(default=100, ge=1, le=1_000)
    jwt_secret_key: SecretStr | None = Field(default=None, min_length=32)
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    access_token_expire_minutes: int = Field(default=15, gt=0, le=1440)
    resume_storage_root: Path = BACKEND_DIR.parent / ".local" / "resume_storage"

    @field_validator("database_url", mode="before")
    @classmethod
    def validate_database_url(cls, value: str | SecretStr | None) -> str | SecretStr | None:
        if value is None:
            return None
        raw_value = value.get_secret_value() if isinstance(value, SecretStr) else value
        if not raw_value.strip():
            return None
        if not raw_value.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use the postgresql+asyncpg driver")
        return value

    @model_validator(mode="after")
    def validate_database_target(self) -> Self:
        if self.test_database_name is not None and self.app_env != "test":
            raise ValueError("TEST_DATABASE_NAME is only permitted with APP_ENV=test")
        if self.database_url is None:
            return self

        try:
            url = make_url(self.database_url.get_secret_value())
        except (ArgumentError, ValueError) as exc:
            raise ValueError("DATABASE_URL is not a valid SQLAlchemy URL") from exc

        if url.database == "webcv_ungvien" and self.test_database_name is None:
            return self

        if not (
            self.app_env == "test"
            and self.test_database_name is not None
            and url.database == self.test_database_name
            and url.host in {"localhost", "127.0.0.1", "::1"}
            and url.port == 55432
            and not url.query
        ):
            raise ValueError(
                "DATABASE_URL must target webcv_ungvien, or the explicitly opted-in "
                "loopback QA database on port 55432 when APP_ENV=test"
            )
        return self

    @field_validator("jwt_secret_key", mode="before")
    @classmethod
    def normalize_jwt_secret(cls, value: str | SecretStr | None) -> str | SecretStr | None:
        if value is None:
            return None
        raw_value = value.get_secret_value() if isinstance(value, SecretStr) else value
        return value if raw_value.strip() else None

    @field_validator("celery_broker_url", mode="before")
    @classmethod
    def normalize_celery_broker_url(
        cls,
        value: str | SecretStr | None,
    ) -> str | SecretStr | None:
        if value is None:
            return None
        raw_value = value.get_secret_value() if isinstance(value, SecretStr) else value
        return value if raw_value.strip() else None


@lru_cache
def get_settings() -> Settings:
    return Settings()
