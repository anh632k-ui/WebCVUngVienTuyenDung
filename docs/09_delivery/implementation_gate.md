# IMPLEMENTATION GATE — ĐIỀU KIỆN CHO PHÉP BẮT ĐẦU CODE

Không chuyển sang ORM/Auth/CRUD cho đến khi tất cả mục dưới đây đạt.

## A. Requirements

- [ ] Scope/MVP/Advanced rõ ràng.
- [ ] Actor Candidate/HR/Admin không chồng quyền mâu thuẫn.
- [ ] Ownership CV/JD/Match được định nghĩa.
- [ ] Business rules có mã BR và không mâu thuẫn API/schema.
- [ ] State model khớp CHECK constraint.

## B. UML

- [ ] BFD phản ánh đủ các phân hệ chính.
- [ ] Use Case overview và phân hệ dùng cùng UC ID.
- [ ] Đặc tả Use Case có precondition/postcondition/exception cho luồng quan trọng.
- [ ] Activity không tham chiếu endpoint/table/field không tồn tại.
- [ ] Sequence không mô tả component trái kiến trúc tổng thể.
- [ ] PlantUML không còn lỗi cú pháp kiểu `@endumlx`.

## C. Database

- [ ] CDM -> LDM -> PDM -> ERD cùng cardinality logic.
- [ ] `schema.sql` có đúng 10 bảng canonical.
- [ ] Data Dictionary khớp tên field/enum với schema.
- [ ] `match_results` cho phép score NULL khi PENDING/PROCESSING.
- [ ] `candidate_profiles` tồn tại và là 1:1 với resume.
- [ ] `job_descriptions` có parsing state tách business status.
- [ ] HNSW index dùng `vector_cosine_ops` trên 2 embedding.

## D. API

- [ ] API Contract và OpenAPI có cùng route set.
- [ ] Matching chỉ có `/matching/calculate` cho single/batch.
- [ ] API dùng `owner_user_id`, `importance`, `skill_kind` đúng naming canonical.
- [ ] Refresh token HttpOnly do backend Set-Cookie.
- [ ] Candidate không được gọi leaderboard.
- [ ] HR owner check áp dụng cho JD/leaderboard/weights/criteria.

## E. Traceability

- [ ] Mọi UC có FR liên quan.
- [ ] Mọi route nghiệp vụ chính truy được về UC.
- [ ] Mọi bảng chính có ít nhất một chức năng sử dụng hợp lệ.
- [ ] Không có field PDM chỉ xuất hiện vì code cũ.

## F. Database deployment

- [ ] User duyệt PR PTTK.
- [ ] Xác nhận DB cũ không cần giữ dữ liệu.
- [ ] Reset DB và chạy `schema.sql` mới.
- [ ] Verify extension/table/FK/check/index.

Khi toàn bộ checklist được audit đạt, trạng thái PTTK chuyển từ `DESIGN_REBUILD` sang `DESIGN_LOCKED`, sau đó mới code.
