# F03 – Candidate CV (Frontend/BFF)

Phạm vi: ứng viên quản lý CV của mình qua giao diện /cv và /cv/[id]. Các route /api/candidate/resumes/* chỉ dùng ở Next.js BFF; access token chỉ tồn tại trong cookie HttpOnly của F02. Backend FastAPI là nguồn dữ liệu và nơi kiểm tra ownership cuối cùng.

## Chạy và cấu hình

Sử dụng frontend/.env.local với BACKEND_API_URL là origin FastAPI (không kèm /api/v1). Xem AUTH_SETUP.md. Chạy backend cùng PostgreSQL và worker nếu muốn nhận kết quả parsing thực; chỉ chạy frontend không làm cho parsing tự hoàn tất.

Trang /cv: danh sách có lọc/tìm kiếm/phân trang, upload PDF/DOCX tối đa 5 MiB. Mỗi lần chọn tệp tạo UUID Idempotency-Key và giữ nguyên khi retry upload không rõ kết quả; chọn tệp khác tạo UUID mới. Server kiểm tra file size, extension và Origin, backend kiểm MIME/magic bytes/fingerprint. Response upload 202 không có nghĩa worker đã phân tích xong.

Trang /cv/[id]: trạng thái, thông tin parsed, chỉnh sửa bốn nhóm dữ liệu, tải nguồn và xóa mềm. Form dùng skill_id của taxonomy; chỉ nhập mã có trong danh mục thực tế. Không có OCR mặc định hoặc matching ở F03. Chỉnh sửa parsed-data chỉ hợp lệ sau khi resume đạt PARSED. Download được stream qua BFF không đưa bearer JWT cho browser.

## Test

npm test bao gồm unit + SEO OFF/ON + Auth HTTP + Resume HTTP. Resume HTTP suite chạy Next.js production với mock FastAPI để xác minh routing, API contract và bảo mật cơ bản; không chứng minh tích hợp CSDL hoặc worker thật.

## Cần kiểm tra trên máy có backend thật

- Upload PDF/DOCX đúng magic bytes, quá 5 MiB, filename bất thường; retry cùng Idempotency-Key và phản hồi 409 khi reuse khác fingerprint.
- Pending/processing/parsed/failed của Celery/recovery; revision và cập nhật parsed-data thực sự giữ nguyên ownership.
- Download nhị phân/Content-Disposition, soft-delete và quyền giữa các tài khoản.
- Skill_id taxonomy thực, model embeddings có thể nạp, database/pgvector, mạng và file-storage.
- Responsive/keyboard trên trình duyệt 320/375/768/1024/1440px.

Không gọi mock tests là bằng chứng real FastAPI/PostgreSQL integration.
