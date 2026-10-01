import pytest

from app.core.config import Settings
from app.core.database import check_database_connection, create_engine


@pytest.mark.asyncio
async def test_unconfigured_database_has_explicit_status() -> None:
    connected, connection_status = await check_database_connection(None)

    assert connected is False
    assert connection_status == "not_configured"


def test_no_engine_is_created_without_database_url() -> None:
    assert create_engine(Settings(_env_file=None, database_url=None)) is None


@pytest.mark.asyncio
async def test_real_postgresql_connectivity_when_configured() -> None:
    integration_settings = Settings()
    integration_engine = create_engine(integration_settings)
    if integration_engine is None:
        pytest.skip("DATABASE_URL is not configured")

    try:
        connected, connection_status = await check_database_connection(
            integration_engine,
            timeout_seconds=integration_settings.database_connect_timeout_seconds,
        )
    finally:
        await integration_engine.dispose()

    assert connected is True
    assert connection_status == "connected"
