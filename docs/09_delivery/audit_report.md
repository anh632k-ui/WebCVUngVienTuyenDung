# FINAL PTTK AUDIT REPORT

## 1. Phạm vi audit

Audit áp dụng cho toàn bộ bộ PTTK rebuild trên branch `pttk-sync-v2`:

`Requirements -> BFD -> Use Case -> Activity -> Sequence -> Database -> Architecture/AI -> API Contract/OpenAPI -> Traceability -> Delivery Gate`.

Mục tiêu là loại bỏ các mâu thuẫn của thiết kế cũ trước khi bắt đầu code nghiệp vụ.

## 2. Các quyết định canonical đã khóa

### Actor / ownership

- Guest, Candidate, HR, Admin.
- `resumes.owner_user_id` là ownership CV canonical.
- `job_descriptions.recruiter_id` là ownership JD canonical.
- Candidate/HR chỉ quản lý CV thuộc kho mình trong MVP; Admin giám sát toàn hệ thống.
- HR chỉ quản lý JD mình sở hữu; Admin có quyền quản trị.
- Candidate không được xem leaderboard của ứng viên khác.

### Database

Canonical schema có đúng 10 bảng:

1. `users`
2. `skills`
3. `resumes`
4. `candidate_profiles`
5. `resume_skills`
6. `resume_experiences`
7. `resume_educations`
8. `job_descriptions`
9. `job_skills`
10. `match_results`

Naming canonical:

- `skills.skill_kind = HARD|SOFT`.
- `job_skills.importance = MANDATORY|OPTIONAL`.
- không dùng `job_skills.skill_type`.
- embedding là `vector(1024)` và lưu `embedding_model`.

### State

- Resume/Job parse: `PENDING|PROCESSING|PARSED|FAILED`.
- Job business: `DRAFT|ACTIVE|CLOSED`.
- Match: `PENDING|PROCESSING|COMPLETED|FAILED`.
- JD chỉ ACTIVE/matching khi đã PARSED, criteria verified, embedding hợp lệ và service xác nhận có ít nhất một `job_skill`.

### API

- Base `/api/v1`.
- Matching single/batch chỉ dùng `POST /matching/calculate`.
- Có `GET /matching` cho lịch sử kết quả theo ownership.
- Có `GET /skills` cho taxonomy lookup/Human-in-the-loop.
- Refresh Token HttpOnly nếu dùng phải do backend `Set-Cookie`.

### Matching / AI

- Algorithm canonical đầu tiên: `hybrid-v1`.
- `Overall = w_skill*Skill + w_semantic*Semantic + w_experience*Experience`.
- mặc định `0.50 / 0.30 / 0.20`.
- Semantic dùng cosine embedding cùng model/version.
- BM25 trong `hybrid-v1` chỉ là diagnostic/retrieval/experiment signal; không âm thầm blend vào Final Score.
- LLM/XAI chỉ giải thích/gợi ý; không sửa deterministic scores.

## 3. Mâu thuẫn thiết kế cũ đã xử lý

- Bổ sung `candidate_profiles` thay vì Activity ghi vào bảng không tồn tại.
- Đồng bộ field parse/error/manual-edit/soft-delete giữa Activity và schema.
- Tách Job parsing state khỏi Job business state.
- Sửa Match lifecycle để scores được NULL trước `COMPLETED`.
- Chuẩn hóa `/matching/calculate`; loại bỏ split route single/batch.
- Đồng bộ API Contract và OpenAPI route set.
- Bổ sung Skill Taxonomy lookup và Matching History để UI không phải hard-code skill/match id.
- Sửa Refresh Cookie ownership về backend.
- Đồng bộ PostgreSQL mục tiêu thành PostgreSQL 18 + pgvector.
- Thêm BFD và Sequence Diagram theo cam kết đề cương.
- Sửa Use Case JD parsing thành luồng conditional khi tạo/thay `raw_content`, không phải mọi Update JD đều parse lại.
- Khóa invariant criteria verified trước publish/matching.
- Loại bỏ tài liệu legacy ở `docs/Usecase`, `docs/CSDL`, `docs/api`, `docs/system_architecture.puml` khỏi branch mới.

## 4. Kiểm tra tự động

Workflow `.github/workflows/pttk-validate.yml` kiểm tra:

1. OpenAPI YAML parse + `openapi-spec-validator`.
2. `plantuml -checkonly` cho toàn bộ file `.puml`.
3. Static sanity của `schema.sql`: đúng 10 bảng, vector(1024), cosine indexes và match lifecycle.

Trong audit, workflow `Validate PTTK` đã PASS đầy đủ OpenAPI + PlantUML + schema static sau khi sửa lỗi portable PlantUML ở component architecture.

PR cuối cùng vẫn phải giữ check này xanh ở HEAD trước khi merge.

## 5. Traceability audit

Ma trận `08_traceability/traceability_matrix.md` đã truy vết các Use Case từ FR tới Activity/Sequence/API/bảng chính.

Các support requirement quan trọng đã được thêm riêng:

- FR-35 Skill Taxonomy lookup -> `GET /skills` -> `skills`.
- FR-36 Matching History -> `GET /matching` -> `match_results`.
- verified JD criteria -> Business Rules -> UC13/UC15 -> API -> schema/service invariant.

## 6. Gate còn lại không thuộc lỗi PTTK

PTTK đạt `DESIGN_LOCKED_FOR_REVIEW`, nhưng chưa được gọi `IMPLEMENTATION_READY` cho đến khi user thực hiện sau khi review/merge PR:

1. xác nhận DB cũ không có dữ liệu cần giữ;
2. reset `webcv_ungvien`;
3. chạy canonical `docs/05_database/schema.sql` trên PostgreSQL 18 + pgvector;
4. verify extensions, tables, FK, CHECK constraints, HNSW indexes và smoke-test constraint.

Đây là runtime deployment verification, không phải một bước phải làm trước khi review PTTK.

## 7. Kết luận

Không phát hiện mâu thuẫn blocker còn biết được trong baseline thiết kế sau audit semantic + CI syntax/static validation. Bộ PTTK đủ điều kiện đưa vào Pull Request để user review.

Sau khi PR được merge và database runtime verification PASS, dự án mới chuyển sang `IMPLEMENTATION_READY` và bắt đầu code theo thứ tự venv -> DB connection -> ORM -> Auth -> Resume/Job -> AI/NLP -> Matching -> Frontend.
