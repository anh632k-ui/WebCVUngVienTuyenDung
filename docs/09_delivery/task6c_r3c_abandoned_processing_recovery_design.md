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

## 6. Attempt ownership, activation, and entry paths

Proposed columns on each of `resumes`, `job_descriptions`, and `match_results`:

| Column | Type | Rule |
| --- | --- | --- |
| `processing_attempt_epoch` | `BIGINT NOT NULL DEFAULT 0` | monotonic; increment on initial claim and reclaim |
| `processing_owner_token` | `UUID NULL` | unguessable identity of the persisted attempt; never logged |
| `processing_activation_token` | `UUID NULL` | grants execution to exactly one queue delivery |
| `processing_heartbeat_at` | `TIMESTAMPTZ NULL` | last successful fenced heartbeat after activation |
| `processing_lease_expires_at` | `TIMESTAMPTZ NULL` | database-clock ownership horizon |

The owner token grants ownership to an *attempt*. It is not sufficient to grant execution to a
delivery because two copies of the same message carry the same owner and epoch. The activation
token is a second CAS fence that grants start permission to exactly one delivery.

### 6.1 Initial dispatch path

The existing immutable task identifies a PENDING resource snapshot. A worker generates fresh
owner and activation tokens, then changes `PENDING -> PROCESSING` in one claim CAS. That update
increments the epoch and persists both tokens. Only `rowcount=1` may load content, parse, embed, or
score. Duplicate initial messages lose the PENDING claim and discard without expensive work.

### 6.2 Reclaimed dispatch path

A reclaimer atomically changes `PROCESSING(epoch N) -> PROCESSING(epoch N+1)`, writes a fresh owner
token and lease, and leaves activation/heartbeat null. It commits before publishing a reclaimed
payload containing resource identity, revision/generation snapshots, epoch, owner token, and a
fresh delivery activation token. The receiver uses a dedicated activation CAS; it **must not call
the PENDING-only claim**. Before work starts the activation CAS verifies resource identity,
revision/generation, owner, epoch, unexpired lease, soft-delete, and Match linked snapshots.

Two copies of the same reclaimed payload may both read valid ownership, but only one can change
`processing_activation_token IS NULL` to its delivery token. The loser discards. If the activated
worker dies, redelivery cannot reactivate the same attempt; recovery waits for lease expiry and
creates epoch N+1. If publish succeeds but ACK is lost, duplicate redelivery also loses activation.
If publication fails or its outcome is unknown, the persisted unactivated attempt remains fenced;
safe retry may republish the same payload, and at most one delivery activates it. A later reclaim
is allowed only after lease expiry.

Terminal and aggregate writes require epoch, owner, and activation tokens. Therefore an old worker
revoked by reclaim cannot write even if it resumes. Token values are operational secrets and must
not appear in logs, metrics labels, exception messages, or API responses.

## 7. Per-resource CAS specification

All time comparisons use PostgreSQL `now()`. `rowcount=0` means stale/revoked/deleted/duplicate and
must cause rollback plus discard. A database disconnect gives no commit knowledge: reconnect and
read current fenced state; never assume success or repeat an aggregate write without revalidation.

### 7.1 Resume

Initial claim and activation are one transaction:

```sql
UPDATE resumes
SET parsing_status = 'PROCESSING',
    processing_attempt_epoch = processing_attempt_epoch + 1,
    processing_owner_token = :owner,
    processing_activation_token = :activation,
    processing_heartbeat_at = now(),
    processing_lease_expires_at = now() + :lease,
    error_message = NULL,
    updated_at = now()
WHERE id = :resume_id AND revision = :revision
  AND parsing_status = 'PENDING' AND is_deleted = false
RETURNING processing_attempt_epoch;
```

Heartbeat requires `id`, `revision`, `parsing_status='PROCESSING'`, current epoch, owner and
activation tokens, `is_deleted=false`, and `processing_lease_expires_at > now()`. It only updates
heartbeat and lease timestamps. Reclaim keeps the same revision and uses:

```sql
UPDATE resumes
SET processing_attempt_epoch = processing_attempt_epoch + 1,
    processing_owner_token = :new_owner,
    processing_activation_token = NULL,
    processing_heartbeat_at = NULL,
    processing_lease_expires_at = now() + :lease,
    updated_at = now()
WHERE id = :resume_id AND revision = :revision
  AND parsing_status = 'PROCESSING' AND is_deleted = false
  AND processing_attempt_epoch = :old_epoch
  AND processing_owner_token = :old_owner
  AND processing_lease_expires_at <= now()
RETURNING processing_attempt_epoch;
```

The reclaimed receiver activates with an UPDATE requiring the returned epoch/new owner,
`processing_activation_token IS NULL`, an unexpired lease, the same revision and active resource.
It sets activation token, heartbeat, and refreshed lease. Duplicate activation gets rowcount zero.
After activation, heartbeat and both terminal updates repeat this concrete ownership predicate:

```sql
WHERE id = :resume_id AND revision = :revision
  AND parsing_status = 'PROCESSING' AND is_deleted = false
  AND processing_attempt_epoch = :epoch
  AND processing_owner_token = :owner
  AND processing_activation_token = :activation
  AND processing_lease_expires_at > now()
```

PARSED is one transaction: first lock/re-read the Resume using the complete fence, then replace
CandidateProfile/ResumeSkill/ResumeExperience/ResumeEducation, then terminal-update the Resume.
The terminal UPDATE repeats the complete fence and sets parsed fields plus:

```sql
parsing_status = 'PARSED', processing_owner_token = NULL,
processing_activation_token = NULL, processing_heartbeat_at = NULL,
processing_lease_expires_at = NULL
```

FAILED uses the same predicate and clears all four ownership fields while setting the bounded
error. Any stale revision, soft-delete, expired/revoked owner, aggregate error, or rowcount zero
rolls back the whole transaction. No old attempt may delete or replace aggregate rows.

### 7.2 Job

Job initial claim is explicit and uses the Job error column:

```sql
UPDATE job_descriptions
SET parsing_status='PROCESSING',
    processing_attempt_epoch=processing_attempt_epoch+1,
    processing_owner_token=:owner, processing_activation_token=:activation,
    processing_heartbeat_at=now(), processing_lease_expires_at=now()+:lease,
    parsing_error_message=NULL, updated_at=now()
WHERE id=:job_id AND revision=:revision
  AND parsing_status='PENDING' AND is_deleted=false
RETURNING processing_attempt_epoch;
```

Job reclaim updates `job_descriptions` where `id`, revision,
`parsing_status='PROCESSING'`, `is_deleted=false`, old epoch/owner and expired lease all match. It
increments epoch, writes the new owner/lease, and clears activation/heartbeat. Reclaimed activation
requires `processing_activation_token IS NULL`; after activation, heartbeat and both terminal
writes require:

```sql
WHERE id=:job_id AND revision=:revision
  AND parsing_status='PROCESSING' AND is_deleted=false
  AND processing_attempt_epoch=:epoch
  AND processing_owner_token=:owner
  AND processing_activation_token=:activation
  AND processing_lease_expires_at > now()
```

PARSED locks/revalidates the fenced Job, replaces `job_skills`, writes parsed fields and embedding,
then changes `parsing_status='PARSED'` and clears owner/activation/heartbeat/lease in the same
transaction. FAILED uses the same fence, sets `parsing_error_message`, and clears ownership fields.
Constraint, flush, or terminal-CAS failure rolls back both Job fields and JobSkills. No failure and
success terminal can both win because both require the same active fence and clear it on success.

### 7.3 Match

Initial Match claim retains every current guard:

```sql
UPDATE match_results m
SET status = 'PROCESSING',
    processing_attempt_epoch = processing_attempt_epoch + 1,
    processing_owner_token = :owner,
    processing_activation_token = :activation,
    processing_heartbeat_at = now(),
    processing_lease_expires_at = now() + :lease,
    error_message = NULL,
    updated_at = now()
WHERE m.id = :match_id AND m.status = 'PENDING'
  AND m.generation = :generation
  AND m.resume_revision = :resume_revision
  AND m.job_revision = :job_revision
  AND m.algorithm_version = :algorithm_version
  AND EXISTS (SELECT 1 FROM resumes r WHERE r.id=m.resume_id
              AND r.revision=:resume_revision AND r.is_deleted=false)
  AND EXISTS (SELECT 1 FROM job_descriptions j WHERE j.id=m.job_id
              AND j.revision=:job_revision AND j.is_deleted=false)
RETURNING processing_attempt_epoch;
```

Heartbeat, activation, reclaim, COMPLETED and FAILED retain `generation`, both snapshot revisions,
algorithm version, epoch/owner/activation, and linked-resource guards. Reclaim additionally needs
the observed owner and expired lease, increments only attempt epoch, and clears activation and
heartbeat. It never increments generation.

The post-activation Match ownership predicate is:

```sql
WHERE m.id=:match_id AND m.status='PROCESSING'
  AND m.generation=:generation
  AND m.resume_revision=:resume_revision AND m.job_revision=:job_revision
  AND m.algorithm_version=:algorithm_version
  AND m.processing_attempt_epoch=:epoch
  AND m.processing_owner_token=:owner
  AND m.processing_activation_token=:activation
  AND m.processing_lease_expires_at > now()
  AND <linked Resume/Job current revision, readiness, and is_deleted=false guards>
```

Match reclaim uses the same snapshot and linked guards, substitutes old epoch/owner plus
`processing_lease_expires_at <= now()`, increments epoch, writes a new owner/lease and clears
activation/heartbeat. Reclaimed activation replaces the old-owner/expired predicates with the new
epoch/owner, `processing_activation_token IS NULL`, and an unexpired lease.

For COMPLETED or FAILED, begin one transaction, identify linked IDs from the fully fenced Match,
lock Job first and Resume second using the established order, verify linked revisions,
soft-delete and readiness, then terminal-update the Match with the same fence. COMPLETED writes all
scores/evidence/provenance; FAILED keeps the canonical cleared non-completed payload. Both clear
owner/activation/heartbeat/lease. Linked mutation, deletion, owner revocation, rowcount zero, or
database error rolls back and discards.

## 8. Lease, errors, and commit order

Lease expiry is permission to compete for ownership, not proof of worker death. Safety comes from
the committed epoch/owner change before new work begins. Thresholds must be task-specific and
longer than measured heartbeat jitter; slow BGE-M3 work must heartbeat independently. Redis health
is not ownership authority.

Claim/activation/reclaim commit before external work. Reclaim commits before broker publication.
Aggregate mutation and terminal transition commit together. Broker ACK is outside database
atomicity and is handled by activation fencing. On ambiguous database commit, a worker reads state
using a new connection and proceeds only if its full fence remains current; it does not replay
non-idempotent aggregate writes blindly.

## 9. Race matrix

| Case | Initial condition | Concurrent operations | Expected CAS winner / durable state | Loser behavior and no-double-write reason |
| --- | --- | --- | --- | --- |
| Old heartbeat vs reclaim | epoch N lease expires | N heartbeat and reclaimer CAS | whichever DB CAS locks first; heartbeat may renew before expiry check, otherwise reclaim commits N+1 | loser rowcount 0; epoch/owner predicate prevents later renewal |
| Old terminal success vs reclaim | N owns PROCESSING at expiry | fenced terminal and reclaim | one update wins; terminal state with cleared fence, or PROCESSING N+1 | loser rolls back; both require N and same owner |
| Failure vs success | one activated attempt | FAILED and success transactions | one terminal CAS commits | winner clears fence; loser rowcount 0 and aggregate transaction rolls back |
| Two reclaim sweepers | same expired N | both propose distinct N+1 owners | one observed-epoch/owner CAS | loser discards and does not publish |
| Duplicate same-attempt messages | unactivated N+1 payload duplicated | two activation CAS calls | one sets activation token | loser does no parser/embedding; SELECT-only token check is never used as authorization |
| Reclaim commit vs publish failure | N+1 committed, not activated | broker publication fails | PROCESSING N+1 remains durable until retry or expiry | no reset; same payload may be republished, activation CAS still single-winner |
| Network partition | activated worker loses DB/broker network | heartbeat/terminal and later reclaim | after DB lease expiry, reclaim can commit N+1 | partitioned N cannot heartbeat or terminal-write after owner changes |
| Slow live BGE-M3 | activated N legitimately runs | heartbeats and observer/reclaimer | heartbeats renew lease; observer is read-only | no age-only failure; reclaim predicate fails while lease current |
| Match linked mutation | Match N processing | Job/Resume revision change and terminal/reclaim | linked mutation/invalidation wins according to established locks | Match CAS sees changed snapshots and discards |
| Soft-delete during work | active Resume/Job/Match | delete and terminal/reclaim | delete or fenced terminal serializes under existing lock order | no terminal/reclaim passes `is_deleted=false` afterward |
| Recovery restart | expired or unactivated attempt persisted | process restarts and rescans | database epoch/owner/lease remains authority | no in-memory ownership assumption and no duplicate aggregate write |
| Legacy worker bypass | old binary can terminal-write without fence | old terminal and fenced attempt | **unsafe by design** | reclaim must remain disabled until mandatory drain barrier proves this impossible |

## 10. Five-phase migration, rollout, and rollback

This is a proposal only. No migration is created or run in R3C observability.

### Phase 1 — Expand-only schema

Add epoch with default zero and add owner/activation/heartbeat/lease as nullable columns. Preserve
all old rows and old CAS behavior. Do **not** add any CHECK requiring ownership for PROCESSING, even
as `NOT VALID`: PostgreSQL enforces a NOT VALID CHECK for every new INSERT/UPDATE and old workers
would fail their PENDING claim. Reclaim and heartbeat remain disabled.

Create partial lease indexes with `CREATE INDEX CONCURRENTLY` in separate, non-transactional
migration steps on PostgreSQL 18 to avoid blocking ordinary writes; failed concurrent builds must
be detected and cleaned through an approved migration procedure. Plain column additions and
defaults require reviewed lock/timeout windows. Rollback: leave harmless nullable columns/indexes,
or drop only unused indexes/columns after proving no deployed code references them.

### Phase 2 — Fence-aware code, reclaim disabled

Deploy code that uses fenced initial claims for new PENDING work and fenced terminal paths for
those claims. It must retain a compatibility path for legacy PROCESSING rows with null ownership;
those rows are observed/quarantined only, not reclaimed or silently assigned an owner. Existing
PENDING recovery continues to redispatch the same revision/generation; whichever new worker wins
the PENDING claim creates the first fenced attempt. Merely deploying new code does not revoke old
workers. Rollback: stop new claims, drain fence-aware attempts, then deploy compatible code; do not
start an old terminal writer while fenced attempts remain.

### Phase 3 — Mandatory old-worker drain barrier

Inventory and stop all old worker processes, remove/disable old deployment artifacts so they
cannot restart, move queue routing exclusively to fence-aware workers, and verify no old terminal
writer remains. Inspect every legacy PROCESSING row and classify it as live, terminally resolved,
or operator-approved recovery candidate; never reset it blindly. Evidence must include process
inventory, deployment version, queue routing, active-task drain, and database inventory. Reclaim
cannot be enabled unless every check passes. Rollback means keep reclaim disabled and restore only
a version that cannot overlap fenced attempts.

### Phase 4 — Ownership enforcement and heartbeat

After the barrier, resolve legacy rows through an approved operator procedure, verify fresh claims
populate all fencing fields, then add ownership/activation consistency CHECK constraints. They may
be introduced NOT VALID only now, because no legacy writer may create invalid new states; validate
existing rows separately before `VALIDATE CONSTRAINT`. Enable and observe heartbeat while reclaim
remains disabled. Rollback: disable heartbeat, drain fenced attempts, keep constraints/columns if
code still depends on them; never null/reset PROCESSING ownership blindly.

### Phase 5 — Controlled reclaim

Enable reclaim only after fencing implementation, activation CAS, real PostgreSQL concurrency
tests, measured heartbeat/lease policy, successful drain evidence, monitoring, and deployment
approval. Start with a narrow canary and kill switch. Rollback order is: disable reclaim first,
allow or deliberately drain current fenced attempts, confirm no old binary can write, then roll
back application behavior. Columns or constraints are removed only in a later migration after all
dependent workers are gone. Revision, generation, and valid terminal data are never decremented,
reset, or discarded during rollback.

## 11. Implemented observability

`processing_observation.py` reads only IDs, revision/generation metadata, timestamps, and delete
flags. The field `snapshot_version` means Resume/Job revision or Match generation; it is not a
future processing attempt epoch. The observer performs three bounded SELECTs, globally
sorts/truncates to the configured batch size, closes the session before logging, and logs only
bounded sample counts plus the batch limit. Those counts are not database-wide totals. Defaults:

- disabled;
- warning threshold 1,800 seconds;
- batch size 100.

It is deliberately not registered in FastAPI lifespan or Celery. Enabling the setting alone does
not start a production sweeper; an operator-approved caller is still required.

## 12. Test matrix

Unit tests cover disabled-by-default behavior, SELECT-only statements, bounded metadata, session
closure, threshold propagation, batch configuration, and logs without IDs/content. Isolated real
PostgreSQL tests cover all three resource types, state/cutoff filtering, deterministic global batch
ordering, exactly three observer SELECT statements with no INSERT/UPDATE/DELETE, unchanged
business and aggregate snapshots, safe sample logging, and outer-transaction fixture rollback.

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
