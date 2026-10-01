from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.v1.endpoints import health as health_module
from app.core.database import check_database_connection
from main import app


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as test_client:
            yield test_client


@pytest.mark.asyncio
async def test_startup_and_health_without_database(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def not_configured() -> tuple[bool, str]:
        return await check_database_connection(None)

    monkeypatch.setattr(health_module, "check_database_connection", not_configured)
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "degraded",
        "database": {"status": "not_configured"},
    }


@pytest.mark.asyncio
async def test_health_reports_connected_database(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def connected() -> tuple[bool, str]:
        return True, "connected"

    monkeypatch.setattr(health_module, "check_database_connection", connected)
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": {"status": "connected"}}


@pytest.mark.asyncio
async def test_health_reports_database_failure(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unavailable() -> tuple[bool, str]:
        return False, "unavailable"

    monkeypatch.setattr(health_module, "check_database_connection", unavailable)
    response = await client.get("/api/v1/health")

    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "database": {"status": "unavailable"},
    }
