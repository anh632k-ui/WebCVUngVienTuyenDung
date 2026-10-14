import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.database import check_database_connection, create_engine

BACKEND_DIR = Path(__file__).resolve().parents[1]


def test_unit_pytest_bootstrap_cannot_load_database_url_from_dotenv() -> None:
    environment = os.environ.copy()
    environment.pop("DATABASE_URL", None)
    script = (
        "import runpy; "
        "runpy.run_path('tests/conftest.py'); "
        "from app.core.config import get_settings; "
        "from app.core.database import engine; "
        "assert get_settings().database_url is None; "
        "assert engine is None"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_DIR,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


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
