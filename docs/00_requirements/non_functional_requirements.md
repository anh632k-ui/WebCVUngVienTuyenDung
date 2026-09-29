# 03. YÊU CẦU PHI CHỨC NĂNG

Các chỉ tiêu dưới đây là target thiết kế/kiểm thử của đồ án. Chúng cụ thể hóa yêu cầu trong đề cương về giao diện thân thiện, backend tin cậy, bảo mật và hiệu năng ổn định.

## NFR-01 — Bảo mật

- Mật khẩu lưu bằng thuật toán hash hiện đại (ưu tiên Argon2id; bcrypt là phương án tương thích).
- API bảo vệ tài nguyên bằng JWT Bearer Access Token.
- Authorization kiểm ở backend theo role + ownership.
- Không đưa password, JWT secret, database password, API key vào Git.
- Upload phải kiểm kích thước và MIME thực tế.
- Không sử dụng tên file người dùng trực tiếp làm đường dẫn lưu trữ.
- Response lỗi đăng nhập không tiết lộ email có tồn tại hay không.
- CORS chỉ mở cho origin frontend đã cấu hình trong môi trường triển khai.

## NFR-02 — Tính đúng đắn dữ liệu

- Database dùng PK/FK/UNIQUE/CHECK để enforce invariant quan trọng.
- Transaction cho thao tác cập nhật nhiều bảng: chỉnh parsed CV, chỉnh criteria JD.
- Trạng thái asynchronous phải nhất quán với state model.
- Điểm `COMPLETED` bắt buộc nằm trong [0,100].

## NFR-03 — Hiệu năng

Target cho môi trường demo đồ án, không phải SLA production:

- API CRUD thông thường: mục tiêu phản hồi < 1 giây trên máy phát triển với dữ liệu demo.
- Upload endpoint trả `202` sớm, không giữ HTTP request chờ toàn bộ NLP nếu background worker được bật.
- Truy vấn leaderboard có index theo `(job_id, overall_score)`.
- Vector dùng pgvector và HNSW khi dữ liệu đủ lớn để cần approximate search.
- Model embedding được load một lần trên worker/process thay vì load lại mỗi request.

## NFR-04 — Khả năng mở rộng và bảo trì

- Backend phân tầng: Router/API -> Service -> Repository/ORM/AI Pipeline -> Database.
- Pydantic schema không dùng thay ORM model.
- AI pipeline tách thành các thành phần extraction, normalization, embedding, matching.
- Cấu hình môi trường qua `.env`, không hard-code credential.
- API versioning: `/api/v1`.

## NFR-05 — Khả dụng và UX

- Giao diện responsive cơ bản trên desktop/mobile.
- Các tác vụ dài hiển thị trạng thái `PENDING/PROCESSING/PARSED|FAILED` hoặc tương đương.
- Validation lỗi phải hiển thị rõ trường sai.
- Candidate nhìn được lý do điểm số thông qua điểm thành phần, matched/missing skills.

## NFR-06 — AI/NLP

- CV/JD tiếng Việt và tiếng Anh là phạm vi ưu tiên.
- Embedding CV/JD phải dùng cùng model và preprocessing version.
- Dữ liệu AI bóc tách không được mặc định coi là tuyệt đối đúng; phải có Human-in-the-loop.
- LLM output nếu có phải được coi là recommendation/explanation, không phải dữ liệu ground truth.

## NFR-07 — Logging và quan sát lỗi

- Backend ghi log request lỗi, task ID/resource ID và exception; không log mật khẩu/token thô.
- Task NLP/Matching thất bại phải lưu error message kỹ thuật rút gọn vào trường phù hợp và log chi tiết ở server.

## NFR-08 — Kiểm thử

- Unit test cho công thức matching và validation.
- Integration test cho Auth, ownership, upload metadata, CRUD JD, matching lifecycle.
- Test API đối chiếu OpenAPI/API Contract.
- Thực nghiệm AI theo đề cương: MAE/Pearson cho matching score nếu có ground truth; NDCG@K/Precision@K cho ranking nếu dataset đối chứng đủ điều kiện.

## NFR-09 — Tương thích công nghệ

- Backend: Python + FastAPI async.
- Frontend: Next.js App Router + TypeScript + Tailwind CSS.
- Database: PostgreSQL 18 trong môi trường phát triển hiện tại + pgvector.
- Vector dimension mặc định 1024 khi sử dụng `bge-m3`; nếu đổi model phải migration schema/vector dimension đồng bộ.
