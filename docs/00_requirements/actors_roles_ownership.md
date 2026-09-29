# 01. ACTOR, VAI TRÒ VÀ QUYỀN SỞ HỮU DỮ LIỆU

## 1. Actor nghiệp vụ

### 1.1 Guest
Người chưa đăng nhập.

Quyền:
- Đăng ký tài khoản Candidate hoặc HR.
- Đăng nhập.
- Xem các nội dung công khai nếu giao diện sau này cho phép.

Không có quyền truy cập CV, matching, tiêu chí nội bộ của HR hoặc quản trị hệ thống.

### 1.2 Candidate
Ứng viên/người tìm việc đã đăng nhập.

Quyền:
- Quản lý thông tin tài khoản cá nhân.
- Upload CV của chính mình.
- Xem, tải xuống, chỉnh dữ liệu bóc tách và xóa mềm CV của chính mình.
- Xem JD đang `ACTIVE`.
- Yêu cầu so khớp một CV của chính mình với một JD đang `ACTIVE`.
- Xem kết quả Matching/Skill Gap có CV thuộc chính mình.

Không có quyền:
- Xem kho CV của người khác.
- Xem leaderboard toàn bộ ứng viên của một JD.
- Chỉnh tiêu chí/trọng số JD.
- Quản trị tài khoản khác.

### 1.3 HR
Nhà tuyển dụng đã đăng nhập.

Quyền:
- Quản lý tài khoản cá nhân.
- Tạo/quản lý JD do chính mình sở hữu.
- Rà soát tiêu chí đã bóc tách từ JD.
- Điều chỉnh trọng số matching cho JD của mình.
- Upload CV vào kho tuyển dụng của chính mình nếu HR thu thập CV bên ngoài.
- Quản lý các CV do chính HR upload.
- Chạy matching hàng loạt giữa JD của mình và các CV mà HR có quyền truy cập.
- Xem leaderboard cho JD của mình.
- Xem Skill Gap/chi tiết kết quả thuộc JD của mình.
- Xuất báo cáo nếu module nâng cao được bật.

Không có quyền:
- Sửa JD của HR khác.
- Quản trị role/trạng thái tài khoản toàn hệ thống.

### 1.4 Admin
Quản trị viên hệ thống.

Quyền:
- Xem danh sách tài khoản.
- Khóa/mở khóa tài khoản.
- Thay đổi role theo chính sách quản trị.
- Giám sát dữ liệu hệ thống khi cần hỗ trợ/vận hành.

Ràng buộc:
- Không được tự khóa chính mình.
- Không được tự hạ quyền Admin của chính mình qua API quản trị thông thường.

## 2. Tác nhân kỹ thuật

Celery Worker, Redis, PostgreSQL, pgvector, NLP model, OCR và LLM **không phải actor nghiệp vụ** trong Use Case Diagram vì chúng là thành phần bên trong/được hệ thống sử dụng. Chúng xuất hiện trong Component/Sequence/Activity Diagram.

## 3. Ownership model

### CV
- `resumes.owner_user_id` xác định tài khoản sở hữu kho CV.
- Candidate upload CV: `owner_user_id = candidate.id`.
- HR upload CV vào talent pool: `owner_user_id = hr.id`.
- Admin có thể truy cập theo quyền quản trị.
- Candidate/HR không được lấy một `resume_id` bất kỳ để vượt quyền truy cập.

### JD
- `job_descriptions.recruiter_id` xác định HR sở hữu JD.
- HR chỉ được sửa/xóa/cấu hình JD có `recruiter_id = current_user.id`.
- Candidate chỉ xem JD công khai khi `status = ACTIVE` và `is_deleted = false`.

### Matching
Một Match hợp lệ khi người gọi có quyền với cả phía CV và JD theo từng vai trò:

- Candidate: CV phải thuộc Candidate; JD phải `ACTIVE`.
- HR: JD phải thuộc HR; CV phải thuộc kho HR hoặc thuộc nguồn truy cập hợp lệ được thiết kế sau này.
- Admin: toàn quyền vận hành.

Trong phiên bản đồ án hiện tại chưa thiết kế quy trình nộp đơn (`applications`). Vì vậy HR không mặc nhiên được đọc CV cá nhân của Candidate khác chỉ vì CV tồn tại trong hệ thống.

## 4. Nguyên tắc chống BOLA/IDOR

Mọi endpoint nhận `{id}` phải kiểm tra authorization ở tầng service/dependency, không chỉ ẩn nút trên frontend. Với tài nguyên không được phép lộ sự tồn tại, API có thể trả `404` thay vì xác nhận bằng `403` tùy trường hợp.
