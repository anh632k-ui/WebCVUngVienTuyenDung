# Task 6C-R3C Phase 1 migration plan

Status: canonical source artifacts synchronized; physical database deployment **NOT performed**.
Migration execution remains **NOT AUTHORIZED FOR LOCAL/QA/STAGING/PRODUCTION** by this document.

## 1. Baseline SHA

Prepared from `origin/dev` at `a38ec82d81e915581766d0ae6c38c717696a8724` on
`feature/processing-fencing-schema-r3c-p1`, after the read-only R3C design merged.

## 2. Migration scope

The reviewed Phase 1 migration is expand-only. It adds persistent attempt metadata to `resumes`,
`job_descriptions`, and `match_results`, plus three future lease lookup indexes. It
does not add a table, change lifecycle constraints, backfill owners, or implement
runtime fencing, activation, heartbeat, reclaim, or redispatch.

## 3. Current and proposed schema

The synchronized canonical physical schema still has exactly ten tables and now includes the same
five columns on each target table. Existing databases that predate synchronization do not gain
these objects from documentation changes; they require the reviewed migration and separate
deployment approval.

| Column | Type and Phase 1 default |
|---|---|
| `processing_attempt_epoch` | `BIGINT NOT NULL DEFAULT 0` |
| `processing_owner_token` | nullable `UUID` |
| `processing_activation_token` | nullable `UUID` |
| `processing_heartbeat_at` | nullable `TIMESTAMPTZ` |
| `processing_lease_expires_at` | nullable `TIMESTAMPTZ` |

The migration and canonical bootstrap create no ownership-required `CHECK`, including `NOT VALID`,
and no trigger. Canonical `schema.sql`, PDM, and data dictionary are synchronized with the approved
Phase 1 shape. This is a source-artifact change only and does **not** claim that a local, QA,
staging, production, or other existing physical database has been upgraded.

## 4. DDL files and run order

After an approved target passes all gates, run:

1. `docs/05_database/migrations/r3c_phase1_expand.sql` with `psql` and
   `ON_ERROR_STOP`.
2. Inspect the committed column result.
3. `docs/05_database/migrations/r3c_phase1_indexes.sql` with `psql`, outside any
   transaction wrapper.
4. Inspect all indexes and application health before closing the change.

The index file uses `psql` conditionals so a correct existing index is accepted,
while a conflicting or invalid object causes refusal before index creation.

## 5. Transaction and lock strategy

Column expansion is one transaction with a 5-second `lock_timeout` and 60-second
`statement_timeout`; timeout rolls back all additions. Each `ALTER TABLE` needs a
brief `ACCESS EXCLUSIVE` lock. PostgreSQL's constant-default path should avoid a
row rewrite, but lock acquisition still requires a quiet, observed window.

Indexes run outside a transaction with `CREATE INDEX CONCURRENTLY`, a 5-second
lock timeout, and 10-minute statement timeout. An interrupted build can leave an
invalid index. The script never drops it automatically; operators must stop and
obtain a separately reviewed repair plan.

## 6. PostgreSQL 18 compatibility

CI runs both the legacy fixture and canonical schema on `pgvector/pgvector:0.8.6-pg18` and rejects
servers whose `server_version_num / 10000` is not 18. It validates UUID, bigint,
timestamptz, catalog metadata, partial predicates, and `pg_index.indisvalid`.

## 7. Legacy worker compatibility

ORM models intentionally remain unmapped to the new columns in Phase 1. Old
INSERT statements receive epoch `0` and NULL ownership fields. Existing claim and
terminal CAS statements update named legacy columns, so they require no token and
do not increment epoch. CI calls the current Resume, Job, and Match claim/terminal
implementations on the migrated schema. Existing PROCESSING rows remain unfenced
and must only be observed until later phases.

## 8. Index creation strategy

The three btree partial indexes are:

- `idx_resumes_processing_lease` on `(processing_lease_expires_at, id)` for
  PROCESSING Resume rows with a non-NULL lease;
- `idx_jobs_processing_lease` with the same shape and `parsing_status` predicate;
- `idx_matches_processing_lease` with the same shape and `status` predicate.

The timestamp supports future expired-lease range scans and `id` deterministic
pagination. The non-NULL predicate excludes legacy Phase 1 rows.

## 9. Idempotency and mismatch detection

Correct existing columns are accepted. Wrong type, default, or nullability raises
an exception; `ADD COLUMN IF NOT EXISTS` is not used. Index validation covers
object kind, table, access method, keys, uniqueness, predicate, and validity.
Correct indexes are accepted on rerun; occupied names and invalid indexes stop.
CI applies both scripts twice and verifies rows and pre-existing indexes unchanged.

## 10. Preflight and stop conditions

Before migration DDL, independently verify the approved change ticket, repository
SHA, exact database identity, PostgreSQL 18, ten public tables, 42 skills, target
table signatures, disk, backup/replication health, and absence of long transactions
or conflicting DDL. Stop on identity mismatch, unexpected metadata, invalid index,
timeout, elevated errors, replication lag, or missing rollback authority. Never
infer data corruption from a migration refusal.

## 11. PostgreSQL CI test evidence

The workflow uses only its GitHub-hosted service container and loopback port 55432. Gates cover
GitHub, runner, repository, container, host, port, database, PG major, table count, and taxonomy
count. Path A bootstraps the immutable pre-fencing fixture copied from canonical commit
`a38ec82d81e915581766d0ae6c38c717696a8724`, seeds legacy PROCESSING rows, applies both migrations,
checks legacy CAS, negative paths, and rerun idempotency. Path B bootstraps current canonical
`schema.sql` from scratch, seeds 42 skills, verifies exactly 10 tables, all 15 columns, all three
partial indexes, and the absence of ownership CHECKs. Backend regression runs on Path B.

A dedicated catalog snapshot compares Path A and Path B for public tables, columns, PostgreSQL
types, nullability, defaults, primary keys, foreign keys, unique constraints, CHECK constraints,
and index name/method/keys/predicate/validity. Any material difference is printed by `diff` and
fails CI. PR checks are the evidence of record; local PostgreSQL is not a substitute.

## 12. Proposed rollout procedure

This is a proposal, not authorization. Canonical `schema.sql`, PDM, and data dictionary are now
synchronized with the approved shape. Re-run CI and rehearse on a disposable PostgreSQL 18 clone.
Drain conflicting DDL, observe locks, verify backup,
then apply expansion in an approved window. Verify metadata and legacy traffic.
Apply indexes separately, verify `indisvalid`, predicates, latency, errors, and
replication lag, then hold before any Phase 2 deployment.

## 13. Rollback procedure

Before Phase 2 depends on these fields, stop and leave compatible nullable columns
in place. A failed expansion rolls back atomically. A failed concurrent build may
leave an invalid index; capture evidence and use separately approved concurrent
drop/rebuild SQL. Dropping columns is destructive and lock-heavy, requiring a new
reviewed migration and proof no deployed code depends on them. Restore is last
resort.

## 14. Monitoring

Monitor lock waits, duration, application/worker errors, DB CPU/I/O, connections,
replication lag, deadlocks, and invalid indexes. Sample legacy INSERT/claim/
terminal paths and confirm epoch stays zero with NULL ownership. Never log owner
or activation token values in later phases.

## 15. Risk register

| Risk | Mitigation |
|---|---|
| `ACCESS EXCLUSIVE` wait | short timeout, quiet window, stop on timeout |
| Incorrect rerun | catalog validation and fail-closed checks |
| Interrupted index build | validate `indisvalid`; no automatic drop |
| Old workers rejected | no ownership checks; defaults/NULL preserve writes |
| False deployment claim | explicit artifact/environment reporting |
| Canonical documentation drift | mandatory synchronization before rollout |
| Premature reclaim | no runtime or feature-flag changes in Phase 1 |

## 16. Deployment authorization boundary

These files are **NOT AUTHORIZED FOR LOCAL, QA, STAGING, OR PRODUCTION
EXECUTION**. DDL is authorized only in the disposable GitHub Actions service. This
task does not authorize local ports 5432/55432, the local QA directory,
`webcv_ungvien`, or any canonical/external database.

For a new empty database, bootstrap with current canonical `schema.sql` followed by the taxonomy
seed. Never run `schema.sql` as an upgrade procedure against an existing populated database.
Existing databases use the reviewed expand and index migrations only after explicit deployment
approval and environment preflight; this document grants no such approval.

## 17. Phase 2 prerequisites

Phase 2 requires Technical Lead approval, synchronized canonical artifacts,
successful PostgreSQL 18 migration CI, approved Phase 1 deployment, old-worker
inventory/drain strategy, ORM mappings, fenced claim/terminal CAS design, token
secrecy review, real concurrency tests, metrics/alerts, and disabled-by-default
reclaim. Phase 2–5 are not implemented here.

Accordingly, ORM mappings and runtime CAS remain Phase 1 legacy-compatible and do not map/use the
five columns. Heartbeat and reclaim must remain disabled; old PROCESSING rows are not safely
reclaimable merely because canonical source artifacts now contain the metadata.
