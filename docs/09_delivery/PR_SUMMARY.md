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

## Canonical decisions

- 10 database tables.
- PostgreSQL 18 + pgvector; embedding `vector(1024)`.
- `resumes.owner_user_id`, `job_descriptions.recruiter_id`.
- `skills.skill_kind=HARD|SOFT`.
- `job_skills.importance=MANDATORY|OPTIONAL`.
- Single/batch matching dùng `POST /api/v1/matching/calculate`.
- `GET /api/v1/skills` cho taxonomy lookup.
- `GET /api/v1/matching` cho match history.
- JD ACTIVE/matching chỉ khi PARSED + criteria verified + embedding hợp lệ + >=1 job_skill.
- `hybrid-v1`: Skill + Semantic Cosine + Experience; BM25 chưa blend vào Final Score.
- LLM/XAI chỉ giải thích/gợi ý, không sửa deterministic score.

## CI validation

Workflow `Validate PTTK` chạy trên branch và kiểm tra:

1. OpenAPI bằng `openapi-spec-validator`.
2. Tất cả PlantUML bằng `plantuml -checkonly`.
3. Static sanity của canonical schema.

Final pre-PR branch run đã PASS cả ba nhóm kiểm tra.

## Sau khi merge

Chưa code ngay. Thực hiện:

1. xác nhận DB cũ không có dữ liệu cần giữ;
2. reset `webcv_ungvien`;
3. chạy `docs/05_database/schema.sql`;
4. verify extensions/tables/FK/CHECK/HNSW/smoke tests;
5. rồi mới venv -> DB connection -> ORM -> Auth -> modules nghiệp vụ.

Không có business code backend/frontend nào được triển khai trong rebuild PTTK này.
