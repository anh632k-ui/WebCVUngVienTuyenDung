# Backend foundation

FastAPI and async SQLAlchemy foundation mapped to the existing canonical PostgreSQL 18
schema. The application never creates or migrates database objects.

## Local setup

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
# Replace placeholders in .env with local credentials.
uvicorn main:app --reload
```

Health is available at `GET /api/v1/health`. With no `DATABASE_URL`, startup remains
available and health reports `database.status=not_configured`. A configured but unreachable
database returns HTTP 503 without exposing connection details.

Run verification with:

```powershell
python -m pytest
python -m ruff check .
python -m mypy app
```
