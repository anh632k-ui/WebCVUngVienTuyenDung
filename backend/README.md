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

## Parse and Match queue

Resume upload works without queue dependencies or Redis. In that mode the committed resume stays
`PENDING`, dispatch is a no-op, and the recovery loop is disabled. To run asynchronous parsing,
install both optional runtime groups and configure `CELERY_BROKER_URL` to a shared Redis broker:

```powershell
python -m pip install -e ".[queue,ai]"
```

Parse tasks publish only the resource ID and expected revision. Match tasks use `match.calculate`
with `match_id, expected_generation, expected_resume_revision, expected_job_revision,
algorithm_version`; scoring context and embeddings are loaded from PostgreSQL by the worker.
Match computation uses stored embeddings and does not load BGE-M3. Start Redis separately, then
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
API recovery loops periodically republish old, active `PENDING` work. Parse recovery uses current
resource revisions. Match recovery selects only supported algorithms with snapshots matching both
active linked resources and republishes the exact stored generation, revisions and algorithm.
Selection sessions close before broker publication. Recovery never changes state, generations,
revisions, scoring payloads or `updated_at`; it never resets `PROCESSING`. Duplicate deliveries and
mutations after selection are rejected by the existing claim/terminal compare-and-set guards.
Broker publication failures are logged without connection credentials and do not undo persistence.

Match recovery starts with the API when both database and broker are configured. Its bounded
settings are `MATCH_RECOVERY_GRACE_SECONDS` (default 300), `MATCH_RECOVERY_INTERVAL_SECONDS`
(default 60), and `MATCH_RECOVERY_BATCH_SIZE` (default 100). With no broker, Match dispatch is a
no-op and the sweep is disabled. The matching trigger/batch endpoint is a separate feature.
