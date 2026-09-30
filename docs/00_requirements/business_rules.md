# 02. BUSINESS RULES

Các quy tắc dưới đây là chuẩn nghiệp vụ dùng để kiểm tra Use Case, Activity, API và database.

## Tài khoản và phân quyền

- **BR-AUTH-01**: Email tài khoản là duy nhất, không phân biệt hoa/thường khi so sánh logic.
- **BR-AUTH-02**: Người dùng tự đăng ký chỉ được chọn `CANDIDATE` hoặc `HR`; `ADMIN` chỉ được tạo/gán qua quản trị.
- **BR-AUTH-03**: Tài khoản `is_active=false` không được đăng nhập hoặc gọi API yêu cầu xác thực.
- **BR-AUTH-04**: Mật khẩu không lưu dạng rõ; backend chỉ lưu password hash.
- **BR-AUTH-05**: Admin không được tự khóa hoặc tự hạ quyền chính mình qua API quản trị thông thường.
- **BR-AUTH-06**: Nếu triển khai Refresh Token Rotation, refresh token phải do backend đặt trong HttpOnly Cookie; frontend không đọc token này bằng JavaScript.

## CV

- **BR-CV-01**: Hệ thống chấp nhận CV định dạng PDF/DOCX; giới hạn mặc định 5 MB/file.
- **BR-CV-02**: Backend phải kiểm tra loại file thực tế, không chỉ tin phần mở rộng do client gửi.
- **BR-CV-03**: Trạng thái phân tích CV chỉ thuộc `PENDING`, `PROCESSING`, `PARSED`, `FAILED`.
- **BR-CV-04**: Matching chỉ sử dụng CV có `parsing_status=PARSED` và chưa bị xóa mềm.
- **BR-CV-05**: Dữ liệu nghề nghiệp bóc từ CV thuộc về **một resume cụ thể**, không ghi đè profile tài khoản trong `users`.
- **BR-CV-06**: Mỗi resume có tối đa một `candidate_profile` snapshot.
- **BR-CV-07**: Người có quyền được chỉnh dữ liệu bóc tách; sau khi chỉnh phải đánh dấu `is_manually_edited=true`.
- **BR-CV-08**: Xóa CV mặc định là xóa mềm để tránh phá dữ liệu audit trong quá trình đồ án; file vật lý có thể được cleanup theo chính sách riêng.
- **BR-CV-09**: Candidate/HR chỉ thấy CV thuộc kho của chính mình, trừ Admin.

## Skill Taxonomy

- **BR-SKILL-01**: `skills.normalized_name` là duy nhất và dùng làm khóa chuẩn hóa logic.
- **BR-SKILL-02**: Mỗi skill được phân loại `HARD` hoặc `SOFT`; `category` có thể chi tiết hơn như Language, Framework, Database, DevOps, Communication.
- **BR-SKILL-03**: Một resume không chứa trùng cùng `skill_id`; một JD không chứa trùng cùng `skill_id`.

## JD

- **BR-JOB-01**: JD chỉ có business status `DRAFT`, `ACTIVE`, `CLOSED`.
- **BR-JOB-02**: Trạng thái NLP của JD tách khỏi business status và chỉ thuộc `PENDING`, `PROCESSING`, `PARSED`, `FAILED`.
- **BR-JOB-03**: Candidate chỉ xem/match JD `ACTIVE`, chưa xóa mềm.
- **BR-JOB-04**: HR chỉ sửa/xóa/cấu hình JD do mình sở hữu; Admin có quyền quản trị.
- **BR-JOB-05**: Skill của JD có importance `MANDATORY` hoặc `OPTIONAL`.
- **BR-JOB-06**: Khi `raw_content` thay đổi, kết quả bóc tách cũ không còn được coi là đã xác minh. Hệ thống bắt buộc đưa JD về `status=DRAFT`, đặt `parsing_status=PENDING`, `is_criteria_verified=false`, vô hiệu embedding/parsed timestamp cũ rồi mới phân tích lại. Quy tắc này tránh để một JD đang `ACTIVE` tồn tại với criteria/embedding đã lỗi thời.
- **BR-JOB-07**: `w_skill + w_semantic + w_experience = 1.00`; mỗi trọng số nằm trong [0,1].
- **BR-JOB-08**: JD chỉ được chuyển sang `ACTIVE` hoặc dùng để matching khi `parsing_status=PARSED`, `is_criteria_verified=true`, chưa bị xóa mềm, có embedding hợp lệ và có ít nhất một `job_skill` hợp lệ.

## Matching

- **BR-MATCH-01**: Match chỉ được tạo khi CV `PARSED`, JD `PARSED`, criteria JD đã được xác minh, JD có ít nhất một `job_skill`, tài nguyên chưa xóa, embedding CV/JD hợp lệ và caller có quyền.
- **BR-MATCH-02**: Trạng thái Match: `PENDING`, `PROCESSING`, `COMPLETED`, `FAILED`.
- **BR-MATCH-03**: Điểm thành phần và điểm tổng được phép NULL khi task chưa hoàn tất; khi `COMPLETED` thì tất cả điểm bắt buộc có giá trị 0–100.
- **BR-MATCH-04**: Mỗi cặp `(job_id, resume_id)` chỉ có một kết quả hiện hành trong phạm vi thiết kế MVP; chạy lại cập nhật/recalculate kết quả đó.
- **BR-MATCH-05**: `Skill Score` dựa trên mức đáp ứng các skill chuẩn hóa; kỹ năng bắt buộc có trọng số lớn hơn kỹ năng ưu tiên.
- **BR-MATCH-06**: `Semantic Score` dùng embedding đa ngôn ngữ và Cosine Similarity; BM25 có thể dùng làm tín hiệu lexical bổ sung nhưng phải được chuẩn hóa trước khi kết hợp.
- **BR-MATCH-07**: `Experience Score`: nếu yêu cầu <= 0 thì đạt 100%; nếu kinh nghiệm ứng viên >= yêu cầu thì đạt 100%; ngược lại lấy tỷ lệ ứng viên/yêu cầu.
- **BR-MATCH-08**: `Overall Score = w_skill*Skill + w_semantic*Semantic + w_experience*Experience` trên cùng thang điểm.
- **BR-MATCH-09**: Candidate chỉ xem Match gắn với CV mình sở hữu; HR chỉ xem Match gắn với JD mình sở hữu; Admin toàn quyền.
- **BR-MATCH-10**: Leaderboard chỉ dành cho HR sở hữu JD hoặc Admin; Candidate không xem danh sách xếp hạng ứng viên khác.

## AI/NLP và giải thích

- **BR-AI-01**: AI/NLP có thể sai; dữ liệu bóc tách phải hỗ trợ rà soát thủ công.
- **BR-AI-02**: LLM/XAI nếu dùng chỉ tạo giải thích/gợi ý từ dữ liệu/điểm đã có, không tự thay đổi điểm deterministic của Matching Engine.
- **BR-AI-03**: Khi AI pipeline lỗi, hệ thống phải ghi trạng thái `FAILED` và error message thay vì tạo dữ liệu giả.
- **BR-AI-04**: Vector embedding của CV/JD dùng cùng model/phiên bản và cùng số chiều trong một lần triển khai.

## Soft delete và dữ liệu

- **BR-DATA-01**: Query nghiệp vụ mặc định loại tài nguyên `is_deleted=true`.
- **BR-DATA-02**: Soft delete JD/CV không làm mất dữ liệu liên quan ngay lập tức.
- **BR-DATA-03**: Timestamp lưu dưới dạng timezone-aware (`TIMESTAMPTZ`).
