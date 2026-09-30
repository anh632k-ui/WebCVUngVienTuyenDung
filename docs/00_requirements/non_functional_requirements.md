# YÊU CẦU PHI CHỨC NĂNG

## NFR-01 Bảo mật
Argon2id ưu tiên; JWT Bearer; backend enforce role và ownership; không commit thông tin cấu hình nhạy cảm; upload kiểm MIME/magic bytes; safe storage key; CORS theo cấu hình.

## NFR-02 Tính đúng đắn và concurrency
- PK/FK/UNIQUE/CHECK cho invariant quan trọng.
- Transaction cho parsed-data, criteria, weights, revision và invalidation.
- Background task phải idempotent/retry-safe.
- Parse worker dùng `expected_revision`.
- Match worker dùng `expected_generation + expected_resume_revision + expected_job_revision`.
- Terminal update phải conditional; stale worker ảnh hưởng 0 row và bị discard.
- FAILED bắt buộc có error; non-FAILED không giữ error cũ.

## NFR-03 Hiệu năng
CRUD mục tiêu <1s trên demo; tác vụ NLP/matching trả 202; leaderboard có index; vector pgvector/HNSW; model load một lần/process.

## NFR-04 Maintainability
Router -> Service -> ORM/AI; Pydantic tách ORM; cấu hình qua `.env`; `/api/v1`; không ORM `create_all()` để tự phát minh schema.

## NFR-05 UX
Responsive cơ bản; hiển thị state; lỗi validation rõ; giải thích score bằng components/evidence.

## NFR-06 AI/NLP và reproducibility
- Việt/Anh ưu tiên.
- CV/JD cùng `embedding_model` và `embedding_preprocessing_version`.
- Taxonomy phải có seed/version kiểm soát; pipeline không tự thêm skill lạ.
- Human-in-the-loop.
- Benchmark phải ghi algorithm/model/preprocessing version.

## NFR-07 Logging
Log resource/task/generation/revision và exception phù hợp; không ghi thông tin xác thực dạng thô. Stale task được log thay vì ghi FAILED vào generation mới.

## NFR-08 Testing
- Unit matching, experience interval và state transitions.
- Integration Auth/ownership/role-change conflict/JD readiness.
- Concurrency tests: old JD parse task và old Match task không overwrite revision/generation mới.
- Batch test: một resume invalid thì không Match row nào bị mutate.
- Dispatch failure -> 503 và retry an toàn.
- Schema/seed smoke test.

## NFR-09 Technology
FastAPI async; Next.js App Router/TypeScript/Tailwind; PostgreSQL 18 + pgvector; baseline vector(1024).
