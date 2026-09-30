# YÊU CẦU PHI CHỨC NĂNG

## NFR-01 Bảo mật
Argon2id ưu tiên; JWT Bearer; backend enforce role và ownership; không commit thông tin cấu hình nhạy cảm; upload kiểm MIME/magic bytes; safe storage key; CORS theo cấu hình.

## NFR-02 Tính đúng đắn và concurrency
- PK/FK/UNIQUE/CHECK cho invariant quan trọng.
- Transaction cho parsed-data, criteria, weights, revision và invalidation.
- `revision` là version của input/computation request; tăng trước computation mới, worker của đúng expected revision không tăng lại khi terminal commit.
- Background task phải idempotent/retry-safe trước duplicate delivery.
- Parse claim CAS: chỉ `PENDING -> PROCESSING` khi `revision=expected_revision`.
- Parse terminal CAS: chỉ `PROCESSING -> PARSED|FAILED` khi vẫn cùng expected revision.
- Match claim CAS: chỉ `PENDING -> PROCESSING` khi generation, snapshot revisions và linked revisions đều khớp expected.
- Match terminal CAS: chỉ `PROCESSING -> COMPLETED|FAILED` khi các expected values vẫn khớp.
- Conditional update `rowcount=0` => stale/duplicate worker discard.
- FAILED bắt buộc có error; non-FAILED không giữ error cũ.

## NFR-03 Reliability / Recovery
- `POST /resumes/upload` và `POST /jobs` phải idempotent theo `Idempotency-Key` UUID; retry cùng logical request không tạo resource mới.
- DB commit resource trước dispatch parse.
- Immediate dispatcher failure không làm mất resource; trạng thái giữ PENDING.
- Startup/periodic recovery sweep re-dispatch Resume/JD PENDING quá grace window bằng current revision mà không mutate revision.
- Duplicate re-dispatch phải an toàn nhờ claim CAS.
- MVP không tự reset PROCESSING timeout về PENDING vì không có lease token; hardening worker-crash recovery phải thiết kế lease/attempt token riêng.

## NFR-04 Hiệu năng
CRUD mục tiêu <1s trên demo; tác vụ NLP/matching trả nhanh sau persistence; leaderboard có index; vector pgvector/HNSW; model load một lần/process.

## NFR-05 Maintainability
Router -> Service -> ORM/AI; Pydantic tách ORM; cấu hình qua `.env`; `/api/v1`; không ORM `create_all()` để tự phát minh schema. Recovery sweeper và version/CAS guard phải nằm service/infrastructure rõ ràng, không rải logic race-control ở endpoint.

## NFR-06 UX
Responsive cơ bản; hiển thị state; lỗi validation rõ; giải thích score bằng components/evidence. Khi parse dispatcher tạm lỗi sau persistence, UI vẫn nhận resource PENDING và polling status bình thường.

## NFR-07 AI/NLP và reproducibility
- Việt/Anh ưu tiên.
- CV/JD cùng `embedding_model` và `embedding_preprocessing_version`.
- Taxonomy phải có seed/version kiểm soát; pipeline không tự thêm skill lạ.
- Human-in-the-loop.
- Benchmark phải ghi algorithm/model/preprocessing version.

## NFR-08 Logging
Log resource/task/generation/revision, idempotency key hash/redacted identifier và exception phù hợp; không ghi thông tin xác thực dạng thô. Stale/duplicate task được log thay vì ghi FAILED vào generation/revision khác.

## NFR-09 Testing
- Unit matching, experience interval và state transitions.
- Integration Auth/ownership/role-change conflict/JD readiness.
- Duplicate delivery tests: hai CV/JD parse worker cùng expected revision chỉ một claim được.
- Duplicate delivery tests: hai Match worker cùng generation chỉ một claim được; không race FAILED/COMPLETED.
- Concurrency tests: old JD parse task và old Match task không overwrite revision/generation mới.
- Batch test: một resume invalid thì không Match row nào bị mutate.
- Matching dispatch failure -> 503 và retry an toàn.
- Parse dispatch failure sau commit -> resource vẫn PENDING, recovery re-dispatch; retry POST cùng Idempotency-Key không tạo duplicate.
- Schema/seed smoke test.

## NFR-10 Technology
FastAPI async; Next.js App Router/TypeScript/Tailwind; PostgreSQL 18 + pgvector; baseline vector(1024).
