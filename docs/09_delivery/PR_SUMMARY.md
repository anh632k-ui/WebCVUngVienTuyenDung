# PTTK REVIEW SUMMARY — ROUND 3

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

## Các quyết định canonical sau semantic review vòng 3
- PostgreSQL 18 + pgvector, đúng 10 bảng.
- Resume/JD có `revision`; Match có `generation`, `resume_revision`, `job_revision` để chống stale worker.
- Parse task dùng `expected_revision`; matching task dùng generation + resource revisions; conditional terminal write.
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

## CI
`Validate PTTK` kiểm:
1. OpenAPI bằng `openapi-spec-validator`.
2. Advanced route không bị expose trong MVP.
3. Response schema cho success JSON.
4. Role conflict 409 + dispatcher 503.
5. Revision/generation/provenance fields.
6. CandidateProfile nullable.
7. PlantUML `-checkonly` toàn bộ.
8. Đúng 10 tables, revision/generation/preprocessing/error constraints, HNSW, case-insensitive email.
9. Seed có HARD + SOFT skill và đủ baseline entries.
10. Round-3 business invariants tồn tại.

Chỉ merge khi CI ở HEAD cuối cùng PASS.

## Sau merge
1. xác nhận DB cũ không cần giữ dữ liệu;
2. reset `webcv_ungvien`;
3. chạy `docs/05_database/schema.sql`;
4. chạy `docs/05_database/skill_taxonomy_seed.sql`;
5. verify extensions/tables/FK/CHECK/index/HNSW/seed;
6. sau đó mới venv -> async DB -> ORM -> Auth MVP -> Skills -> Resume/Job -> AI/NLP -> Matching -> Frontend.

PR này vẫn chưa triển khai business code backend/frontend.
