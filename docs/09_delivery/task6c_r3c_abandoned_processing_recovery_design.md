# Task 6C-R3C — Abandoned PROCESSING Recovery

**Status:** `NEEDS SCHEMA APPROVAL`

**Baseline:** `dev` at `480162588cfb00b1f20ae41f283731a4ab18f5da`

**Delivered scope:** opt-in, read-only observation only. Automatic reclaim is intentionally not implemented.

## 1. Baseline and prior work

R3A established that killing a Celery child after claim can strand a row in `PROCESSING`, and
that a timeout alone is not ownership proof. R3B isolated PDF/DOCX parsing in a bounded child
process, while the parent retains terminal CAS authority. The current Task 6B recovery loops
correctly redispatch only old `PENDING` work and must remain unchanged.

The canonical schema has ten tables. Resume and Job carry `revision`; Match carries `generation`
plus linked resource revisions. None of the three resources stores a processing attempt identity,
owner, lease, or heartbeat.

## 2. Failure mode

The current claim commits `PENDING -> PROCESSING` before parser, embedding, or matching work.
If the owning worker process dies after that commit, no terminal write occurs. Queue redelivery
cannot claim the row because the exclusive claim predicate requires `PENDING`; the existing
recovery loops also deliberately select only `PENDING`.

An old `updated_at` does not distinguish worker death from a slow BGE-M3 embedding, a live worker
during a network partition, a stalled broker, or a worker unable to heartbeat. Treating age as
proof of abandonment can create two writers for one logical computation.

## 3. Resource analysis

- **Resume:** revision fences input changes, but two attempts for the same revision are
  indistinguishable. Success also replaces a multi-table parsed aggregate transactionally.
- **Job:** has the same attempt ambiguity; success replaces Job fields and `job_skills` in one
  transaction.
- **Match:** generation and linked revisions fence invalidation, but not two attempts within the
  same generation. Terminal writes additionally lock Job then Resume and verify linked revisions
  and soft-delete state.

The existing CAS is sufficient for duplicate delivery before claim and for stale
revision/generation. It is not sufficient to revoke a worker that already claimed the current
revision/generation.

## 4. Options considered

### A. Stale detection and safe observation

Select bounded metadata for rows whose status is `PROCESSING` and `updated_at` is older than a
configured threshold. Do not claim, fail, reset, dispatch, or modify timestamps. Log aggregate
counts only; do not log CV/JD text, embeddings, errors, credentials, or resource IDs.

This is safe and useful for alerting and operator diagnosis, but it is not recovery and must not
label a candidate as abandoned. This option is implemented as an opt-in service and is not wired
into application startup.

### B. Persistent attempt fencing and controlled recovery

Give every claimed attempt a persistent epoch and unguessable owner token, with heartbeat and
lease timestamps. Every context read, heartbeat, success write, failure write, and aggregate
replacement must include that ownership fence. Recovery may replace ownership only through one
atomic CAS after lease expiry. The old worker then cannot write even if it becomes live again.

This is the only option that supports automatic reclaim safely. It requires canonical schema,
ORM, queue payload, worker CAS, migration, and coordinated rollout changes, so it is blocked on
schema approval.

## 5. State transitions

Allowed today:

```text
PENDING --exclusive revision/generation claim--> PROCESSING
PROCESSING --current revision/generation terminal CAS--> PARSED|COMPLETED|FAILED
```

Allowed after fencing approval:

```text
PENDING --claim and create attempt N--> PROCESSING(attempt N)
PROCESSING(attempt N) --fenced heartbeat--> PROCESSING(attempt N)
PROCESSING(expired attempt N) --atomic ownership CAS--> PROCESSING(attempt N+1)
PROCESSING(attempt N+1) --fenced terminal CAS--> terminal
```

Forbidden:

- age-only `PROCESSING -> FAILED`;
- blind `PROCESSING -> PENDING`;
- revision/generation increment merely to retry an attempt;
- terminal write without current attempt ownership;
- reclaim that ignores soft-delete or linked Match revisions;
- queue publication while holding a database transaction open.

## 6. Attempt ownership and fencing model

Proposed columns on each of `resumes`, `job_descriptions`, and `match_results`:

| Column | Type | Rule |
| --- | --- | --- |
| `processing_attempt_epoch` | `BIGINT NOT NULL DEFAULT 0` | `CHECK >= 0`; increment on every initial claim or reclaim |
| `processing_owner_token` | `UUID NULL` | application-generated random token for the current attempt |
| `processing_heartbeat_at` | `TIMESTAMPTZ NULL` | last successful fenced heartbeat |
| `processing_lease_expires_at` | `TIMESTAMPTZ NULL` | bounded validity horizon for ownership |

Add a partial index per resource on `(processing_lease_expires_at, id)` where the parse/match
status is `PROCESSING`. Add constraints requiring owner/heartbeat/lease while `PROCESSING` and
requiring them to be null in `PENDING` or terminal states. Epoch remains monotonic after terminal
completion for audit and future attempts.

The queue payload for a reclaimed attempt must include resource identity, existing
revision/generation snapshots, `processing_attempt_epoch`, and `processing_owner_token`. Tokens
must never be logged. A normal PENDING worker may generate its owner token immediately before the
claim; the claim returns the new epoch.

## 7. CAS predicates

Initial Resume claim (Job is analogous):

```sql
UPDATE resumes
SET parsing_status = 'PROCESSING',
    processing_attempt_epoch = processing_attempt_epoch + 1,
    processing_owner_token = :owner,
    processing_heartbeat_at = now(),
    processing_lease_expires_at = now() + :lease,
    updated_at = now()
WHERE id = :id AND revision = :revision
  AND parsing_status = 'PENDING' AND is_deleted = false
RETURNING processing_attempt_epoch;
```

Heartbeat and every terminal Resume/Job update add:

```sql
AND revision = :revision
AND parsing_status = 'PROCESSING'
AND processing_attempt_epoch = :epoch
AND processing_owner_token = :owner
AND is_deleted = false
```

Match terminal predicates additionally retain generation, algorithm, snapshot, linked-resource
revision, readiness/soft-delete checks, and the established Job-then-Resume lock order.

Reclaim is a single update that does not return the row to PENDING:

```sql
UPDATE <resource>
SET processing_attempt_epoch = processing_attempt_epoch + 1,
    processing_owner_token = :new_owner,
    processing_heartbeat_at = now(),
    processing_lease_expires_at = now() + :lease,
    updated_at = now()
WHERE id = :id
  AND status = 'PROCESSING'
  AND processing_attempt_epoch = :observed_epoch
  AND processing_owner_token = :observed_owner
  AND processing_lease_expires_at <= now()
  AND <revision/generation and soft-delete/linked-resource guards>
RETURNING processing_attempt_epoch;
```

`rowcount=0` always means stale/discard. Publication happens only after commit. If publication
fails, the new attempt remains fenced and becomes eligible for a later reclaim after its lease.

## 8. Lease and heartbeat semantics

Lease expiry is permission to compete for ownership, not proof that a worker died. Safety comes
from changing the persistent fence before new work begins. Heartbeat must be a small, independent
database update containing the full ownership predicate. It must not extend the lease after the
worker has lost ownership.

Thresholds must be task-specific and longer than measured heartbeat jitter. Parser, BGE-M3, and
Match may require different values. Redis health is not ownership authority; the database fence
is. Clock decisions use PostgreSQL `now()`, not worker wall clocks.

## 9. Race matrix

| Race | Required result |
| --- | --- |
| Slow live worker vs observer | observer reports only; no mutation |
| Old heartbeat vs reclaim | exactly one CAS wins; losing heartbeat cannot renew |
| Old success/failure vs reclaim | exactly one fenced CAS wins |
| Two reclaim sweepers | only one increments observed epoch |
| Duplicate queue delivery | only current owner/epoch can proceed or commit |
| Revision/generation changes | old attempt and reclaim both discard |
| Resume/Job soft-delete | no reclaim or terminal write |
| Match linked revision/delete change | no reclaim or terminal write |
| Broker failure after reclaim | no rollback to old owner; later lease can be reclaimed |
| Recovery restart | persistent epoch/token/lease preserve ownership truth |

## 10. Migration and compatibility plan

Forward migration, subject to explicit approval:

1. Add nullable ownership timestamps/token plus epoch default, checks initially `NOT VALID` if
   necessary, and partial indexes concurrently where deployment policy permits.
2. Deploy fence-aware code with reclaim disabled. New claims and all terminal paths must use the
   fence; existing PENDING recovery behavior remains unchanged.
3. Drain and stop every old worker binary. This is mandatory: an old terminal UPDATE does not
   include the new fence and could otherwise overwrite a reclaimed attempt.
4. Verify no old worker processes remain and route all queued messages to fence-aware workers.
5. Classify pre-existing PROCESSING rows operationally; do not silently backfill a live owner.
6. Validate constraints, enable heartbeat, observe lease behavior, then separately enable reclaim.

There is no safe mixed-version period with reclaim enabled while old workers can execute terminal
SQL. Deployment must therefore use a drain barrier. The ten-table invariant is preserved because
the proposal adds columns and indexes, not an eleventh table.

Rollback must disable reclaim first, drain fence-aware workers, and deploy code that no longer
depends on the columns before a later schema down-migration. Never drop fencing columns while a
fenced attempt can still run.

## 11. Implemented observability

`processing_observation.py` reads only IDs, revision/generation metadata, timestamps, and delete
flags. It performs three bounded SELECTs, globally sorts/truncates to the configured batch size,
closes the session before logging, and logs only per-type counts. Defaults:

- disabled;
- warning threshold 1,800 seconds;
- batch size 100.

It is deliberately not registered in FastAPI lifespan or Celery. Enabling the setting alone does
not start a production sweeper; an operator-approved caller is still required.

## 12. Test matrix

The observability implementation tests disabled-by-default behavior, SELECT-only statements,
bounded metadata, session closure, threshold propagation, batch configuration, and logs without
IDs/content. It never mutates state.

Full fencing approval requires real PostgreSQL concurrency tests for: death immediately after
claim; death during parser/embedding; slow live worker; old worker returning after reclaim;
duplicate delivery; concurrent terminal/reclaim; two reclaimers; stale Resume/Job revision;
Match generation/linked revision changes; soft-delete; DB/Redis disconnect; recovery restart;
aggregate atomicity; and preservation of valid terminal data. Unit mocks are not sufficient
evidence for those races.

## 13. Deployment risks

- Mixed old/new workers can bypass the fence.
- Too-short leases cause availability churn even though fencing preserves correctness.
- Heartbeat database load and partial-index selectivity require measurement.
- Broker outage after ownership transfer causes delayed work until the next safe reclaim.
- Tokens in logs or metrics would create unnecessary operational exposure.
- Pre-existing PROCESSING rows need an explicit operator policy.

## 14. Remaining security limitations

Observation cannot prove abandonment, recover work, or cap BGE-M3 resources. It provides no
distributed liveness oracle and does not address host compromise. R3D hard limits remain blocked
until persistent fencing, isolated Redis/PostgreSQL runtime QA, and deployment policy are approved.

## 15. Decision

**NEEDS SCHEMA APPROVAL.** The current schema cannot safely auto-reclaim `PROCESSING`. The
read-only observer is safe to review independently, but R3C full recovery is not complete and no
automatic status transition or dispatch has been added.
