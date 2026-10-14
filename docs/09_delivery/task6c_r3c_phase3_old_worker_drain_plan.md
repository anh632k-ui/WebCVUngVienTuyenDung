# Task 6C-R3C Phase 3 — Old-worker drain barrier preparation

**Status:** source preparation only; live drain unverified and reclaim unauthorized.

**Prepared baseline:** `origin/dev` at
`298eb7fa68131e50d1f28cc6c2ac57aaac3a438e`.

This plan prepares evidence collection, offline validation, and operator procedure. It does not
pause publishers, stop processes, alter routing, query a deployment, execute SQL, apply a
migration, enable heartbeat/reclaim, or certify a live environment.

## 1. Safety boundary and decisions

The Phase 3 barrier is a mixed-version exclusion barrier. A fence-aware attempt is unsafe while
any older terminal writer can still run because the older SQL does not require attempt ownership.
An old process being absent from one snapshot is insufficient: its deployment, autoscaler,
supervisor, scheduled launcher, rollback artifact, and image reference must also be unable to
start it again.

The offline validator has only three results: `BLOCKED`, `REVIEW_REQUIRED`, and
`READY_FOR_OPERATOR_REVIEW`. None means production-safe, live-drain-complete, or
reclaim-authorized. A synthetic manifest reaching the highest result proves validator logic only.
Structural validation, source authenticity, and operational approval remain separate decisions.

## 2. Repository writer inventory

The repository establishes code paths; it does not prove what is deployed. Operators must locate
additional launchers, one-off scripts, external services, and old artifacts outside this repo.

| Writer or publisher | Current entry path | Write or publication | Phase 2 ownership behavior |
| --- | --- | --- | --- |
| Resume create/API | `resume_service.upload_resume` | Inserts PENDING Resume, publishes `resume.parse` after commit | creation is not a terminal writer |
| Resume initial claim | `resume_parse_worker` -> `processing_fencing.claim_resume_attempt` or `version_guard.claim_resume_revision` | PENDING -> PROCESSING | flag ON: epoch/owner/activation/lease; flag OFF: all four ownership fields must be NULL |
| Resume terminal | `resume_parse_worker._persist_success/_mark_failed` | aggregate replacement plus PARSED, or FAILED | ON uses full revision/epoch/owner/activation/unexpired-lease fence; OFF requires unowned PROCESSING |
| Resume business mutation | `resume_service.update_resume_parsed_data`, soft delete | increments revision, replaces aggregate, invalidates Matches | Match ownership cleared atomically; locks protect invalidation |
| Job create/API | `job_service.create_job` | Inserts PENDING Job, publishes `job.parse` after commit | creation is not a terminal writer |
| Job initial claim | `job_parse_worker` -> fenced or legacy compatibility claim | PENDING -> PROCESSING | same ON/OFF distinction as Resume |
| Job terminal | `job_parse_worker._persist_success/_mark_failed` | JobSkills plus PARSED, or FAILED | full ownership fence ON; unowned-only compatibility writer OFF |
| Job business mutation | `update_job`, `update_job_weights`, `update_job_criteria`, status/delete | revision/generation changes and Match invalidation | clears relevant processing ownership while preserving epoch |
| Match trigger/API | `match_service.calculate_matches/_prepare_match_batch` | creates/resets PENDING Match and publishes `match.calculate` | generation/snapshot invalidation clears ownership |
| Match initial claim | `match_worker` -> fenced or compatibility claim | PENDING -> PROCESSING | ON adds ownership capability; OFF requires all ownership fields NULL |
| Match terminal | `match_worker._terminal_update` | COMPLETED or FAILED | locks Job then Resume; ON uses full ownership and snapshot fence; OFF requires unowned row |
| PENDING recovery | API lifespan starts Resume, Job, and Match recovery loops | republishes immutable current PENDING payloads | never resets PROCESSING; must be quiesced for drain observation |
| Celery publication | three dispatchers | `resume.parse`, `job.parse`, `match.calculate` | task names and payload contracts remain unchanged |
| Celery execution | `app.tasks.celery_worker`, registered task functions, per-child `worker_runtime` | invokes the three workers | runtime verifies Phase 1 catalog before task execution |
| API process | FastAPI lifespan plus HTTP services | business writes and background publications | catalog readiness check runs at startup when DB is configured |

The reviewed repository contains no standalone production writer script. That is not proof that
none exists in deployment tooling, host crontabs, ad-hoc jobs, or older releases. Such unknowns
block the barrier. Celery parent and child processes are distinct inventory records; Python
processes must not be classified as Celery merely by process name.

## 3. Deployment inventory specification

For every environment-scoped API, Celery parent, Celery child, scheduler, background loop, and
other process able to claim, publish, or terminal-write, record:

- stable environment and workload/host/container identity;
- process kind, state, role, queue subscriptions, and observation time;
- release identity plus immutable artifact digest;
- evidence source identifiers and collector identity.

Classify compatibility as `LEGACY_UNFENCED`, `FENCE_AWARE`, or `UNKNOWN`. `UNKNOWN` blocks. A
mutable tag such as `latest`, a hostname, or a self-reported Git SHA is not enough. Evidence must
link an immutable running artifact to the reviewed release. Cross-check orchestrator inventory
against host/container process inventory so missing workloads and Celery children are visible.

Restart-prevention evidence is required per legacy release: replica count, autoscaling policy,
restart policy, systemd/supervisor state when present, cron/beat/scheduled jobs, deployment
revisions, rollback artifacts, old image references, and every other launcher. A stopped old
worker whose image or supervisor can restart remains a blocker.

## 4. Queue and task drain specification

Inventory broker exchanges, queues, routing keys, bindings, consumer identities, consumer
artifact identities, retry/delayed deliveries, unacknowledged deliveries, and dead-letter queues
through a broker-specific read-only adapter. Exactly the three canonical task names are expected:

- `resume.parse`
- `job.parse`
- `match.calculate`

Collect Celery active, reserved, and scheduled state from every expected parent node and correlate
it with broker and orchestrator inventory. No response is an unresponsive/unknown node, not zero
workers. An empty active snapshot does not prove that reserved, scheduled, delayed, unacked, or
broker-pending work is absent. Capture repeated coherent snapshots across an approved observation
window longer than the relevant prefetch/retry/visibility behavior. Broker/orchestrator details
are adapter inputs; this plan assumes neither Redis nor Kubernetes.

Before the observation window, the operator plan must quiesce every publisher. That includes HTTP
request paths, scheduled publishers, and the three PENDING recovery loops started in FastAPI
lifespan. This repository task does not perform that action.

## 5. PROCESSING inventory and classification

`task6c_r3c_phase3_processing_inventory.sql` is an aggregate, read-only template. It distinguishes
attempt epoch (attempt identity) from Resume/Job revision and Match generation (business snapshot
identity). It returns no row IDs, raw documents, profiles, e-mail, errors, embeddings, or
ownership capability values. Do not execute it on a developer database under this task.
Its canonical table list is the left side of the final join, so the result always contains
`resumes`, `job_descriptions`, and `match_results`; an empty table has zero counts and NULL
minimum/maximum attempt epochs.
For Resume and Job rows, `is_deleted=true` while `parsing_status='PROCESSING'` is reported as
`snapshot_invalidated`. Match invalidation continues to require the referenced Resume and Job
revision to exist and remain not soft-deleted. The inventory does not mutate status, epoch, or
ownership fields.

Each legacy unowned PROCESSING row must receive an operator disposition backed by correlated
process/task evidence: live owner, terminally resolved after the capture, investigation required,
or separately approved recovery candidate. Counts must reconcile. Age of `updated_at` is not
proof of death; lease expiry is not reclaim authorization. Never bulk-reset status, backfill
tokens, decrement/reset epoch, or discard parsed/match evidence.

Inconsistent partial ownership metadata, live legacy owners, unclassified rows, and investigation
items block. An approved recovery-candidate classification records a future operator decision; it
does not itself authorize recovery.

## 6. Evidence manifest and provenance

The strict `r3c-phase3-v1` model is implemented in
`backend/scripts/r3c_phase3_drain_evidence.py`; unknown fields are rejected. The committed JSON is
synthetic and uses placeholder identities/digests. A live bundle must include independently
verifiable sources for orchestrator, process inventory, broker, task inspection, database summary,
and change control. Every inventory item references its sources. Evidence is SHA-256 bound,
timestamped, environment-scoped, and collected without secrets or PII.

Source types are purpose-bound: deployment/process inventory requires orchestrator plus process
inventory; queue routes require broker evidence; worker snapshots require task inspection;
database counts require database summary; and release approval, restart prevention, publisher
quiescence, and operator review require their specified change-control/inventory sources. Merely
listing the six source types does not satisfy the gate. Unused, cross-environment, wrongly typed,
or synthetic sources in a live bundle block validation. A 64-hex SHA-256 remains the evidence
artifact hash, an image digest remains `sha256:<64 hex>`, and `commit_sha` is a 40-hex Git object
ID.

Forbidden evidence includes credentials, bearer/access/ownership tokens, connection URLs with
passwords, CV/JD raw text, profiles, e-mail, embeddings, and persisted error messages. Task
references are hashed. The validator emits only stable issue codes and never echoes rejected
values. A hand-edited JSON file is only a claim: unverifiable/manual provenance yields at best
`REVIEW_REQUIRED`. In particular, `authenticity: VERIFIED` and a syntactically valid digest are
self-assertions unless an operator independently verifies the referenced artifact/trust anchor;
the offline validator therefore never promotes a live bundle past `REVIEW_REQUIRED` on those
fields alone.

Freshness policy is explicit in each manifest, but the manifest does not choose the clock. The
validator captures current UTC from its own runtime and compares the declared `evaluated_at`
against that trusted clock within `maximum_evaluation_clock_skew_seconds`. Unit tests may inject a
frozen clock through the Python API; the CLI has no flag or environment override for it. All
timestamps require timezone offsets, must not exceed future skew or maximum age relative to the
trusted clock, and must fit the approved cross-source capture window. The committed fixed-time
synthetic fixture is test data and is only evaluated with the test-injected clock; it is not a
permanently fresh CLI success example.
Contradictory counts, identities, routes, artifacts, or timestamps fail closed.

Run offline from `backend/`:

```text
python -m scripts.r3c_phase3_drain_evidence <manifest.json>
```

Exit `0` is reserved for `READY_FOR_OPERATOR_REVIEW`; blocked/review-required results exit `2`.
The tool imports no app settings/engine and makes no database, broker, GitHub, or orchestrator call.

## 7. Operator runbook (not executed here)

### Pre-drain

1. Confirm exact live environment identity, approved change window, backup health, rollback plan,
   and accountable operator/reviewer identities.
2. Prove Phase 1 physical migration was separately approved/applied and catalog readiness passed
   before any Phase 2 runtime deployment. Never run canonical `schema.sql` as an upgrade.
3. Bind the approved reviewed source commit to an immutable API/worker artifact digest.
4. Inventory all writer/publisher processes, launch mechanisms, task routes, and current rollback
   artifacts. Stop if any path is unknown.

### Quiesce

1. Under approved operational control, pause new API and other task publication.
2. Quiesce Resume/Job/Match PENDING recovery loops and every scheduler/beat/cron publisher.
3. Prevent autoscaling, restart, supervisor, rollback, and redeployment of legacy artifacts.
4. Record each change and its source; do not infer it from a single process snapshot.

### Drain

1. Allow in-flight work to finish or use a separately approved disposition.
2. Repeatedly inspect every expected Celery node for active/reserved/scheduled tasks.
3. Inspect broker pending, retry/delayed, unacked, and dead-letter state.
4. Reconcile queue consumers with deployment artifact identities and verify no legacy terminal
   writer or subscription remains.

### Verify

1. Capture coherent process/orchestrator, broker/task, restart-prevention, publisher, and aggregate
   database evidence.
2. Reconcile Celery parents/children and all API/background writers across sources.
3. Classify every legacy PROCESSING row without changing it.
4. Run the offline validator, preserve its sanitized result, and conduct independent operator
   review. `READY_FOR_OPERATOR_REVIEW` is not final approval.

### Stop conditions

Any missing, stale, contradictory, unresponsive, unknown, unverifiable, non-quiesced, non-drained,
legacy-routed, restart-capable, incomplete, or unclassified condition stops rollout. Keep reclaim
and heartbeat disabled; do not add ownership CHECKs; do not reset PROCESSING; do not fall back to
an unfenced binary.

### Rollback

Rollback is allowed only to a schema-compatible release with complete ownership predicates. To
return this release to flag OFF, stop publishing, drain active fenced attempts, preserve epochs,
deploy OFF consistently, verify the barrier again, then resume. Never restart an unfenced legacy
binary or clear ownership merely to make rollback appear clean.

## 8. Validator fail-closed matrix

`BLOCKED` covers missing/invalid structure, environment or artifact mismatch, UNKNOWN/active
legacy writers, restart capability, incomplete/unresponsive nodes, active/reserved/scheduled/
delayed/unacked/broker work, legacy routes, incomplete publisher quiescence, missing database
inventory, inconsistent or unclassified PROCESSING rows, unresolved items, expired/future/mixed
timestamps, contradictions, missing item provenance, and CI-only claims presented as live proof.

`REVIEW_REQUIRED` covers a structurally coherent live bundle whose referenced artifact/trust
anchor still needs independent operator verification, or whose collection review remains
manual/unverified. `READY_FOR_OPERATOR_REVIEW` is the synthetic-test ceiling only; it means that
the machine-checkable fixture conditions passed. Final live inventory, actual drain, source
authenticity judgment, and operator authorization stay outside the validator.

## 9. Testing and CI boundary

Unit tests cover the positive synthetic ceiling, all mandatory negative cases, sanitized secret
rejection, strict structure, and execution with deliberately invalid database/broker environment
variables. The dedicated workflow uses an isolated PostgreSQL 18 service only to execute the
read-only inventory template against temporary minimal tables for the empty, one-table, and
all-table cases; it uses no production credential and performs no external operational action. It
also verifies active and soft-deleted Resume/Job PROCESSING rows plus Matches linked to each
soft-deleted resource. Before any test connection, an opt-in guard requires GitHub Actions on a
GitHub-hosted runner and the exact disposable host, port, database, user, and option-free DSN;
non-CI execution skips, while an opted-in GitHub CI target mismatch fails without connecting. It
exercises the fixed example through a frozen unit-test clock and exercises CLI success with
timestamps generated at test runtime. Existing Backend CI
continues to run the full backend test suite,
Ruff, format check, and mypy; existing Phase 1 migration CI, Phase 2 PostgreSQL concurrency
coverage, and PTTK validation remain unchanged.

## 10. Phase boundary

Source preparation and CI may pass independently. Actual live process inventory, operational
drain, and operator approval remain separately unverified. Phase 4 ownership enforcement and
heartbeat, Phase 5 reclaim, production rollout, and any database change are outside this scope.
