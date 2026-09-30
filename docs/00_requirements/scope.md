# PHẠM VI VÀ MỤC TIÊU HỆ THỐNG

## 1. Tên đề tài và định hướng
Tên đề tài chính của dự án:

**Xây dựng hệ thống phân tích hồ sơ nghề nghiệp và đánh giá mức độ tương thích công việc ứng dụng Trí tuệ nhân tạo và Xử lý ngôn ngữ tự nhiên.**

Sản phẩm triển khai dưới dạng Web Application; cách triển khai web không làm giảm trọng tâm AI/NLP của đề tài.

## 2. Bài toán
- Candidate cần biết CV phù hợp JD ở mức nào, thiếu skill gì.
- HR cần chuẩn hóa JD và hỗ trợ xếp hạng CV trong talent pool của chính HR.
- Hệ thống là công cụ hỗ trợ, không tự động quyết định tuyển/loại.

## 3. MVP bắt buộc
- Account Candidate/HR/Admin, login JWT Access Token, RBAC/ownership.
- Profile tài khoản.
- Upload PDF/DOCX <=5MB.
- Extract/parse CV + Human-in-the-loop.
- Skill Taxonomy có bootstrap seed.
- HR tạo/quản lý JD; NLP tách criteria MANDATORY/OPTIONAL.
- Embedding đa ngôn ngữ CV/JD, lưu model + preprocessing version.
- Hybrid Matching: Skill + Semantic + Experience.
- Matching Score 0..100, Skill Gap.
- Candidate self-match private.
- HR batch matching và leaderboard chỉ với CV thuộc talent pool của HR.
- Background task semantics có revision/generation guard để stale worker không overwrite dữ liệu mới.

## 4. Advanced
Không được làm hỏng MVP nếu chưa triển khai:
- Celery + Redis production-grade background queue.
- Refresh Token Rotation + HttpOnly session/revoke.
- OCR fallback.
- BM25 blend sau benchmark (hybrid-v2 nếu thay công thức).
- LLM/XAI explanation/recommendation.
- PDF/Excel export.
- WebSocket/SSE notification.

## 5. Ngoài phạm vi
- Tự động quyết định tuyển/loại.
- Interview scheduling/payroll/hợp đồng/thanh toán.
- Full professional social network.
- Train LLM từ đầu.
- `applications`/Candidate nộp CV cho HR trong MVP. Nếu bổ sung phải thiết kế lại privacy/ownership trước.

## 6. Miền dữ liệu
Ưu tiên hồ sơ CNTT, tiếng Việt/Anh; schema có thể mở rộng ngành khác sau.

## 7. Nguyên tắc thiết kế
1. PTTK quyết định code/database.
2. API phải truy vết về requirement/Use Case.
3. State/enum/field dùng trong luồng phải có nguồn canonical.
4. Candidate/HR luôn bị giới hạn ownership; Admin là override quản trị.
5. AI/NLP phải có error path + Human-in-the-loop.
6. Vector chỉ so sánh khi model + preprocessing version tương thích.
7. Background worker phải version-aware; stale task bị discard.
8. Tính năng Advanced không được expose như MVP trước khi bật thật.
