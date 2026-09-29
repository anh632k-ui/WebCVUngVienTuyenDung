# 00. PHẠM VI VÀ MỤC TIÊU HỆ THỐNG

## 1. Căn cứ

Tài liệu này được xây dựng từ đề cương `dc.docx` của đồ án và mục tiêu kỹ thuật đã thống nhất cho dự án `WebCVUngVienTuyenDung`.

Tên đề tài đăng ký trong đề cương:

**Xây dựng website phân tích hồ sơ ứng viên và hỗ trợ đánh giá mức độ tương thích công việc**.

Mục tiêu kỹ thuật của sản phẩm:

**Xây dựng hệ thống phân tích hồ sơ nghề nghiệp và đánh giá mức độ tương thích công việc ứng dụng Trí tuệ nhân tạo và Xử lý ngôn ngữ tự nhiên.**

Hai cách diễn đạt không mâu thuẫn: tên đăng ký mô tả sản phẩm ở mức an toàn theo hướng Web Application; kiến trúc và tính năng bên trong hướng tới NLP/AI cho bài toán CV–JD.

## 2. Bài toán nghiệp vụ

Hệ thống giải quyết hai nhu cầu chính:

1. Ứng viên cần biết CV của mình phù hợp với một vị trí tuyển dụng ở mức nào, đang thiếu kỹ năng gì và cần cải thiện điểm nào.
2. Nhà tuyển dụng cần giảm thời gian đọc/lọc CV thủ công, chuẩn hóa tiêu chí JD và xếp hạng hồ sơ theo mức độ phù hợp.

Hệ thống không thay thế quyết định tuyển dụng của con người. Điểm số và gợi ý chỉ có vai trò **hỗ trợ đánh giá**.

## 3. Đối tượng dữ liệu chính

- Tài khoản người dùng.
- Hồ sơ CV/Resume dạng PDF hoặc DOCX.
- Dữ liệu nghề nghiệp được bóc tách từ CV: thông tin liên hệ, kỹ năng, kinh nghiệm, học vấn.
- Bản mô tả công việc (Job Description – JD).
- Từ điển kỹ năng chuẩn (Skill Taxonomy).
- Vector biểu diễn ngữ nghĩa CV/JD.
- Kết quả so khớp và Skill Gap.

## 4. Phạm vi chức năng bắt buộc (MVP)

- Đăng ký, đăng nhập và phân quyền Candidate/HR/Admin.
- Quản lý hồ sơ tài khoản.
- Upload CV PDF/DOCX, lưu file, bóc tách văn bản.
- Trích xuất dữ liệu có cấu trúc từ CV và cho phép người dùng rà soát/chỉnh sửa.
- HR tạo và quản lý JD.
- Tự động trích xuất kỹ năng/yêu cầu từ JD; phân loại bắt buộc và ưu tiên.
- Chuẩn hóa kỹ năng theo Skill Taxonomy.
- Sinh embedding ngữ nghĩa đa ngôn ngữ cho CV và JD.
- So khớp lai giữa CV và JD dựa trên kỹ năng, độ liên quan văn bản/ngữ nghĩa và kinh nghiệm.
- Tính Matching Score theo thang 0–100.
- Phân tích kỹ năng còn thiếu (Skill Gap).
- HR xem bảng xếp hạng hồ sơ theo JD.
- Candidate xem kết quả của CV thuộc chính mình.

## 5. Phạm vi nâng cao

Các nội dung sau được thiết kế để có thể tích hợp nhưng không được phép làm hỏng MVP nếu chưa hoàn thiện:

- Celery + Redis cho xử lý nền.
- OCR fallback cho PDF scan.
- Refresh Token Rotation và thu hồi phiên.
- BM25 kết hợp embedding trong điểm liên quan văn bản.
- LLM/XAI để tạo giải thích và gợi ý cải thiện; LLM không quyết định điểm số cốt lõi.
- Xuất báo cáo PDF/Excel.
- WebSocket/SSE thông báo trạng thái xử lý.

## 6. Ngoài phạm vi phiên bản đồ án

- Tự động ra quyết định tuyển/loại ứng viên.
- Phỏng vấn trực tuyến, lịch phỏng vấn, payroll, hợp đồng lao động.
- Thu phí, thanh toán, ví điện tử.
- Mạng xã hội nghề nghiệp hoàn chỉnh.
- Huấn luyện từ đầu mô hình ngôn ngữ lớn.
- Cam kết điểm AI là quyết định tuyển dụng khách quan tuyệt đối.

## 7. Ngôn ngữ và miền nghiệp vụ

Ưu tiên hồ sơ nghề nghiệp thuộc ngành Công nghệ thông tin và hỗ trợ văn bản tiếng Việt/tiếng Anh. Thiết kế dữ liệu không khóa cứng để có thể mở rộng sang ngành văn phòng.

## 8. Nguyên tắc thiết kế

1. PTTK là nguồn quyết định code và database, không làm ngược lại.
2. Mọi API phải có Use Case hoặc business rule hợp lệ.
3. Mọi field trong API cần lưu bền vững phải truy vết được tới mô hình dữ liệu.
4. Mọi state dùng trong Activity/API phải tồn tại trong schema.
5. Candidate/HR phải bị giới hạn theo quyền sở hữu tài nguyên; Admin là vai trò quản trị.
6. Tính năng AI phải có đường xử lý lỗi và cho phép Human-in-the-loop ở dữ liệu bóc tách.
7. Không dùng LLM để thay thế hoàn toàn matching deterministic có thể giải thích được.
