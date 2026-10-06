from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import Settings


def create_engine(settings: Settings) -> AsyncEngine | None:
    """Create a canonical async engine without application-global side effects."""

    if settings.database_url is None:
        return None
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        connect_args={"timeout": settings.database_connect_timeout_seconds},
    )
