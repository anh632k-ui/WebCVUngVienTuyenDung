# 04. YÊU CẦU CHỨC NĂNG

## Nhóm A — Xác thực & tài khoản

- **FR-01**: Guest đăng ký tài khoản Candidate hoặc HR bằng email, mật khẩu, họ tên, số điện thoại tùy chọn.
- **FR-02**: Người dùng đăng nhập bằng email/mật khẩu và nhận Access Token.
- **FR-03**: Hệ thống hỗ trợ refresh/logout session nếu cơ chế Refresh Token được bật.
- **FR-04**: Người dùng xem/cập nhật profile tài khoản của chính mình.
- **FR-05**: Người dùng đổi mật khẩu.
- **FR-06**: Admin xem/lọc danh sách user, khóa/mở khóa và thay đổi role với ràng buộc chống tự khóa/tự hạ quyền.

## Nhóm B — CV & NLP

- **FR-07**: Candidate/HR upload CV PDF hoặc DOCX.
- **FR-08**: Hệ thống kiểm tra file, lưu file và tạo record Resume.
- **FR-09**: Hệ thống bóc text, phân đoạn, trích xuất thông tin cá nhân nghề nghiệp, kỹ năng, kinh nghiệm và học vấn.
- **FR-10**: Hệ thống chuẩn hóa skill theo taxonomy và sinh embedding CV.
- **FR-11**: Người có quyền xem chi tiết dữ liệu đã bóc tách.
- **FR-12**: Người có quyền chỉnh sửa dữ liệu AI bóc sai/thiếu; nếu thay đổi ảnh hưởng matching thì các Match hiện hành liên quan phải được invalidate trước khi dùng lại.
- **FR-13**: Candidate/HR quản lý danh sách CV thuộc kho của chính mình: tìm kiếm, lọc, tải file gốc, xóa mềm.
- **FR-14**: Người dùng xem trạng thái phân tích CV.

## Nhóm C — JD & NLP

- **FR-15**: HR tạo JD với tiêu đề, cấp bậc, địa điểm, nội dung và trọng số matching.
- **FR-16**: HR xem/cập nhật/xóa mềm/đóng-mở JD của mình.
- **FR-17**: Candidate xem danh sách/chi tiết JD đang ACTIVE.
- **FR-18**: Hệ thống phân tích JD, trích kinh nghiệm/học vấn/kỹ năng và phân loại MANDATORY/OPTIONAL.
- **FR-19**: Hệ thống sinh embedding JD.
- **FR-20**: HR rà soát, thêm/xóa/chuyển loại skill criteria đã bóc tách và xác nhận criteria trước khi công bố/matching; thay đổi criteria phải invalidate Match hiện hành của JD.
- **FR-21**: HR cấu hình `w_skill`, `w_semantic`, `w_experience` sao cho tổng bằng 1; thay đổi trọng số phải invalidate Match hiện hành trước khi leaderboard tiếp tục sử dụng.

## Nhóm D — Matching & Analytics

- **FR-22**: Candidate yêu cầu matching CV của mình với một JD ACTIVE; kết quả self-match là private đối với Candidate trong MVP.
- **FR-23**: HR yêu cầu matching một hoặc nhiều CV trong kho của mình với JD của mình.
- **FR-24**: Hệ thống tính Skill Score.
- **FR-25**: Hệ thống tính Semantic Score bằng embedding/cosine; BM25 trong `hybrid-v1` chỉ dùng cho diagnostic/retrieval/experiment, không blend vào Final Score.
- **FR-26**: Hệ thống tính Experience Score.
- **FR-27**: Hệ thống tính Overall Score 0–100 theo trọng số JD.
- **FR-28**: Hệ thống lưu matched skills, missing skills và dữ liệu giải thích cơ bản.
- **FR-29**: Candidate xem Match/Skill Gap có CV thuộc mình; HR chỉ xem Match/Skill Gap khi JD thuộc HR và CV cũng thuộc kho HR; Admin toàn quyền.
- **FR-30**: HR xem leaderboard ứng viên cho JD của mình nhưng chỉ gồm CV thuộc kho HR trong MVP.
- **FR-31**: Khi dữ liệu ảnh hưởng scoring thay đổi, hệ thống invalidate kết quả liên quan; tùy thao tác có thể enqueue recalculate ngay hoặc để `PENDING` chờ tính lại.
- **FR-32**: Hệ thống có thể xuất leaderboard/chi tiết ra PDF/Excel nếu module nâng cao được triển khai; chức năng này không thuộc OpenAPI MVP cho tới khi thực sự bật.

## Nhóm E — AI Explainability & thực nghiệm

- **FR-33**: Nếu tích hợp LLM/XAI, hệ thống tạo tóm tắt/gợi ý dựa trên score + matched/missing skills; output này không sửa score cốt lõi.
- **FR-34**: Hệ thống hỗ trợ thu thập kết quả thực nghiệm trên tập CV/JD mẫu để đánh giá MAE, Pearson, NDCG@K, Precision@K khi có ground truth phù hợp.

## Nhóm F — Chức năng hỗ trợ xuyên suốt

- **FR-35**: Người dùng đã xác thực tra cứu Skill Taxonomy để autocomplete/tìm kỹ năng chuẩn khi rà soát CV hoặc criteria JD.
- **FR-36**: Candidate/HR/Admin xem **danh sách kết quả matching hiện hành** theo đúng ownership/scope, có phân trang và lọc theo JD/CV/trạng thái. MVP không lưu lịch sử nhiều lần chạy cho cùng một cặp `(job_id, resume_id)`.
