import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

# PostgreSQL integration jobs opt in by exporting DATABASE_URL before pytest
# starts. Ordinary unit QA gets an explicit blank that overrides backend/.env
# during test-module collection, so importing main cannot open a local database.
DATABASE_URL_EXPLICIT_AT_PYTEST_BOOTSTRAP = "DATABASE_URL" in os.environ
if not DATABASE_URL_EXPLICIT_AT_PYTEST_BOOTSTRAP:
    os.environ["DATABASE_URL"] = ""
