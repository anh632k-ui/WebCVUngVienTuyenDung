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

## Resume parse queue

Resume upload works without queue dependencies or Redis. In that mode the committed resume stays
`PENDING`, dispatch is a no-op, and the recovery loop is disabled. To run asynchronous parsing,
install both optional runtime groups and configure `CELERY_BROKER_URL` to a shared Redis broker:

```powershell
python -m pip install -e ".[queue,ai]"
```

The API process publishes only the resume ID and expected revision. Start Redis separately, then
run a worker from `backend/`. Windows development uses Celery's single-process pool:

```powershell
celery -A app.tasks.celery_worker:app worker --loglevel=INFO --pool=solo
```

Linux production may use the prefork pool; model and database resources are initialized lazily in
each child after fork:

```bash
celery -A app.tasks.celery_worker:app worker --loglevel=INFO --pool=prefork
```

API and worker processes must use the same `DATABASE_URL`, `RESUME_STORAGE_ROOT`, and broker. The
API recovery loop periodically republishes old, active `PENDING` revisions. It never changes resume
state; duplicate deliveries are safe because the parse worker claims work with the existing
revision/status compare-and-set guard. Broker publication failures are logged without connection
credentials and do not undo a committed upload.
