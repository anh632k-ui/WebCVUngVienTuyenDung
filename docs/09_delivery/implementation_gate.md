# IMPLEMENTATION GATE — RELIABILITY + FINGERPRINT LOCK

## Requirements
- [x] Actor/ownership/privacy rõ.
- [x] Candidate profile cardinality 0..1 thống nhất.
- [x] Role transition Candidate<->HR có conflict rule.
- [x] Refresh session được tách Advanced khỏi OpenAPI MVP.
- [x] Chỉ HR tạo JD mới.
- [x] JD status transition matrix được khóa.
- [x] `revision` được định nghĩa là input/computation version, tăng trước computation mới.
- [x] Worker của chính expected revision không tăng revision khi terminal commit.
- [x] Create Resume/JD có Idempotency-Key + persisted SHA-256 fingerprint contract.
- [x] Stable application UUIDv5 namespace được khóa literal và không đổi theo deployment.
- [x] Same key + different fingerprint trả `409 IDEMPOTENCY_KEY_REUSED`.
- [x] Resume orphan storage có deterministic key + SHA-256 reconciliation/no-overwrite.
- [x] Soft-deleted Resume/JD bị loại khỏi parse/matching/current analytics và chặn async terminal write mới.

## Database
- [x] 10 bảng canonical.
- [x] Resume/JD có `revision` + `is_deleted`.
- [x] Resume/JD có `create_request_fingerprint VARCHAR(64)` + lowercase SHA-256 format CHECK.
- [x] Match có `generation`, `resume_revision`, `job_revision`.
- [x] Embedding lưu `embedding_model` + `embedding_preprocessing_version`.
- [x] COMPLETED/non-COMPLETED payload constraints.
- [x] FAILED/non-FAILED error-state constraints cho Resume/JD/Match.
- [x] Case-insensitive email.
- [x] HNSW cosine indexes.
- [x] `skill_taxonomy_seed.sql` tồn tại và là bootstrap bắt buộc.
- [ ] Chạy schema + seed thật trên PostgreSQL 18 + pgvector.

## Async / Concurrency
- [x] CV/JD parse task mang `expected_revision`.
- [x] Parse claim exclusive CAS yêu cầu `PENDING + expected_revision + is_deleted=false`.
- [x] Parse terminal CAS yêu cầu `PROCESSING + expected_revision + is_deleted=false`.
- [x] Duplicate parse delivery chỉ một worker claim được.
- [x] Match task mang expected generation + resource revisions.
- [x] Match claim exclusive CAS yêu cầu `PENDING + expected generation/snapshots/linked revisions + linked not-deleted`.
- [x] Match terminal COMPLETED/FAILED chỉ từ `PROCESSING` với same expected values + linked not-deleted.
- [x] Duplicate Match delivery không được race FAILED/COMPLETED.
- [x] Concurrent soft-delete làm worker terminal CAS thất bại/rollback.
- [x] Worker stale/duplicate/deleted task phải discard, không overwrite.
- [x] Batch validate all-or-nothing trước mutation.
- [x] Batch prepare Match trong một transaction.
- [x] Matching dispatch sau commit; failure trả 503 và retry-safe.

## Create Idempotency / Storage Integrity
- [x] `POST /resumes/upload` và `POST /jobs` bắt buộc `Idempotency-Key` UUID.
- [x] Namespace literal: `bd7b1f30-b2de-549c-a8dd-8d742ee5bc12`.
- [x] Deterministic UUIDv5 input = actor id + canonical route + key.
- [x] Resume fingerprint = SHA-256 raw file bytes.
- [x] Job fingerprint = SHA-256 canonical validated create payload after defaults.
- [x] Same deterministic id + same fingerprint trả same resource.
- [x] Same deterministic id + different fingerprint trả 409 `IDEMPOTENCY_KEY_REUSED`.
- [x] Concurrent deterministic-PK loser re-read row + compare fingerprint.
- [x] CV canonical storage key = `resumes/{resume_id}/source`, put-if-absent/no-overwrite.
- [x] Storage object tồn tại nhưng DB row thiếu: compute SHA-256 object; equal => reuse/retry DB insert, mismatch => 409/no-overwrite.
- [x] Storage write success + DB insert fail được coi là orphan tạm thời và có reconciliation deterministic khi retry.

## Parse Dispatch Reliability
- [x] Resume/JD persistence commit trước parse dispatch.
- [x] Immediate parse dispatcher failure giữ resource PENDING, không rollback resource.
- [x] Startup/periodic Parse Recovery Sweeper re-dispatch PENDING + not-deleted quá grace window bằng current revision.
- [x] Recovery không tăng revision và không mutate state trước enqueue.
- [x] Recovery không blind-reset PROCESSING trong MVP nếu chưa có lease/attempt token.

## API
- [x] OpenAPI MVP không expose `/auth/refresh`, `/auth/logout`, `/jobs/{id}/export`.
- [x] POST `/jobs` là HR-only về mặt contract.
- [x] POST `/jobs` và POST `/resumes/upload` có required `Idempotency-Key`.
- [x] Hai create endpoint khai báo 409 `IdempotencyConflict`/`IDEMPOTENCY_KEY_REUSED`.
- [x] Admin role change có 409 conflict.
- [x] Candidate profile nullable trong Resume detail.
- [x] Match response có generation/revisions/provenance.
- [x] Matching calculate có 503 dispatcher failure.
- [x] API Contract filter current Resume/JD/Match/Leaderboard theo soft-delete state.
- [x] JSON success endpoint có response schema.

## Matching
- [x] Skill/Semantic/Experience/Overall deterministic.
- [x] Experience missing-date policy rõ.
- [x] CV/JD phải cùng model + preprocessing version.
- [x] BM25 không blend v1.
- [x] LLM/XAI Advanced/explanation only.

## UML / Traceability
- [x] Use Case/Activity/Sequence cập nhật revision/generation.
- [x] CV/JD Activity/Sequence cập nhật exclusive CAS + fingerprinted idempotent create + recovery + delete-state guard.
- [x] CV Activity/Sequence có orphan storage SHA-256 reconciliation + no-overwrite.
- [x] Matching Activity/Sequence cập nhật PENDING claim, PROCESSING-only terminal CAS + linked delete-state guard.
- [x] System/Component Architecture có UUIDv5/SHA-256 Idempotency Guard + Parse Recovery Sweeper + delete-aware CAS Guard.
- [x] PDM/Data Dictionary/schema có `create_request_fingerprint`.
- [x] CDM/LDM/PDM/ERD vẫn giữ đúng 10 entity/table và 0..1 CandidateProfile; fingerprint là physical reliability metadata nên không thêm entity/relationship.
- [x] Traceability có seed, concurrency, batch, role transition, auth Advanced, fingerprinted idempotency, storage reconciliation, recovery và soft-delete race guard.
- [ ] GitHub Actions PASS trên HEAD cuối cùng fingerprint-lock baseline.

## Runtime validation bắt buộc trước code nghiệp vụ
- [ ] Duplicate CV parse task cùng expected_revision: đúng 1 claim success.
- [ ] Duplicate JD parse task cùng expected_revision: đúng 1 claim success.
- [ ] Duplicate Match task cùng generation: đúng 1 claim success; không race terminal state.
- [ ] Stale old revision/generation không overwrite state mới.
- [ ] Soft-delete Resume/JD sau claim nhưng trước terminal commit: terminal write fail/rollback.
- [ ] Current Match/Leaderboard không trả linked Resume/JD đã soft-delete.
- [ ] Simulate parse dispatcher failure sau commit: resource còn PENDING và recovery re-dispatch được.
- [ ] Retry Resume POST cùng key + cùng file fingerprint: cùng resource id, không duplicate row/file.
- [ ] Retry Job POST cùng key + cùng canonical fingerprint: cùng resource id, không duplicate row.
- [ ] Reuse same key với Resume file khác hoặc Job payload khác: `409 IDEMPOTENCY_KEY_REUSED`.
- [ ] Concurrent duplicate create cùng key/fingerprint: PK loser re-read và trả same resource.
- [ ] Resume storage orphan cùng fingerprint: retry reuse object và insert DB; orphan khác fingerprint: 409, object không bị overwrite.
- [ ] Restart/deploy với cùng code constant: UUIDv5 namespace cho cùng actor/route/key vẫn sinh cùng resource id.

## Deployment gate
- [ ] User review/merge PR.
- [ ] Confirm DB cũ không có dữ liệu cần giữ.
- [ ] Reset DB.
- [ ] Run `schema.sql`.
- [ ] Run `skill_taxonomy_seed.sql`.
- [ ] Verify constraints/indexes/seed + runtime smoke tests bên trên.

Chỉ khi các mục runtime cuối PASS mới chuyển `IMPLEMENTATION_READY` và bắt đầu ORM/Auth.
