# IMPLEMENTATION GATE — ĐIỀU KIỆN CHO PHÉP BẮT ĐẦU CODE

Trạng thái sau audit thiết kế trên branch `pttk-sync-v2`.

## A. Requirements

- [x] Scope/MVP/Advanced rõ ràng.
- [x] Actor Candidate/HR/Admin không chồng quyền mâu thuẫn.
- [x] Ownership CV/JD/Match được định nghĩa.
- [x] Business rules có mã BR và đồng bộ với API/schema.
- [x] State model khớp CHECK constraint và service-level invariant.

## B. UML

- [x] BFD phản ánh đủ các phân hệ chính.
- [x] Use Case overview và phân hệ dùng cùng UC ID.
- [x] Đặc tả Use Case có precondition/postcondition/exception cho luồng quan trọng.
- [x] Activity không còn tham chiếu endpoint/table/field legacy không tồn tại.
- [x] Sequence dùng cùng component/lifecycle với kiến trúc tổng thể.
- [x] Không còn lỗi `@endumlx`; các file có cặp `@startuml`/`@enduml`.
- [x] GitHub Actions `Validate PTTK` đã chạy `plantuml -checkonly` cho toàn bộ `.puml` và PASS.

## C. Database

- [x] CDM -> LDM -> PDM -> ERD dùng cùng mô hình 10 bảng/cardinality.
- [x] `schema.sql` có đúng 10 bảng canonical.
- [x] Data Dictionary khớp naming/enum/invariant của schema.
- [x] `match_results` cho phép score NULL khi PENDING/PROCESSING.
- [x] `candidate_profiles` tồn tại và là 1:1 với resume.
- [x] `job_descriptions` có parsing state tách business status.
- [x] JD ACTIVE yêu cầu PARSED + criteria verified; service/API còn yêu cầu >=1 `job_skill`.
- [x] HNSW index dùng `vector_cosine_ops` trên 2 embedding.
- [x] CI static schema sanity PASS: đúng 10 bảng, vector(1024), cosine indexes và match lifecycle canonical.
- [ ] Chạy `schema.sql` thật trên PostgreSQL 18 + pgvector sau khi PR được duyệt.

## D. API

- [x] API Contract và OpenAPI dùng cùng route set canonical.
- [x] Matching chỉ có `/matching/calculate` cho trigger single/batch.
- [x] Có `GET /matching` để truy hồi lịch sử kết quả theo ownership.
- [x] Có `GET /skills` làm nguồn taxonomy cho Human-in-the-loop.
- [x] API dùng `owner_user_id`, `importance`, `skill_kind` đúng naming canonical.
- [x] Refresh token HttpOnly do backend `Set-Cookie`.
- [x] Candidate không được gọi leaderboard.
- [x] HR owner check áp dụng cho JD/leaderboard/weights/criteria.
- [x] JD chỉ ACTIVE/matching khi PARSED + verified + embedding hợp lệ + có ít nhất một criteria skill.
- [x] GitHub Actions `Validate PTTK` đã parse và validate `openapi.yaml` bằng `openapi-spec-validator` — PASS.

## E. Matching / AI

- [x] `hybrid-v1` có công thức Overall xác định và thang 0–100.
- [x] Skill/Semantic/Experience score có quy tắc rõ ràng.
- [x] Embedding CV/JD yêu cầu cùng model/version.
- [x] BM25 trong `hybrid-v1` chỉ diagnostic/retrieval/experiment, không âm thầm blend vào Final Score.
- [x] LLM/XAI chỉ explanation/recommendation, không sửa deterministic scores.
- [x] AI pipeline có FAILED/error path và Human-in-the-loop.

## F. Traceability

- [x] Mọi UC có FR liên quan.
- [x] Các route nghiệp vụ chính truy được về UC/support requirement.
- [x] Mọi bảng chính có chức năng sử dụng hợp lệ.
- [x] Không giữ field legacy chỉ vì code/database cũ.
- [x] Advanced features được đánh dấu, không coi là đã code.
- [x] Traceability đã bổ sung `/skills`, `/matching` history và invariant verified JD criteria.

## G. Review & Database deployment

- [ ] User duyệt/merge Pull Request PTTK.
- [ ] Xác nhận lần cuối DB cũ không có dữ liệu cần giữ.
- [ ] Reset DB và chạy `schema.sql` mới.
- [ ] Verify extensions, 10 tables, FK, CHECK constraints, indexes, HNSW và smoke-test constraint.

## Kết luận gate

Phần **thiết kế nội bộ** đạt trạng thái `DESIGN_LOCKED_FOR_REVIEW`.

CI đã xác nhận:

1. OpenAPI parse/validate PASS.
2. Toàn bộ PlantUML syntax PASS.
3. Static schema sanity PASS.

Chưa chuyển sang `IMPLEMENTATION_READY` cho đến khi:

1. Pull Request được user review/merge;
2. database cũ được reset;
3. `schema.sql` chạy thành công trên PostgreSQL 18 + pgvector và verification runtime PASS.

Sau ba điều kiện trên mới bắt đầu venv -> database connection -> SQLAlchemy ORM -> Auth.
