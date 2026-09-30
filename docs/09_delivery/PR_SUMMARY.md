# PTTK REVIEW SUMMARY — RELIABILITY + FINGERPRINT LOCK

Branch `pttk-sync-v2` thay toàn bộ PTTK legacy bằng baseline thống nhất trước khi code.

## Artefact hoàn chỉnh
- Requirements/ownership/business rules/state model.
- BFD.
- Use Case UC01..UC19 + đặc tả.
- Activity + Sequence.
- CDM/LDM/PDM/ERD + Data Dictionary + `schema.sql`.
- `skill_taxonomy_seed.sql` bootstrap dữ liệu taxonomy.
- System/Component Architecture + AI/NLP Pipeline + `hybrid-v1`.
- API Contract + OpenAPI 3.0.3.
- Traceability Matrix.
- Reset Database Plan, Implementation Gate, Audit Report.

## Quyết định canonical hiện tại
- PostgreSQL 18 + pgvector, đúng 10 bảng.
- Resume/JD có `revision`; Match có `generation`, `resume_revision`, `job_revision`.
- `revision` = version của input/computation request; tăng trước computation mới, worker của chính expected revision không tăng lúc commit.
- CV/JD parse claim exclusive CAS: chỉ `PENDING -> PROCESSING` khi expected revision còn đúng **và resource chưa soft-delete**; terminal chỉ từ PROCESSING cùng revision + not-deleted.
- Match claim exclusive CAS: PENDING + expected generation/snapshots/linked revisions + linked not-deleted; terminal COMPLETED/FAILED chỉ từ PROCESSING với cùng expected values + linked not-deleted.
- Duplicate queue delivery cùng revision/generation chỉ một worker claim được; concurrent soft-delete làm terminal CAS fail/rollback.
- `POST /resumes/upload` và `POST /jobs` bắt buộc `Idempotency-Key` UUID.
- Stable UUIDv5 namespace canonical: `bd7b1f30-b2de-549c-a8dd-8d742ee5bc12`.
- Resume/JD persist `create_request_fingerprint` SHA-256; không thêm bảng thứ 11.
- Resume fingerprint = SHA-256 raw file bytes; Job fingerprint = SHA-256 canonical validated create payload after defaults.
- Same key + same fingerprint => same resource; same key + different fingerprint => `409 IDEMPOTENCY_KEY_REUSED`.
- Concurrent deterministic-PK loser re-read persisted row và compare fingerprint.
- CV storage key canonical `resumes/{resume_id}/source`; create dùng put-if-absent/no-overwrite.
- Resume storage orphan chỉ được reuse khi SHA-256 object == request fingerprint; mismatch => 409, không overwrite.
- Resume/JD persistence commit trước parse dispatch. Dispatcher lỗi sau commit giữ resource PENDING.
- Parse Recovery Sweeper startup/periodic re-dispatch PENDING + not-deleted quá grace window bằng current revision; không tăng revision.
- Không blind-reset PROCESSING trong MVP nếu chưa có lease/attempt token.
- Current Match/Gap/Leaderboard loại linked Resume/JD đã soft-delete.
- Embedding identity gồm cả `embedding_model` và `embedding_preprocessing_version`.
- Skill Taxonomy phải seed sau schema; parser không tự insert skill lạ.
- CandidateProfile cardinality 0..1/Resume.
- Candidate<->HR role change chỉ khi không còn Resume/JD chưa soft-delete; conflict 409.
- Refresh Rotation/HttpOnly session là Advanced, không expose trong OpenAPI MVP.
- `POST /jobs` chỉ HR; Admin chỉ quản trị JD đã tồn tại.
- JD transition: DRAFT->ACTIVE; ACTIVE->DRAFT|CLOSED; CLOSED->DRAFT|ACTIVE; same-state idempotent; DRAFT->CLOSED invalid.
- Batch matching validate toàn bộ trước mutation, atomic prepare transaction, dispatch sau commit; dispatch lỗi trả 503 và retry-safe.
- Experience Score không đoán khoảng thời gian khi date thiếu.
- FAILED bắt buộc có error; non-FAILED không giữ stale error.
- Candidate self-match private; HR chỉ xem Match khi JD + CV đều thuộc HR.
- `hybrid-v1` = Skill + cosine Semantic + Experience; BM25 diagnostic, LLM/XAI explanation only.

## Branch topology
- `pttk-sync-v2`: canonical branch, head của PR #1.
- `pttk-sync-v2-r3-work`: ancestor/checkpoint sau review vòng 3.
- `pttk-sync-v2-safety-copy`: ancestor/checkpoint cũ hơn.
Hai branch phụ không có commit độc lập cần merge ngược vào canonical branch.

## CI
`Validate PTTK` kiểm OpenAPI, Idempotency-Key + 409 fingerprint conflict, stable namespace/fingerprint docs, response schema, revision/generation/provenance, PlantUML, 10-table schema/seed, đúng 2 fingerprint fields/checks, exclusive CAS state guards, delete-aware guards, Recovery Sweeper, Resume orphan reconciliation và FR-43..48 traceability.

Chỉ merge khi CI ở HEAD cuối cùng PASS.

## Sau merge
1. xác nhận DB cũ không cần giữ dữ liệu;
2. reset `webcv_ungvien`;
3. chạy `docs/05_database/schema.sql`;
4. chạy `docs/05_database/skill_taxonomy_seed.sql`;
5. verify extensions/tables/FK/CHECK/index/HNSW/seed;
6. smoke-test duplicate worker claim, stale revision/generation, soft-delete race, dispatcher recovery;
7. smoke-test same-key same-fingerprint, same-key different-fingerprint 409, concurrent deterministic create và Resume orphan storage reconciliation;
8. sau đó mới venv -> async DB -> ORM -> Auth MVP -> Skills -> Resume/Job -> AI/NLP -> Matching -> Frontend.

PR này vẫn chưa triển khai business code backend/frontend.
