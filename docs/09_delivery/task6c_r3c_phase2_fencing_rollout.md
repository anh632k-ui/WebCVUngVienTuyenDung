# Task 6C-R3C Phase 2 — persistent attempt fencing rollout

## Runtime contract

Resume parsing, Job parsing, and Match computation have a disabled-by-default
`PROCESSING_FENCING_ENABLED` path. A successful initial CAS changes PENDING to
PROCESSING, increments the independent attempt epoch, and persists random owner
and activation UUIDs plus database-clock heartbeat and lease timestamps. Worker
context reads and every terminal CAS use the complete persisted capability.
Terminal success or failure clears both tokens and both timestamps but preserves
the epoch and business revision/generation.

Reclaimed-attempt activation is a separate internal CAS. It accepts only a
persisted PROCESSING attempt whose activation is NULL, ownership and snapshots
match, and lease is still valid. It does not reclaim, scan, publish, or register a
new queue route. No heartbeat renewal is implemented in Phase 2.

Resume aggregate replacement and its PARSED CAS share one transaction after an
ownership lock/revalidation. JobSkills use the same pattern. Match terminal work
keeps Job then Resume lock ordering and revalidates linked snapshots and deleted
state. Success also requires both resources to remain ready. A controlled invalid-
context failure may write FAILED while snapshots remain current even if readiness
became false; changed or deleted resources still cause DISCARD.

Flag-OFF compatibility writers require all four ownership fields to be NULL, but do
not require epoch 0. A completed fenced attempt therefore keeps its monotonic epoch
and a later business revision/generation can still be processed after rollback.
Every locked business invalidation that moves a Job or Match to PENDING clears the
four ownership fields atomically while preserving the epoch. This revokes stale
fenced workers before an OFF worker can claim the new snapshot. No terminal writer
may bypass a non-NULL owner or activation capability.

`clock_timestamp()` is intentional for lease decisions. PostgreSQL `now()` is the
transaction start time and can remain stale during a row-lock wait; the wall clock
must be evaluated when each CAS statement actually runs.

## Mandatory deployment barrier

The application and lazy worker runtime perform a read-only catalog check for the
15 Phase 1 columns (including type/nullability/default presence) and the three
exact partial lease indexes. Each index must be a ready, valid, non-unique btree in
the current schema, with exactly two key attributes and no INCLUDE columns, canonical
key order, and the exact canonical predicate. Missing, broader, plain, invalid, or
otherwise unverifiable metadata aborts startup/task
execution with a credential-free error. The check performs no DDL and has no
fallback. Feature flag OFF is not schema compatibility because ORM SELECTs map the
new columns.

Deployment remains blocked until all of the following are separately approved:

1. Apply the reviewed Phase 1 migrations to the target database and verify the
   catalog before deploying this backend.
2. Drain all older API and worker binaries; their SQL does not contain the legacy
   epoch/token predicates and mixed-version safety is not claimed.
3. Deploy this version with fencing still OFF and validate readiness and legacy
   traffic.
4. Approve a workload-specific lease duration. Phase 2 has no heartbeat, so do not
   enable fencing for work that can outlive the lease.
5. Enable only through a controlled configuration change with rollback and
   monitoring approval. To roll this version back to flag OFF, stop publishing,
   drain active fenced attempts (or reclaim expired attempts while still ON), deploy
   OFF consistently, then resume publishing. Historical nonzero epochs are supported
   and must never be reset. Never roll back to an older binary without ownership
   predicates.

Do not run canonical `schema.sql` as an upgrade, auto-apply migrations, enable an
automatic reclaimer, or reset PROCESSING rows. Existing PENDING recovery semantics
remain unchanged. Phase 2 does not authorize local, staging, or production DDL.
