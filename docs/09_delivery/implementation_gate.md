# IMPLEMENTATION GATE — ROUND 3

## Requirements
- [x] Actor/ownership/privacy rõ.
- [x] Candidate profile cardinality 0..1 thống nhất.
- [x] Role transition Candidate<->HR có conflict rule.
- [x] Refresh session được tách Advanced khỏi OpenAPI MVP.
- [x] Chỉ HR tạo JD mới.
- [x] JD status transition matrix được khóa.

## Database
- [x] 10 bảng canonical.
- [x] Resume/JD có `revision`.
- [x] Match có `generation`, `resume_revision`, `job_revision`.
- [x] Invalidation refresh revision snapshots theo resource hiện tại.
- [x] Embedding lưu `embedding_model` + `embedding_preprocessing_version`.
- [x] COMPLETED/non-COMPLETED payload constraints.
- [x] FAILED/non-FAILED error-state constraints cho Resume/JD/Match.
- [x] Case-insensitive email.
- [x] HNSW cosine indexes.
- [x] `skill_taxonomy_seed.sql` tồn tại và là bootstrap bắt buộc.
- [ ] Chạy schema + seed thật trên PostgreSQL 18 + pgvector.

## Async / Concurrency
- [x] CV/JD parse task mang `expected_revision`.
- [x] Match task mang expected generation + resource revisions.
- [x] Worker stale task phải discard, không overwrite.
- [x] Terminal Match write compare cả Match snapshots và linked resource revisions.
- [x] Batch validate all-or-nothing trước mutation.
- [x] Batch prepare Match trong một transaction.
- [x] Dispatch sau commit; failure trả 503 và retry-safe.

## API
- [x] OpenAPI MVP không expose `/auth/refresh`, `/auth/logout`, `/jobs/{id}/export`.
- [x] POST `/jobs` là HR-only về mặt contract.
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

## UML / Traceability / CI
- [x] Use Case/Activity/Sequence cập nhật revision/generation.
- [x] System/Component Architecture biểu diễn version guard.
- [x] CDM/LDM/PDM/ERD dùng 0..1 CandidateProfile.
- [x] Traceability có seed, concurrency, batch, role transition, auth Advanced.
- [x] Workflow `Validate PTTK` đã PASS OpenAPI + PlantUML + schema/seed/semantic round-3 checks trên baseline vòng 3 trước commit đánh dấu gate này.

> Mọi commit mới sau gate vẫn phải chờ chính workflow này PASS lại trên HEAD mới nhất trước khi merge.

## Deployment gate
- [ ] User review/merge PR.
- [ ] Confirm DB cũ không có dữ liệu cần giữ.
- [ ] Reset DB.
- [ ] Run `schema.sql`.
- [ ] Run `skill_taxonomy_seed.sql`.
- [ ] Verify constraints/indexes/seed + concurrency smoke tests.

Chỉ khi các mục runtime cuối PASS mới chuyển `IMPLEMENTATION_READY` và bắt đầu ORM/Auth.
