# F05 – Giao diện Admin quản lý tài khoản

Route protected /admin/users yêu cầu Admin server-side. BFF GET /api/admin/users hỗ trợ page, limit, role, is_active, keyword; PATCH /api/admin/users/{id} chỉ cho is_active và role CANDIDATE/HR. JWT chỉ nằm trong cookie HttpOnly, FastAPI là nguồn phân quyền chính thức. Không hỗ trợ tạo Admin từ đăng ký hoặc gán role ADMIN qua PATCH.

Đổi role Candidate<->HR có thể trả 409 khi target sở hữu Resume/JD chưa soft-delete. Giao diện hiển thị lỗi để admin xử lý theo nghiệp vụ; không tự xóa tài nguyên. Không sửa profile/email hoặc truy cập kho CV/JD riêng tư thông qua màn hình này.

Kiểm thử npm test: unit + production HTTP mock FastAPI + hồi quy. Cần Codex xác minh backend/PostgreSQL thật: quyền Admin, phản hồi 409, self-lock, target role ADMIN, inactive sessions, ownership và audit, responsive/keyboard trước production.
