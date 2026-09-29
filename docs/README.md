# PHÂN TÍCH & THIẾT KẾ HỆ THỐNG — WebCVUngVienTuyenDung

Đây là bộ PTTK được rebuild từ đầu dựa trên đề cương `dc.docx` và mục tiêu kỹ thuật AI/NLP của dự án.

## Thứ tự đọc

1. `PTTK_MASTER.md` — quyết định thiết kế đã khóa.
2. `00_requirements/` — scope, functional/non-functional requirements, actor/ownership, business rules, state model.
3. `01_bfd/` — phân rã chức năng.
4. `02_usecase/` — Use Case overview, phân hệ và đặc tả.
5. `03_activity/` — luồng nghiệp vụ chi tiết.
6. `04_sequence/` — tương tác Frontend/API/Service/DB/AI worker.
7. `05_database/` — CDM/LDM/PDM/ERD/Data Dictionary/schema.sql.
8. `06_architecture/` — kiến trúc hệ thống, component, AI pipeline, matching algorithm.
9. `07_api/` — API Contract + OpenAPI 3.0.3.
10. `08_traceability/` — ma trận truy vết yêu cầu -> API -> database.
11. `09_delivery/` — kế hoạch reset DB và gate trước khi code.

## Nguyên tắc

- Không dùng database thực nghiệm cũ làm chuẩn thiết kế.
- `docs/05_database/schema.sql` là PDM executable sau khi PTTK được review.
- Không code route/field/table ngoài tài liệu mà không cập nhật PTTK trước.
- Tính năng được đánh dấu `[Advanced]` có thể triển khai sau MVP; không được báo cáo là hoàn thành khi chưa code.

## Mục tiêu đồ án

Sản phẩm phải vẫn là một Web Application hoàn chỉnh ngay cả khi các module AI nâng cao chưa đạt mức tối đa; đồng thời thiết kế phải cho phép tích hợp NLP/embedding/skill matching/Skill Gap đúng mục tiêu nghiên cứu.
