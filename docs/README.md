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
11. `09_delivery/` — audit, kế hoạch reset DB và gate trước khi code.

## Nguyên tắc

- Không dùng database thực nghiệm cũ làm chuẩn thiết kế.
- `docs/05_database/schema.sql` là PDM executable sau khi PTTK được review.
- Không code route/field/table ngoài tài liệu mà không cập nhật PTTK trước.
- Tính năng được đánh dấu `[Advanced]` có thể triển khai sau MVP; không được expose trong OpenAPI MVP hoặc báo cáo là hoàn thành khi chưa code.
- Candidate self-match với JD của HR là private trong MVP; HR chỉ xem Match/Leaderboard khi cả JD và CV đều thuộc scope HR.
- `GET /matching` là danh sách **kết quả hiện hành**, không phải lịch sử nhiều attempt.
- Mọi thay đổi làm score cũ stale phải invalidate Match về `PENDING` và clear toàn bộ score/evidence/timestamp cũ trước khi dùng lại.
- `QUEUED` không phải MatchStatus canonical.
- Email được normalize lowercase ở application và enforce case-insensitive uniqueness trong PostgreSQL.

## Mục tiêu đồ án

Sản phẩm phải vẫn là một Web Application hoàn chỉnh ngay cả khi các module AI nâng cao chưa đạt mức tối đa; đồng thời thiết kế phải cho phép tích hợp NLP/embedding/skill matching/Skill Gap đúng mục tiêu nghiên cứu.

## Trạng thái trước implementation

PTTK chỉ được coi `IMPLEMENTATION_READY` sau khi:

1. GitHub Actions `Validate PTTK` PASS ở HEAD cuối cùng;
2. PR được user review/merge;
3. database `webcv_ungvien` được reset;
4. canonical `schema.sql` chạy và smoke-test thành công trên PostgreSQL 18 + pgvector.
