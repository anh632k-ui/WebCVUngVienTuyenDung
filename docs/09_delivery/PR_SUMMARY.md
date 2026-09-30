# PTTK REVIEW SUMMARY

## Mục đích

Branch `pttk-sync-v2` thay thế bộ tài liệu PTTK cũ bằng baseline được thiết kế lại và audit chéo trước khi code nghiệp vụ.

## Đã hoàn thành

- Requirements: scope, actor/role/ownership, FR/NFR, business rules, state model.
- BFD.
- Use Case tổng thể/phân hệ + đặc tả UC01..UC19.
- Activity Diagram.
- Sequence Diagram.
- CDM/LDM/PDM/ERD + Data Dictionary + executable `schema.sql`.
- System/Component Architecture.
- AI/NLP Pipeline + Matching Algorithm `hybrid-v1`.
- API Contract + OpenAPI 3.0.3.
- Traceability Matrix.
- Database Reset Plan + Implementation Gate + Final Audit Report.
- Semantic review vòng 2 cho privacy, stale-match invalidation, response contract và DB invariants.

## Canonical decisions

- 10 database tables.
- PostgreSQL 18 + pgvector; embedding `vector(1024)`.
- `resumes.owner_user_id`, `job_descriptions.recruiter_id`.
- `skills.skill_kind=HARD|SOFT`.
- `job_skills.importance=MANDATORY|OPTIONAL`.
- Email account unique không phân biệt hoa/thường bằng DB index `LOWER(email)`.
- Single/batch matching dùng `POST /api/v1/matching/calculate`.
- `GET /api/v1/skills` cho taxonomy lookup.
- `GET /api/v1/matching` cho **danh sách current results**, không phải lịch sử nhiều attempt.
- Candidate self-match với JD của HR không tự động lộ cho HR.
- HR chỉ xem Match/Skill Gap/Leaderboard khi cả JD và CV đều thuộc scope HR trong MVP.
- JD ACTIVE/matching chỉ khi PARSED + criteria verified + embedding hợp lệ + >=1 job_skill.
- Thay criteria/weights/raw JD/parsed CV phải invalidate Match hiện hành; `recalculate=true` chỉ quyết định enqueue ngay.
- Match trigger trả `status=PENDING`, `total_matches`; `QUEUED` không phải MatchStatus.
- `hybrid-v1`: Skill + Semantic Cosine + Experience; BM25 chưa blend vào Final Score.
- LLM/XAI chỉ giải thích/gợi ý, không sửa deterministic score.
- Export PDF/Excel là Advanced và không nằm trong OpenAPI MVP cho tới khi được triển khai thật.

## CI validation

Workflow `Validate PTTK` trên HEAD cuối cùng kiểm tra:

1. OpenAPI bằng `openapi-spec-validator`.
2. JSON success endpoints có response schema.
3. OpenAPI MVP không expose Advanced export.
4. Match status/trigger semantics canonical.
5. Tất cả PlantUML bằng `plantuml -checkonly`.
6. Static schema + semantic sanity: 10 tables, vector(1024), HNSW, case-insensitive email index, current-pair uniqueness, stale-payload constraint, privacy/invalidation rules.

Chỉ merge khi workflow ở HEAD cuối cùng PASS.

## Sau khi merge

Chưa code ngay. Thực hiện:

1. xác nhận DB cũ không có dữ liệu cần giữ;
2. reset `webcv_ungvien`;
3. chạy `docs/05_database/schema.sql`;
4. verify extensions/tables/FK/CHECK/unique indexes/HNSW/smoke tests;
5. rồi mới venv -> DB connection -> ORM -> Auth -> modules nghiệp vụ.

Không có business code backend/frontend nào được triển khai trong rebuild PTTK này.
