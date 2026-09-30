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
- Candidate self-match với JD của HR **không tự động cấp quyền cho HR xem CV/kết quả đó**.
- HR chỉ xem Match/Skill Gap/Leaderboard khi JD thuộc HR **và CV cũng thuộc kho HR** trong MVP.

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

Naming/invariant canonical:

- `skills.skill_kind = HARD|SOFT`.
- `job_skills.importance = MANDATORY|OPTIONAL`.
- không dùng `job_skills.skill_type`.
- embedding là `vector(1024)` và lưu `embedding_model`.
- email unique không phân biệt hoa/thường bằng `uq_users_email_ci ON LOWER(email)`.
- `match_results` chỉ giữ **một kết quả hiện hành** cho mỗi `(job_id,resume_id)`.
- non-COMPLETED Match bắt buộc clear stale scores/evidence/timestamp.

### State

- Resume/Job parse: `PENDING|PROCESSING|PARSED|FAILED`.
- Job business: `DRAFT|ACTIVE|CLOSED`.
- Match: `PENDING|PROCESSING|COMPLETED|FAILED`.
- `QUEUED` không phải MatchStatus; response trigger matching dùng `PENDING`.
- JD chỉ ACTIVE/matching khi đã PARSED, criteria verified, embedding hợp lệ và service xác nhận có ít nhất một `job_skill`.

### API

- Base `/api/v1`.
- Matching single/batch chỉ dùng `POST /matching/calculate`.
- Có `GET /matching` cho **danh sách kết quả hiện hành** theo ownership, không phải attempt history.
- Có `GET /skills` cho taxonomy lookup/Human-in-the-loop.
- JSON endpoint chính trong OpenAPI có response schema thực, không chỉ `description`.
- `/jobs/{id}/export` là Advanced và không nằm trong OpenAPI MVP.
- Refresh Token HttpOnly nếu dùng phải do backend `Set-Cookie`.

### Matching / AI

- Algorithm canonical đầu tiên: `hybrid-v1`.
- `Overall = w_skill*Skill + w_semantic*Semantic + w_experience*Experience`.
- mặc định `0.50 / 0.30 / 0.20`.
- Semantic dùng cosine embedding cùng model/version.
- BM25 trong `hybrid-v1` chỉ là diagnostic/retrieval/experiment signal; không âm thầm blend vào Final Score.
- LLM/XAI chỉ giải thích/gợi ý; không sửa deterministic scores.
- Mọi thay đổi làm score stale phải invalidate Match ngay; `recalculate=true` chỉ quyết định enqueue ngay hay không.

## 3. Mâu thuẫn thiết kế cũ đã xử lý

- Bổ sung `candidate_profiles` thay vì Activity ghi vào bảng không tồn tại.
- Đồng bộ field parse/error/manual-edit/soft-delete giữa Activity và schema.
- Tách Job parsing state khỏi Job business state.
- Sửa Match lifecycle để scores được NULL trước `COMPLETED`.
- Chuẩn hóa `/matching/calculate`; loại bỏ split route single/batch.
- Đồng bộ API Contract và OpenAPI route set.
- Bổ sung Skill Taxonomy lookup và danh sách Match hiện hành để UI không hard-code skill/match id.
- Sửa Refresh Cookie ownership về backend.
- Đồng bộ PostgreSQL mục tiêu thành PostgreSQL 18 + pgvector.
- Thêm BFD và Sequence Diagram theo cam kết đề cương.
- Sửa Use Case JD parsing thành luồng conditional khi tạo/thay `raw_content`.
- Khóa invariant criteria verified trước publish/matching.
- Loại bỏ tài liệu legacy ở `docs/Usecase`, `docs/CSDL`, `docs/api`, `docs/system_architecture.puml` khỏi branch mới.

## 4. Semantic review vòng 2 và các lỗi đã sửa

Sau lần audit đầu, một vòng review độc lập tiếp theo phát hiện các lỗi mà validator cú pháp không thể bắt. Tất cả đã được sửa trước khi merge:

1. **Privacy Candidate -> HR:** HR trước đây có thể đọc Match chỉ vì sở hữu JD. Đã sửa thành HR phải đồng thời sở hữu JD **và** CV trong MVP.
2. **Stale Match sau đổi criteria/weights/CV/JD:** đã khóa rule invalidate bắt buộc, không phụ thuộc `recalculate=true`.
3. **Reset payload chưa đầy đủ:** invalidate giờ clear cả four scores, matched/missing JSON, gap summary, error, `calculated_at`.
4. **Response trigger dùng `QUEUED`/`total_jobs`:** đã sửa thành `status=PENDING`, `total_matches`.
5. **OpenAPI thiếu response schema:** đã bổ sung schema cụ thể cho endpoint JSON chính.
6. **“Matching history” sai nghĩa với unique pair:** đã đổi canonical wording thành **kết quả hiện hành**; MVP không giữ nhiều attempt.
7. **Advanced export bị expose trong OpenAPI MVP:** đã loại khỏi OpenAPI; vẫn giữ như feature dự kiến trong Use Case/API Contract Advanced.
8. **Email unique chỉ dựa application lowercase:** đã thêm DB unique index trên `LOWER(email)`.

## 5. Kiểm tra tự động

Workflow `.github/workflows/pttk-validate.yml` kiểm tra:

1. OpenAPI YAML parse + `openapi-spec-validator`.
2. Các endpoint thành công JSON (`200/201/202`) phải có response schema.
3. OpenAPI MVP không được expose route export Advanced.
4. Match trigger status phải là `PENDING`; không dùng `QUEUED` làm MatchStatus.
5. `plantuml -checkonly` cho toàn bộ file `.puml`.
6. Static schema sanity: đúng 10 bảng, vector(1024), cosine HNSW, case-insensitive email index, unique current match pair, non-COMPLETED stale-payload check.
7. Semantic design sanity: business rules phải chứa privacy/invalidation canonical.

PR cuối cùng phải giữ workflow này xanh ở HEAD trước khi merge.

## 6. Traceability audit

Ma trận `08_traceability/traceability_matrix.md` truy vết các Use Case từ FR tới Activity/Sequence/API/bảng chính.

Các requirement xuyên suốt đã được truy vết riêng:

- Skill Taxonomy lookup -> `GET /skills` -> `skills`.
- Current Matching Results -> `GET /matching` -> `match_results`.
- Match privacy -> Business Rules -> UC15/16/17 -> Activity/Sequence -> API authorization.
- Match invalidation -> CV/JD mutation flows -> matching algorithm -> schema constraint.
- Case-insensitive email -> BR-AUTH-01 -> schema/PDM/Data Dictionary.
- verified JD criteria -> Business Rules -> UC13/UC15 -> API -> schema/service invariant.

## 7. Gate còn lại không thuộc lỗi PTTK

PTTK đạt `DESIGN_LOCKED_FOR_REVIEW`, nhưng chưa được gọi `IMPLEMENTATION_READY` cho đến khi user thực hiện sau khi review/merge PR:

1. xác nhận DB cũ không có dữ liệu cần giữ;
2. reset `webcv_ungvien`;
3. chạy canonical `docs/05_database/schema.sql` trên PostgreSQL 18 + pgvector;
4. verify extensions, tables, FK, CHECK constraints, unique indexes, HNSW indexes và smoke-test constraint.

Đây là runtime deployment verification, không phải một bước phải làm trước khi review PTTK.

## 8. Kết luận

Sau semantic review vòng 2, các blocker/privacy/invalidation/API-contract đã biết đã được xử lý trong baseline. Bộ PTTK chỉ đủ điều kiện merge khi GitHub Actions ở **HEAD hiện tại** PASS lại toàn bộ OpenAPI + PlantUML + schema/semantic sanity.

Sau khi PR được merge và database runtime verification PASS, dự án mới chuyển sang `IMPLEMENTATION_READY` và bắt đầu code theo thứ tự venv -> DB connection -> ORM -> Auth -> Resume/Job -> AI/NLP -> Matching -> Frontend.
