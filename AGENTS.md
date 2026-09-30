# WebCVUngVienTuyenDung - Agent Instructions

## Git workflow

- `main` is the stable milestone branch.
- `dev` is the integration branch.
- Development must happen on `feature/*` branches.
- Current work must stay on `feature/backend-foundation`.
- Never merge automatically into `dev`.
- Never merge automatically into `main`.
- Do not force push.

## Canonical design authority

The canonical design is under `docs/`.

Important sources:

- `docs/PTTK_MASTER.md`
- `docs/00_requirements/`
- `docs/05_database/schema.sql`
- `docs/05_database/data_dictionary.md`
- `docs/06_architecture/`
- `docs/07_api/`
- `docs/08_traceability/`
- `docs/09_delivery/`

Do not invent requirements that conflict with these documents.

## Database

Database: `webcv_ungvien`

Target:
- PostgreSQL 18
- pgvector

The canonical database already exists and contains exactly 10 tables.

Never use:

- `Base.metadata.create_all()`
- ORM-generated replacement schema
- destructive database resets unless explicitly instructed

ORM models must map the existing canonical schema.

## Backend target

Backend stack:

- Python
- FastAPI
- SQLAlchemy async
- asyncpg
- Pydantic

## Current feature scope

Current branch:

`feature/backend-foundation`

Implement backend foundation only.

Allowed scope:

- Python project foundation
- dependency management
- configuration/settings
- environment handling
- async SQLAlchemy engine/session
- PostgreSQL connectivity
- FastAPI application entry point
- health endpoint
- ORM mapping for the canonical schema
- relevant tests

Do NOT implement yet:

- authentication business logic
- Resume parsing
- JD parsing
- matching
- AI/NLP
- frontend business features

## Reliability

The existing schema and design include:

- resource revisions
- Match generation
- request fingerprints
- parsing lifecycle constraints
- deterministic idempotency design
- CAS worker design

Do not weaken or remove those invariants.

## Secrets

- Never commit `.env`.
- Never print passwords or secrets.
- Provide `.env.example` only with placeholders.

## Completion rules

Before declaring the task complete:

1. Inspect all changed files.
2. Run relevant tests.
3. Run lint/type checks if configured.
4. Run `git status`.
5. Report what passed.
6. Report what was not testable.
7. Do not merge any branch.