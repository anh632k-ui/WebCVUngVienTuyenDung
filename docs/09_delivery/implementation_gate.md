# IMPLEMENTATION GATE — ĐIỀU KIỆN CHO PHÉP BẮT ĐẦU CODE

Trạng thái sau audit thiết kế trên branch `pttk-sync-v2`.

## A. Requirements

- [x] Scope/MVP/Advanced rõ ràng.
- [x] Actor Candidate/HR/Admin không chồng quyền mâu thuẫn.
- [x] Ownership CV/JD/Match được định nghĩa.
- [x] Business rules có mã BR và đồng bộ với API/schema.
- [x] State model khớp CHECK constraint.

## B. UML

- [x] BFD phản ánh đủ các phân hệ chính.
- [x] Use Case overview và phân hệ dùng cùng UC ID.
- [x] Đặc tả Use Case có precondition/postcondition/exception cho luồng quan trọng.
- [x] Activity không còn tham chiếu endpoint/table/field legacy không tồn tại.
- [x] Sequence dùng cùng component/lifecycle với kiến trúc tổng thể.
- [x] Static source audit không còn lỗi `@endumlx`; các file mới có cặp `@startuml`/`@enduml`.
- [ ] Render toàn bộ PlantUML trên máy phát triển/CI để xác nhận trình render thực tế.

## C. Database

- [x] CDM -> LDM -> PDM -> ERD dùng cùng mô hình 10 bảng/cardinality.
- [x] `schema.sql` có đúng 10 bảng canonical.
- [x] Data Dictionary khớp naming/enum/invariant của schema.
- [x] `match_results` cho phép score NULL khi PENDING/PROCESSING.
- [x] `candidate_profiles` tồn tại và là 1:1 với resume.
- [x] `job_descriptions` có parsing state tách business status.
- [x] JD ACTIVE yêu cầu PARSED + criteria verified.
- [x] HNSW index dùng `vector_cosine_ops` trên 2 embedding.
- [ ] Chạy `schema.sql` thật trên PostgreSQL 18 + pgvector sau khi PR được duyệt.

## D. API

- [x] API Contract và OpenAPI có cùng route set canonical.
- [x] Matching chỉ có `/matching/calculate` cho trigger single/batch.
- [x] Có `GET /matching` để truy hồi lịch sử kết quả theo ownership.
- [x] Có `GET /skills` làm nguồn taxonomy cho Human-in-the-loop.
- [x] API dùng `owner_user_id`, `importance`, `skill_kind` đúng naming canonical.
- [x] Refresh token HttpOnly do backend `Set-Cookie`.
- [x] Candidate không được gọi leaderboard.
- [x] HR owner check áp dụng cho JD/leaderboard/weights/criteria.
- [x] OpenAPI YAML đã qua parse/static local `$ref` check trong quá trình audit.

## E. Traceability

- [x] Mọi UC có FR liên quan.
- [x] Mọi route nghiệp vụ chính truy được về UC/support requirement.
- [x] Mọi bảng chính có chức năng sử dụng hợp lệ.
- [x] Không giữ field legacy chỉ vì code/database cũ.
- [x] Advanced features được đánh dấu, không coi là đã code.

## F. Review & Database deployment

- [ ] User duyệt/merge Pull Request PTTK.
- [ ] Xác nhận lần cuối DB cũ không có dữ liệu cần giữ.
- [ ] Reset DB và chạy `schema.sql` mới.
- [ ] Verify extensions, 10 tables, FK, CHECK constraints, indexes, HNSW.

## Kết luận gate

Phần **thiết kế nội bộ** đạt trạng thái `DESIGN_LOCKED_FOR_REVIEW`. Chưa chuyển sang `IMPLEMENTATION_READY` cho đến khi:

1. Pull Request được user review/merge;
2. PlantUML được render thử;
3. `schema.sql` chạy thành công trên PostgreSQL 18 + pgvector và verification pass.

Sau ba điều kiện trên mới bắt đầu venv -> database connection -> SQLAlchemy ORM -> Auth.
