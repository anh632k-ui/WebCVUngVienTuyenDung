# Task 6C-R3C Phase 4A — Heartbeat and ownership-constraint preparation

**Status:** source preparation only; live activation is blocked.

> Phase 4A implements inactive heartbeat primitives and prepares constraint verification only.
> No live drain, heartbeat activation, schema change, or reclaim is authorized.

## 1. Scope and audited baseline

The canonical schema has ten tables and five attempt-fencing columns on `resumes`,
`job_descriptions`, and `match_results`. Phase 2 supplies opt-in initial claim, reclaimed-attempt
activation, terminal fences, database-clock leases, and ownership clearing. Phase 3 supplies
offline inventory evidence. The feature flag remains off by default. There is no heartbeat
scheduler, automatic reclaim, ownership CHECK, or live-drain evidence.

The worker runtime is lazy and PID-owned. One `asyncio.Runner`, async engine/session factory, and
execution lock belong to a Celery child. A worker task holds that execution lock while its async
work runs. Therefore a heartbeat scheduled on that runner would be starved by blocking parsing or
embedding work and is not a valid runtime design.

Current lifecycle findings:

- Initial fenced claims atomically change `PENDING` to `PROCESSING`, increment the epoch, create
  owner and activation capabilities, and write heartbeat/lease values.
- Reclaimed-attempt activation accepts an existing owner/epoch and a null activation token. It
  creates only the activation token and refreshes heartbeat/lease. Reclaim itself is absent.
- Success/failure CAS operations require an activated, unexpired guard and clear all four nullable
  ownership fields when they win. Epoch is retained.
- Revision/generation invalidation clears Match ownership. Resume/Job soft-delete currently marks
  the resource deleted without clearing its own ownership; all new heartbeat predicates therefore
  require `is_deleted = false`.
- Match terminal writes identify the linked rows, lock Job then Resume, revalidate linked snapshots,
  and finally update Match. Job mutations use the same Job-before-Resume ordering. No external work
  is performed while those transactions are open.

## 2. Inactive heartbeat contract

`renew_processing_attempt_lease(session, guard, lease_seconds=...)` accepts only the immutable
internal `ProcessingAttemptGuard`; public HTTP values are never accepted as ownership capabilities.
It returns exactly `RENEWED`, `LOST_OWNERSHIP`, or `PERSISTENCE_ERROR`.

The duration must be an integer other than `bool` in `[60, 86400]`. Invalid input raises instead of
being clamped. PostgreSQL `clock_timestamp()` is the only time authority. The operation obtains the
necessary row locks in one short transaction, then samples the database clock once in a one-row CTE.
That post-lock sample is used for both the current-lease predicate and both assigned values:

```text
processing_heartbeat_at = heartbeat_at
processing_lease_expires_at = GREATEST(existing_lease, heartbeat_at + duration)
```

`GREATEST` prevents a short requested duration from reducing an already longer lease. A lease that
expired before the post-lock sample cannot be resurrected.

Resume and Job require exact ID, revision, `PROCESSING`, not deleted, epoch, owner, activation, and
an unexpired lease. Match additionally requires generation, both recorded revisions, algorithm
version, unchanged linked IDs, and current/non-deleted linked Resume and Job rows. Match locks Job,
then Resume, then performs the Match CAS. This agrees with Match terminal and Job mutation ordering;
it does not do an authorization SELECT followed by an unguarded UPDATE.

Only heartbeat and lease timestamps are assigned. Status, snapshots, capabilities, errors,
aggregates, embeddings, JobSkills, and scores are untouched. A null activation guard is rejected;
the primitive cannot claim, activate, revoke, terminal-write, reclaim, or dispatch work.

Zero returned rows is final `LOST_OWNERSHIP`: do not retry blindly, mint tokens, reset state, or call
reclaim. A database exception is `PERSISTENCE_ERROR`, which is deliberately outcome-ambiguous. It
must never be interpreted as success. A future caller must stop ownership-dependent work when
possible and re-read durable state with a fresh database transaction before deciding what to do.
Tokens must never enter logs or metrics. The terminal fence remains mandatory even after a reported
successful heartbeat.

## 3. PostgreSQL concurrency evidence

The dedicated GitHub workflow uses a disposable pgvector/PostgreSQL 18 service and an exact guarded
database identity. The test modules cannot opt into a developer database, `webcv_ungvien`, port
55432, or a non-GitHub runner.

The test matrix covers valid renewal and non-mutation for all three resources; wrong snapshot,
epoch, owner, and activation; missing activation; expiry; revocation; terminal state; Resume/Job
soft-delete; Match generation/revision/algorithm and linked deletion/revision guards; two concurrent
heartbeats; terminal/invalidation serialization; post-lock lease expiry; linked mutation after a
wait; and connection termination while blocked. Lock races synchronize through
`pg_blocking_pids()`, not fixed sleeps. Existing worker fencing tests remain part of Backend CI.

No local PostgreSQL run is an accepted substitute for this evidence. A skipped Phase 4A PostgreSQL
module is not a pass.

## 4. Ownership state matrix

The same nullable-column logic applies to all three tables. `state` below means `parsing_status` for
Resume/Job and `status` for Match.

| State | owner | activation | heartbeat | lease | Phase 4A compatibility |
|---|---:|---:|---:|---:|---|
| PENDING, legacy/flag OFF | null | null | null | null | valid |
| Legacy PROCESSING | null | null | null | null | valid until fenced-only gate |
| Activated fenced PROCESSING | set | set | set | set | valid |
| Reclaimed, unactivated PROCESSING | set | null | null | set | valid future state |
| PARSED/COMPLETED | null | null | null | null | valid; epoch may remain > 0 |
| FAILED | null | null | null | null | valid; epoch may remain > 0 |
| Soft-deleted PROCESSING | either complete active tuple or all null | matching | matching | matching | compatible now; heartbeat rejects it |
| Revoked ownership | null | null | null | null | valid; epoch is retained |
| Partial metadata | mixed | mixed | mixed | mixed | invalid |

Metadata consistency and mandatory fenced ownership are separate policies. Combining them would
make rollback to flag OFF impossible and would break current legacy writers.

### 4.1 Compatibility-safe candidate

The following predicate is proposed for isolated verification only. It is **NOT AUTHORIZED** for a
canonical schema or migration in Phase 4A:

```sql
CHECK (
  (
    processing_owner_token IS NULL
    AND processing_activation_token IS NULL
    AND processing_heartbeat_at IS NULL
    AND processing_lease_expires_at IS NULL
  )
  OR
  (
    processing_owner_token IS NOT NULL
    AND processing_lease_expires_at IS NOT NULL
    AND (
      (processing_activation_token IS NULL AND processing_heartbeat_at IS NULL)
      OR
      (processing_activation_token IS NOT NULL AND processing_heartbeat_at IS NOT NULL)
    )
  )
)
```

It permits flag-OFF/legacy PROCESSING, future unactivated reclaimed attempts, activated attempts,
soft-deleted rows awaiting disposition, and terminal rows with a preserved epoch. It rejects owner
without lease, activation without owner/heartbeat, heartbeat without activation, lease-only, and
other partial tuples. Before any future application, Phase 4B must inventory actual data, explain
every violation, review writer compatibility, name each constraint per table, and obtain approval.

### 4.2 Fenced-only candidate — irreversible rollout gate

The semantic rules below are **unsafe now**:

```sql
CHECK (processing_owner_token IS NULL OR parsing_status = 'PROCESSING')
CHECK (parsing_status <> 'PROCESSING' OR processing_owner_token IS NOT NULL)
-- Match uses status in place of parsing_status.
```

The first enforces cleared terminal ownership. It is not compatibility-safe while an old terminal
writer could encounter a fenced row and change only the status. The second requires every
PROCESSING row to be fenced and directly breaks flag-OFF/legacy claims. They must share the drain
gate even though they enforce opposite directions of the eventual equivalence.

It may be considered only after the approved Phase 1 migration, exact readiness, live old-worker
inventory and drain, prevention of legacy binary restart, queue routing verification, explicit
legacy PROCESSING disposition, and operator authorization to enter fenced-only mode. It is not a
kill switch that can coexist with an ordinary flag-OFF rollback.

`NOT VALID` is not a compatibility bypass: PostgreSQL still enforces it for new inserts and updates,
so an old writer can fail immediately. `VALIDATE CONSTRAINT` also fails while a legacy unowned
PROCESSING row remains. Real PostgreSQL tests demonstrate both behaviors.

## 5. Future heartbeat execution model (design only)

Activation would begin only after an activated guard has committed and immediately before the first
potentially long parser/embedding stage. The executing task owns the controller; it must stop and
join it before entering any terminal transaction. No module-import task, API-startup scheduler,
Celery beat entry, or autonomous daemon is allowed.

The recommended controller is one bounded heartbeat thread per active worker task. That thread owns
its own `asyncio.Runner`, creates its own `AsyncEngine` and session factory inside that runner, opens
a fresh `AsyncSession` for each short renewal, disposes the engine on the same loop, closes the
runner, and then exits. No session or engine crosses a thread/event-loop/process boundary. This
cost is intentional and must be load-tested before approval; a later per-child controller is
acceptable only if it retains the same ownership and bounded-shutdown guarantees.

The parser/BGE-M3 work remains on the task execution path while the independent heartbeat thread can
run even if that runner is blocked. Python/GIL and native-library behavior still require measured
canary evidence; Phase 4A does not choose an interval or assert that 1800 seconds is sufficient.

Each iteration opens, commits/rolls back, and closes its session. It never holds a transaction while
waiting for the next interval or while parser/embedding runs. `LOST_OWNERSHIP` or
`PERSISTENCE_ERROR` sets a one-way stop signal. The task must not treat itself as authorized to
terminal-write; cooperative work should stop, and the final database fence remains authoritative.
For an ambiguous persistence error, durable state must be reread before any further action.

Normal completion signals stop, waits for the in-flight short renewal to finish within a reviewed
bound, joins the thread, and only then attempts terminal CAS. Child shutdown performs the same
bounded sequence before closing the primary runtime. An abrupt process kill stops both execution
and heartbeat; the lease eventually expires, but Phase 4A contains no reclaimer. A controller that
cannot be joined must fail closed and must not allow a terminal ownership assumption.

## 6. Gates, readiness, and rollback

Source-level PASS requires review of this matrix, unit/static checks, real PostgreSQL heartbeat and
constraint tests with no skips, existing Phase 2/Phase 3 regressions, and a reviewed rollback
runbook. It authorizes only merging dormant source after normal review.

Live deployment remains blocked until all of the following are independently evidenced:

1. approved Phase 1 migration applied and exact schema readiness passed;
2. operator-reviewed live old-worker inventory and complete legacy-writer drain;
3. no legacy binary can restart and queue routes are fence-aware;
4. every unresolved PROCESSING row is classified and disposition is approved;
5. heartbeat interval, lease duration, jitter, capacity, monitoring, and alert policy are approved;
6. rollback rehearsal and operator approval are recorded.

Activation rollback would disable heartbeat scheduling first, keep reclaim disabled, and drain or
classify active fenced attempts before reverting the fencing flag. Metadata columns and any already
approved compatibility-safe constraint would remain; a fenced-only constraint cannot remain if
flag-OFF legacy writes must resume. Therefore that later constraint needs a separate irreversible
gate and rollback decision. Never reset, delete, or adopt legacy PROCESSING rows automatically.

Phase 4A does not apply DDL, modify readiness, register runtime work, enable the feature flag, select
production timing, activate reclaim, drain workers, or advance to Phase 4B/5/R3D.
