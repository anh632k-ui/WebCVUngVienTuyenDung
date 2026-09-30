# IMPLEMENTATION GATE — POST ROUND 3 RELIABILITY LOCK

## Requirements
- [x] Actor/ownership/privacy rõ.
- [x] Candidate profile cardinality 0..1 thống nhất.
- [x] Role transition Candidate<->HR có conflict rule.
- [x] Refresh session được tách Advanced khỏi OpenAPI MVP.
- [x] Chỉ HR tạo JD mới.
- [x] JD status transition matrix được khóa.
- [x] `revision` được định nghĩa là input/computation version, tăng trước computation mới.
- [x] Worker của chính expected revision không tăng revision khi terminal commit.
- [x] Create Resume/JD có idempotency contract.

## Database
- [x] 10 bảng canonical.
- [x] Resume/JD có `revision`.
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
- [x] Parse claim exclusive CAS yêu cầu `PENDING + expected_revision`.
- [x] Parse terminal CAS yêu cầu `PROCESSING + expected_revision`.
- [x] Duplicate parse delivery chỉ một worker claim được.
- [x] Match task mang expected generation + resource revisions.
- [x] Match claim exclusive CAS yêu cầu `PENDING + expected generation/snapshots/linked revisions`.
- [x] Match terminal COMPLETED/FAILED chỉ từ `PROCESSING` với same expected values.
- [x] Duplicate Match delivery không được race FAILED/COMPLETED.
- [x] Worker stale/duplicate task phải discard, không overwrite.
- [x] Batch validate all-or-nothing trước mutation.
- [x] Batch prepare Match trong một transaction.
- [x] Matching dispatch sau commit; failure trả 503 và retry-safe.

## Parse Dispatch Reliability
- [x] `POST /resumes/upload` và `POST /jobs` bắt buộc `Idempotency-Key` UUID.
- [x] Server derive deterministic UUIDv5 từ actor+route+key để retry cùng logical request không tạo duplicate resource.
- [x] Resume/JD persistence commit trước parse dispatch.
- [x] Immediate parse dispatcher failure giữ resource PENDING, không rollback resource.
- [x] Startup/periodic Parse Recovery Sweeper re-dispatch PENDING quá grace window bằng current revision.
- [x] Recovery không tăng revision và không mutate state trước enqueue.
- [x] Recovery không blind-reset PROCESSING trong MVP nếu chưa có lease/attempt token.

## API
- [x] OpenAPI MVP không expose `/auth/refresh`, `/auth/logout`, `/jobs/{id}/export`.
- [x] POST `/jobs` là HR-only về mặt contract.
- [x] POST `/jobs` và POST `/resumes/upload` có required `Idempotency-Key`.
- [x] Admin role change có 409 conflict.
- [x] Candidate profile nullable trong Resume detail.
- [x] Match response có generation/revisions/provenance.
- [x] Matching calculate có 503 dispatcher failure.
- [x] JSON success endpoint có response schema.

## Matching
- [x] Skill/Semantic/Experience/Overall deterministic.
- [x] Experience missing-date policy rõ.
- [x] CV/JD phải cùng model + preprocessing version.
- [x] BM25 không blend v1.
- [x] LLM/XAI Advanced/explanation only.

## UML / Traceability
- [x] Use Case/Activity/Sequence cập nhật revision/generation.
- [x] CV/JD Activity/Sequence cập nhật exclusive CAS + idempotent create + recovery.
- [x] Matching Activity/Sequence cập nhật PENDING claim và PROCESSING-only terminal CAS.
- [x] System/Component Architecture có Idempotency Guard + Parse Recovery Sweeper + CAS Guard.
- [x] CDM/LDM/PDM/ERD dùng 0..1 CandidateProfile.
- [x] Traceability có seed, concurrency, batch, role transition, auth Advanced, idempotency và recovery.
- [ ] GitHub Actions PASS trên HEAD cuối cùng reliability lock.

## Runtime validation bắt buộc trước code nghiệp vụ
- [ ] Duplicate CV parse task cùng expected_revision: đúng 1 claim success.
- [ ] Duplicate JD parse task cùng expected_revision: đúng 1 claim success.
- [ ] Duplicate Match task cùng generation: đúng 1 claim success; không race terminal state.
- [ ] Stale old revision/generation không overwrite state mới.
- [ ] Simulate parse dispatcher failure sau commit: resource còn PENDING và recovery re-dispatch được.
- [ ] Retry POST create cùng Idempotency-Key: cùng resource id, không duplicate row.

## Deployment gate
- [ ] User review/merge PR.
- [ ] Confirm DB cũ không có dữ liệu cần giữ.
- [ ] Reset DB.
- [ ] Run `schema.sql`.
- [ ] Run `skill_taxonomy_seed.sql`.
- [ ] Verify constraints/indexes/seed + runtime smoke tests bên trên.

Chỉ khi các mục runtime cuối PASS mới chuyển `IMPLEMENTATION_READY` và bắt đầu ORM/Auth.
