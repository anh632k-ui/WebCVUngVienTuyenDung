# ĐẶC TẢ USE CASE CHI TIẾT

Tài liệu này là đặc tả chuẩn cho các Use Case trong `usecase_overview.puml`. Các endpoint nêu ở đây phải khớp với API Contract/OpenAPI ở giai đoạn cuối.

---

## UC01 — Đăng ký tài khoản

- **Actor chính:** Guest.
- **Tiền điều kiện:** Chưa cần đăng nhập.
- **Hậu điều kiện thành công:** Tạo user `is_active=true`, role là `CANDIDATE` hoặc `HR`.
- **Luồng chính:**
  1. Guest nhập email, mật khẩu, họ tên, số điện thoại tùy chọn và role.
  2. Frontend validation cơ bản.
  3. Backend validation lại toàn bộ dữ liệu.
  4. Kiểm tra email chưa tồn tại.
  5. Hash mật khẩu.
  6. Tạo user.
  7. Trả `201 Created`.
- **Ngoại lệ:** Email trùng `409`; dữ liệu sai `422`.
- **API:** `POST /api/v1/auth/register`.

## UC02 — Đăng nhập

- **Actor chính:** Guest.
- **Tiền điều kiện:** Có tài khoản active.
- **Hậu điều kiện:** Cấp Access Token; nếu bật refresh session thì backend set HttpOnly Cookie.
- **Luồng chính:** nhập email/password -> xác minh hash -> kiểm `is_active` -> phát token -> trả profile tối thiểu.
- **Ngoại lệ:** Sai credential `401`; tài khoản khóa `403`.
- **API:** `POST /api/v1/auth/login`.

## UC03 — Quản lý phiên Refresh/Logout

- **Actor chính:** Authenticated User.
- **Luồng Refresh:** client gọi refresh khi access token hết hạn -> backend đọc HttpOnly Cookie -> kiểm session/revocation -> rotate -> set cookie mới -> trả access token mới.
- **Luồng Logout:** revoke refresh session hiện tại -> clear cookie -> `204`.
- **Ngoại lệ:** Refresh token hết hạn/revoked/replay -> `401`.
- **API:** `POST /api/v1/auth/refresh`, `POST /api/v1/auth/logout`.
- **Ghi chú:** Nếu module refresh chưa triển khai trong MVP đầu tiên, Access Token login vẫn hoạt động; không được giả lập HttpOnly Cookie ở frontend.

## UC04 — Quản lý profile tài khoản

- **Actor chính:** Candidate/HR/Admin.
- **Tiền điều kiện:** Authenticated.
- **Luồng chính:** lấy `/users/me` -> hiển thị -> user sửa họ tên/SĐT -> backend validate -> cập nhật chính user hiện tại.
- **API:** `GET /api/v1/users/me`, `PUT /api/v1/users/me`.
- **Bảo mật:** Không nhận `user_id` từ body để xác định user cần cập nhật.

## UC05 — Đổi mật khẩu

- **Actor chính:** Candidate/HR/Admin.
- **Tiền điều kiện:** Authenticated.
- **Luồng chính:** nhập mật khẩu hiện tại + mới -> verify current hash -> validate password mới -> hash -> update -> revoke refresh sessions -> yêu cầu đăng nhập lại tùy UI.
- **Ngoại lệ:** mật khẩu hiện tại sai `400`; validation `422`.
- **API:** `PUT /api/v1/auth/change-password`.

## UC06 — Quản trị người dùng

- **Actor chính:** Admin.
- **Tiền điều kiện:** Role ADMIN.
- **Luồng chính:** xem/lọc user -> chọn user -> khóa/mở khóa hoặc đổi role -> backend kiểm không phải self-lock/self-demote -> cập nhật.
- **Ngoại lệ:** không phải Admin `403`; thao tác cấm lên chính mình `400`.
- **API:** `GET /api/v1/admin/users`, `PATCH /api/v1/admin/users/{id}`.

---

## UC07 — Upload CV

- **Actor chính:** Candidate hoặc HR.
- **Tiền điều kiện:** Authenticated.
- **Hậu điều kiện:** File được lưu; tạo `resumes` ở `PENDING`; tác vụ parse được kích hoạt.
- **Luồng chính:**
  1. User chọn PDF/DOCX.
  2. Frontend kiểm sơ bộ <= 5 MB.
  3. Backend kiểm size, MIME/Magic Bytes.
  4. Tạo `storage_key` an toàn.
  5. Lưu file.
  6. INSERT resume với `owner_user_id=current_user.id`.
  7. Queue parse hoặc gọi background mechanism.
  8. Trả `202 Accepted`.
- **Ngoại lệ:** quá dung lượng `413`; sai MIME `415`; file không thể lưu `500` và rollback metadata phù hợp.
- **API:** `POST /api/v1/resumes/upload`.

## UC08 — Phân tích CV tự động

- **Actor nghiệp vụ:** Không có actor người dùng trực tiếp; đây là Use Case được UC07 include.
- **Tiền điều kiện:** Resume `PENDING`, file tồn tại.
- **Hậu điều kiện:** `PARSED` và dữ liệu cấu trúc/embedding được lưu, hoặc `FAILED` với error message.
- **Luồng chính:** PROCESSING -> extract text -> preprocessing -> entity extraction -> section parsing -> skill normalization -> embedding -> transaction lưu candidate profile/skills/experience/education -> PARSED.
- **Nhánh OCR:** Nếu PDF scan/text layer không đủ, OCR fallback nếu module được bật.
- **Không được:** Tạo dữ liệu bịa khi parser không đủ bằng chứng.

## UC09 — Rà soát/chỉnh dữ liệu CV

- **Actor chính:** Owner Candidate/HR hoặc Admin.
- **Tiền điều kiện:** Resume `PARSED`, caller có quyền.
- **Luồng chính:** GET detail -> hiển thị file + structured data -> user tra cứu taxonomy khi cần -> sửa -> backend validate -> tái sinh embedding từ dữ liệu canonical đã chỉnh -> transaction replace/update child rows + embedding -> set `is_manually_edited=true`.
- **API chính:** `GET /api/v1/resumes/{id}`, `PUT /api/v1/resumes/{id}/parsed-data`.
- **API hỗ trợ:** `GET /api/v1/skills`.

## UC10 — Quản lý kho CV

- **Actor chính:** Candidate, HR; Admin có quyền giám sát.
- **Luồng chính:** list/pagination/filter -> xem detail/status -> download file -> soft delete khi yêu cầu.
- **Ownership:** Candidate/HR chỉ thấy CV có `owner_user_id=current_user.id`; Admin toàn bộ.
- **API:** `GET /api/v1/resumes`, `GET /api/v1/resumes/{id}`, `GET /api/v1/resumes/{id}/status`, `GET /api/v1/resumes/{id}/download`, `DELETE /api/v1/resumes/{id}`.

---

## UC11 — Quản lý JD

- **Actor chính:** HR; Admin có quyền quản trị.
- **Tiền điều kiện:** HR/Admin authenticated.
- **Luồng Create:** nhập metadata + raw content + weights -> validate -> tạo JD `DRAFT`, `parsing_status=PENDING` -> kích hoạt UC12.
- **Luồng Update:** HR sửa JD của mình; chỉ khi `raw_content` đổi mới kích hoạt lại UC12, đồng thời đưa JD về `DRAFT`, invalidate criteria/embedding/parsed timestamp cũ.
- **Luồng Status:** DRAFT/ACTIVE/CLOSED; chỉ ACTIVE khi JD đã PARSED, criteria verified và chưa xóa.
- **Luồng Delete:** soft delete.
- **API:** `POST /api/v1/jobs`, `GET /api/v1/jobs/{id}`, `PUT /api/v1/jobs/{id}`, `PATCH /api/v1/jobs/{id}/status`, `DELETE /api/v1/jobs/{id}`.

## UC12 — Phân tích JD tự động

- **Actor nghiệp vụ:** Không có actor người dùng trực tiếp; mở rộng UC11 khi tạo JD hoặc thay `raw_content`.
- **Tiền điều kiện:** JD chưa xóa, `parsing_status=PENDING`.
- **Luồng chính:** PROCESSING -> normalize/section JD -> extract min experience/education -> extract skills -> normalize taxonomy -> classify MANDATORY/OPTIONAL -> generate embedding -> save `job_skills` + JD parsed metadata -> PARSED.
- **Ngoại lệ:** lỗi -> FAILED + `parsing_error_message`.

## UC13 — Rà soát criteria JD

- **Actor chính:** HR owner hoặc Admin.
- **Tiền điều kiện:** JD `PARSED`.
- **Luồng chính:** load criteria -> tra cứu taxonomy khi cần -> HR thêm/xóa skill, đổi MANDATORY/OPTIONAL, sửa min years/education -> validate tối thiểu một skill và không trùng `skill_id` -> transaction save -> `is_criteria_verified=true`.
- **API chính:** `GET /api/v1/jobs/{id}/criteria`, `PUT /api/v1/jobs/{id}/criteria`.
- **API hỗ trợ:** `GET /api/v1/skills`.

## UC14 — Xem JD đang tuyển

- **Actor chính:** Candidate; có thể mở public list tùy UI nhưng API hiện yêu cầu authenticated.
- **Luồng chính:** danh sách JD `ACTIVE` và chưa xóa -> search/filter -> xem detail.
- **API:** `GET /api/v1/jobs`, `GET /api/v1/jobs/{id}`.

---

## UC15 — Tính mức độ tương thích CV–JD

- **Actor chính:** Candidate hoặc HR; Admin có quyền vận hành.
- **Tiền điều kiện:** caller có quyền; CV `PARSED`; JD `PARSED`; `is_criteria_verified=true`; JD có ít nhất một `job_skill`; embedding CV/JD hợp lệ; tài nguyên chưa xóa; Candidate chỉ match JD ACTIVE.
- **Luồng Candidate:** gửi 1 resume của mình + 1 JD ACTIVE.
- **Luồng HR:** gửi 1 JD mình sở hữu + một hoặc nhiều resume mình có quyền.
- **Xử lý:** tạo/upsert match `PENDING` -> worker PROCESSING -> Skill Score -> Semantic/Text Score -> Experience Score -> Overall Score -> matched/missing skills -> COMPLETED.
- **API thống nhất:** `POST /api/v1/matching/calculate` với `resume_ids[]`; không tách `/single-match` và `/batch-match`.
- **Response:** `202 Accepted` với danh sách `match_ids`.

## UC16 — Xem lịch sử, kết quả & Skill Gap

- **Actor chính:** Candidate owner CV, HR owner JD, Admin.
- **Tiền điều kiện:** Authenticated; khi xem một Match cụ thể, Match phải tồn tại và caller có quyền.
- **Luồng lịch sử:** GET danh sách matching theo ownership/scope -> filter theo JD/CV/status -> chọn một kết quả.
- **Luồng chi tiết:** GET match -> nếu PENDING/PROCESSING hiển thị trạng thái; nếu COMPLETED hiển thị overall + component scores + matched/missing skills.
- **Luồng Skill Gap:** với Match COMPLETED, hiển thị criticality/gap type/recommendation; LLM/XAI nếu bật chỉ bổ sung explanation/recommendation.
- **API:** `GET /api/v1/matching`, `GET /api/v1/matching/{match_id}`, `GET /api/v1/matching/{match_id}/gap-analysis`.

## UC17 — Xem leaderboard

- **Actor chính:** HR owner JD hoặc Admin.
- **Tiền điều kiện:** JD tồn tại, caller có quyền.
- **Luồng:** lọc match COMPLETED của JD -> min_score tùy chọn -> order `overall_score DESC` -> trả ranking + pagination/limit.
- **Candidate:** Không được gọi endpoint leaderboard.
- **API:** `GET /api/v1/jobs/{id}/leaderboard`.

## UC18 — Cấu hình trọng số matching

- **Actor chính:** HR owner JD hoặc Admin.
- **Luồng:** nhập `w_skill`, `w_semantic`, `w_experience` -> validate mỗi số [0,1] và tổng=1 -> update -> tùy chọn kích hoạt recalculate các match đã có.
- **API:** `PUT /api/v1/jobs/{id}/weights`.

## UC19 — Xuất báo cáo [Advanced]

- **Actor chính:** HR owner JD hoặc Admin.
- **Tiền điều kiện:** Có kết quả matching COMPLETED.
- **Luồng:** chọn PDF hoặc Excel -> backend lấy leaderboard/skill gap -> tạo file -> stream về client.
- **API:** `GET /api/v1/jobs/{id}/export?format=pdf|excel`.
- **Ghi chú:** Nếu module chưa triển khai thì không expose route production; đây không phải điều kiện làm hỏng MVP.
