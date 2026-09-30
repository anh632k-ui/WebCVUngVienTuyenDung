# IMPLEMENTATION GATE — ĐIỀU KIỆN CHO PHÉP BẮT ĐẦU CODE

Trạng thái sau audit thiết kế trên branch `pttk-sync-v2`.

## A. Requirements

- [x] Scope/MVP/Advanced rõ ràng.
- [x] Actor Candidate/HR/Admin không chồng quyền mâu thuẫn.
- [x] Ownership CV/JD/Match được định nghĩa.
- [x] Candidate self-match không tự động lộ cho HR trong MVP.
- [x] Business rules có mã BR và đồng bộ với API/schema.
- [x] State model khớp CHECK constraint và service-level invariant.
- [x] Match invalidation khi score stale được định nghĩa đầy đủ.

## B. UML

- [x] BFD phản ánh đủ các phân hệ chính.
- [x] Use Case overview và phân hệ dùng cùng UC ID.
- [x] Đặc tả Use Case có precondition/postcondition/exception cho luồng quan trọng.
- [x] Activity không còn tham chiếu endpoint/table/field legacy không tồn tại.
- [x] Sequence dùng cùng component/lifecycle với kiến trúc tổng thể.
- [x] Matching/Gap/Leaderboard diagram dùng cùng privacy rule Candidate/HR/Admin.
- [x] Không còn lỗi `@endumlx`; các file có cặp `@startuml`/`@enduml`.
- [x] GitHub Actions `Validate PTTK` chạy `plantuml -checkonly` cho toàn bộ `.puml`.

## C. Database

- [x] CDM -> LDM -> PDM -> ERD dùng cùng mô hình 10 bảng/cardinality.
- [x] `schema.sql` có đúng 10 bảng canonical.
- [x] Data Dictionary khớp naming/enum/invariant của schema.
- [x] Email có unique index không phân biệt hoa/thường `LOWER(email)`.
- [x] `match_results` cho phép score NULL khi PENDING/PROCESSING/FAILED.
- [x] non-COMPLETED Match bị CHECK buộc clear stale score/evidence/timestamp.
- [x] `match_results` giữ một current result cho mỗi `(job_id,resume_id)`; không giả vờ là attempt history.
- [x] `candidate_profiles` tồn tại và là 1:1 với resume.
- [x] `job_descriptions` có parsing state tách business status.
- [x] JD ACTIVE yêu cầu PARSED + criteria verified; service/API còn yêu cầu >=1 `job_skill`.
- [x] HNSW index dùng `vector_cosine_ops` trên 2 embedding.
- [ ] Chạy `schema.sql` thật trên PostgreSQL 18 + pgvector sau khi PR được duyệt.

## D. API

- [x] API Contract và OpenAPI dùng cùng route set MVP canonical.
- [x] Matching chỉ có `/matching/calculate` cho trigger single/batch.
- [x] Match trigger trả `status=PENDING`, `total_matches`; không dùng `QUEUED`/`total_jobs`.
- [x] Có `GET /matching` cho danh sách current results theo ownership.
- [x] Có `GET /skills` làm nguồn taxonomy cho Human-in-the-loop.
- [x] API dùng `owner_user_id`, `importance`, `skill_kind` đúng naming canonical.
- [x] Refresh token HttpOnly do backend `Set-Cookie`.
- [x] Candidate không được gọi leaderboard.
- [x] HR Match/Gap/Leaderboard yêu cầu đồng thời JD owner + CV owner trong MVP.
- [x] JD chỉ ACTIVE/matching khi PARSED + verified + embedding hợp lệ + có ít nhất một criteria skill.
- [x] JSON endpoint chính có response schema thực trong OpenAPI.
- [x] Export PDF/Excel Advanced không được expose trong OpenAPI MVP.
- [ ] GitHub Actions `Validate PTTK` PASS ở HEAD cuối cùng sau semantic review vòng 2.

## E. Matching / AI

- [x] `hybrid-v1` có công thức Overall xác định và thang 0–100.
- [x] Skill/Semantic/Experience score có quy tắc rõ ràng.
- [x] Embedding CV/JD yêu cầu cùng model/version.
- [x] BM25 trong `hybrid-v1` chỉ diagnostic/retrieval/experiment, không âm thầm blend vào Final Score.
- [x] LLM/XAI chỉ explanation/recommendation, không sửa deterministic scores.
- [x] AI pipeline có FAILED/error path và Human-in-the-loop.
- [x] criteria/weights/raw JD/parsed CV thay đổi đều invalidate Match cũ trước khi sử dụng lại.

## F. Traceability

- [x] Mọi UC có FR liên quan.
- [x] Các route nghiệp vụ chính truy được về UC/support requirement.
- [x] Mọi bảng chính có chức năng sử dụng hợp lệ.
- [x] Không giữ field legacy chỉ vì code/database cũ.
- [x] Advanced features được đánh dấu, không coi là đã code.
- [x] Traceability có current matching results, privacy, invalidation, case-insensitive email và OpenAPI response schema.

## G. Review & Database deployment

- [ ] User duyệt/merge Pull Request PTTK.
- [ ] Xác nhận lần cuối DB cũ không có dữ liệu cần giữ.
- [ ] Reset DB và chạy `schema.sql` mới.
- [ ] Verify extensions, 10 tables, FK, CHECK constraints, case-insensitive unique email index, indexes, HNSW và smoke-test constraint.

## Kết luận gate

Phần **thiết kế nội bộ** giữ trạng thái `DESIGN_LOCKED_FOR_REVIEW`.

Chưa chuyển sang `IMPLEMENTATION_READY` cho đến khi:

1. CI ở HEAD cuối cùng PASS OpenAPI + response-schema semantic checks + PlantUML + schema/design sanity;
2. Pull Request được user review/merge;
3. database cũ được reset;
4. `schema.sql` chạy thành công trên PostgreSQL 18 + pgvector và verification runtime PASS.

Sau các điều kiện trên mới bắt đầu venv -> database connection -> SQLAlchemy ORM -> Auth.
