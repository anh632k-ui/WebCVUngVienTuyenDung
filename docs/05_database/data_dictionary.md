# DATA DICTIONARY — CANONICAL DATABASE

Nguồn vật lý cuối cùng là `schema.sql`. Tài liệu này giải thích ý nghĩa nghiệp vụ của các trường để tránh ORM/API hiểu sai.

## 1. `users`

| Field | Ý nghĩa | Ghi chú |
|---|---|---|
| `id` | ID tài khoản | UUID |
| `email` | Email đăng nhập | app normalize lowercase; DB enforce unique không phân biệt hoa/thường bằng index `LOWER(email)` |
| `password_hash` | Hash mật khẩu | không lưu plaintext |
| `full_name` | Họ tên tài khoản | khác với tên bóc từ CV |
| `phone_number` | SĐT tài khoản | nullable |
| `role` | CANDIDATE/HR/ADMIN | RBAC |
| `is_active` | Có được đăng nhập hay không | false = khóa |
| `created_at`,`updated_at` | Audit timestamp | TIMESTAMPTZ |

## 2. `skills`

| Field | Ý nghĩa |
|---|---|
| `id` | ID skill taxonomy |
| `name` | Tên hiển thị, ví dụ `PostgreSQL` |
| `normalized_name` | Chuỗi chuẩn hóa phục vụ mapping, ví dụ `postgresql` |
| `skill_kind` | `HARD` hoặc `SOFT` |
| `category` | Nhóm chi tiết: Language/Framework/Database/DevOps/Communication... |
| `description` | Mô tả/tri thức ngắn phục vụ recommendation |
| `created_at`,`updated_at` | Audit timestamp |

## 3. `resumes`

| Field | Ý nghĩa |
|---|---|
| `id` | ID CV |
| `owner_user_id` | Candidate/HR sở hữu kho chứa CV |
| `file_name` | Tên file gốc để hiển thị |
| `storage_key` | Key/path nội bộ an toàn, unique |
| `file_size` | Byte, giới hạn <= 5 MB |
| `mime_type` | MIME đã xác minh |
| `parsing_status` | PENDING/PROCESSING/PARSED/FAILED |
| `raw_text` | Text sau extraction/preprocessing |
| `resume_embedding` | pgvector `vector(1024)` |
| `embedding_model` | Model/version đã sinh vector |
| `error_message` | Lỗi parse rút gọn |
| `is_manually_edited` | Đã Human-in-the-loop hay chưa |
| `is_deleted`,`deleted_at` | Soft delete |
| `parsed_at` | Thời điểm parse thành công |

Khi `parsing_status=PARSED`, schema yêu cầu có `raw_text`, `resume_embedding`, `embedding_model` và `parsed_at`. Khi `is_deleted=true`, phải có `deleted_at`.

## 4. `candidate_profiles`

Thông tin nghề nghiệp được bóc từ **một CV**, không phải account profile.

| Field | Ý nghĩa |
|---|---|
| `resume_id` | 1:1 với resumes |
| `full_name` | Tên xuất hiện trong CV |
| `email`,`phone_number` | Contact trong CV |
| `current_title` | Chức danh/vị trí hiện tại |
| `location` | Địa điểm |
| `linkedin_url`,`github_url` | Link nghề nghiệp |
| `professional_summary` | Tóm tắt nghề nghiệp từ CV |

## 5. `resume_skills`

| Field | Ý nghĩa |
|---|---|
| `resume_id` | CV |
| `skill_id` | Skill chuẩn |
| `years_of_experience` | Số năm ước lượng/bóc tách nếu có |
| `proficiency_level` | Mức độ nếu CV thể hiện hoặc user chỉnh |

Unique `(resume_id, skill_id)`.

## 6. `resume_experiences`

| Field | Ý nghĩa |
|---|---|
| `company_name` | Công ty/tổ chức |
| `job_title` | Vị trí |
| `start_date`,`end_date` | Khoảng thời gian |
| `is_current` | Công việc hiện tại; nếu true thì end_date phải NULL |
| `description` | Nội dung công việc/thành tựu |

## 7. `resume_educations`

| Field | Ý nghĩa |
|---|---|
| `institution_name` | Cơ sở đào tạo |
| `degree` | Bằng cấp |
| `field_of_study` | Ngành |
| `start_year`,`graduation_year` | Mốc học tập |
| `gpa` | Điểm nếu có; không ép một thang điểm duy nhất |
| `description` | Thông tin bổ sung |

## 8. `job_descriptions`

| Field | Ý nghĩa |
|---|---|
| `recruiter_id` | HR sở hữu JD |
| `title` | Tên vị trí |
| `job_level` | Intern/Fresher/Junior/Middle/Senior/Lead hoặc text hợp lệ |
| `location` | Địa điểm |
| `raw_content` | JD nguyên bản |
| `min_experience_years` | Kinh nghiệm tối thiểu sau parse/review criteria |
| `education_requirement` | Yêu cầu học vấn dạng text sau parse/review |
| `job_embedding` | vector(1024) |
| `embedding_model` | Model/version embedding |
| `parsing_status` | PENDING/PROCESSING/PARSED/FAILED |
| `parsing_error_message` | Lỗi NLP JD |
| `is_criteria_verified` | HR/Admin đã Human-in-the-loop criteria |
| `w_skill`,`w_semantic`,`w_experience` | Trọng số tổng = 1 |
| `status` | DRAFT/ACTIVE/CLOSED |
| `is_deleted`,`deleted_at` | Soft delete |
| `parsed_at` | Thời điểm parse thành công |

Business status và parsing status là hai state độc lập. Khi `parsing_status=PARSED`, schema yêu cầu `job_embedding`, `embedding_model`, `parsed_at`. JD chỉ được `ACTIVE` khi đã `PARSED` và `is_criteria_verified=true`; service/API còn bắt buộc JD có ít nhất một `job_skill` hợp lệ trước khi ACTIVE hoặc matching. Khi soft-delete phải có `deleted_at`.

## 9. `job_skills`

| Field | Ý nghĩa |
|---|---|
| `job_id` | JD |
| `skill_id` | Skill taxonomy |
| `importance` | MANDATORY/OPTIONAL |
| `min_years_required` | Số năm yêu cầu riêng cho skill nếu xác định được |

Unique `(job_id, skill_id)`. Child-count `>=1` trước ACTIVE/matching là invariant nghiệp vụ do service/API kiểm tra; PostgreSQL CHECK trên bảng cha không thể kiểm trực tiếp số dòng con một cách an toàn.

## 10. `match_results`

MVP lưu **một kết quả hiện hành** cho mỗi cặp `(job_id,resume_id)`, không lưu lịch sử nhiều attempt. Unique `(job_id,resume_id)` là chủ ý thiết kế.

| Field | Ý nghĩa |
|---|---|
| `job_id`,`resume_id` | Cặp được so khớp |
| `overall_score` | Điểm tổng 0–100 |
| `skill_score` | Điểm skill 0–100 |
| `semantic_score` | Điểm semantic/text 0–100 |
| `experience_score` | Điểm kinh nghiệm 0–100 |
| `matched_skills` | Snapshot JSON skill đáp ứng tại thời điểm tính |
| `missing_skills` | Snapshot JSON skill thiếu |
| `gap_analysis_summary` | Tóm tắt deterministic/được xác minh |
| `algorithm_version` | Phiên bản công thức matching |
| `status` | PENDING/PROCESSING/COMPLETED/FAILED |
| `error_message` | Lỗi tính toán nếu FAILED |
| `calculated_at` | Chỉ có khi COMPLETED |

Khi `status=COMPLETED`, bốn score và `calculated_at` bắt buộc có giá trị.

Khi `status!=COMPLETED`, schema bắt buộc stale payload phải được clear: bốn score NULL, `matched_skills=[]`, `missing_skills=[]`, `gap_analysis_summary=NULL`, `calculated_at=NULL`. Điều này ngăn row PENDING/FAILED giữ evidence/score từ lần tính cũ.

### Quyền riêng tư Match

Quyền này không thể enforce bằng CHECK constraint vì cần join nhiều bảng; service phải kiểm:

- Candidate: `resumes.owner_user_id=current_user.id`.
- HR: **đồng thời** `job_descriptions.recruiter_id=current_user.id` và `resumes.owner_user_id=current_user.id`.
- Admin: toàn quyền.

Candidate self-match với JD của HR không tự động cấp quyền đọc Match/CV cho HR trong MVP.

## Quy tắc timestamp

- Tất cả timestamp audit dùng `TIMESTAMPTZ`.
- `updated_at` được cập nhật ở service/ORM trong MVP.
- Không dùng `created_at` thay cho `calculated_at`, `parsed_at` hoặc `deleted_at` vì ý nghĩa nghiệp vụ khác nhau.
