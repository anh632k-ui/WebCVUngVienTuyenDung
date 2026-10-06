from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.db_base import Base
from app.core.engine_factory import create_engine

__all__ = ["Base", "create_engine"]


settings = get_settings()
engine = create_engine(settings)
SessionFactory: async_sessionmaker[AsyncSession] | None = (
    async_sessionmaker(engine, expire_on_commit=False) if engine is not None else None
)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    if SessionFactory is None:
        raise RuntimeError("Database is not configured")
    async with SessionFactory() as session:
        yield session


async def check_database_connection(
    database_engine: AsyncEngine | None = engine,
    timeout_seconds: float | None = None,
) -> tuple[bool, str]:
    if database_engine is None:
        return False, "not_configured"

    timeout = timeout_seconds or settings.database_connect_timeout_seconds
    try:
        async with asyncio.timeout(timeout):
            async with database_engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - health checks intentionally collapse driver failures
        # Deliberately return a non-sensitive status; callers log no connection URL/details.
        return False, "unavailable"
    return True, "connected"


async def dispose_engine() -> None:
    if engine is not None:
        await engine.dispose()
