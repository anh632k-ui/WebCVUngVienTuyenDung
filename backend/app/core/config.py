from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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
        database_name = raw_value.split("?", maxsplit=1)[0].rstrip("/").rsplit("/", maxsplit=1)[-1]
        if database_name != "webcv_ungvien":
            raise ValueError("DATABASE_URL must target the canonical webcv_ungvien database")
        return value

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
