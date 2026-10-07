# Rà soát dự án ngày 07/10/2026

## Kết luận

Match Worker Core đã được đồng bộ với PR #19 và sửa lỗi thứ tự khóa.
Các kiểm tra cục bộ dưới đây đều đạt. Toàn ứng dụng vẫn chưa hoàn thành MVP:
còn thiếu API kích hoạt matching, Match dispatch/recovery, một số API chỉnh sửa
và frontend nghiệp vụ. Không suy ra ứng dụng sẵn sàng triển khai từ số test đạt.

## Phạm vi và căn cứ

- Repo: `anh632k-ui/WebCVUngVienTuyenDung`.
- Nhánh làm việc: `feature/match-worker`.
- Worker ban đầu: `4439a1c0f2174cf9eefd0a163dcd6363174786ab`.
- Đã merge `dev@3f59dd316037dab59c6d227ad20a5c3d63be056b` vào nhánh feature.
- Không merge vào `dev` hoặc `main`, không force push.
- Đối chiếu `PTTK_MASTER.md`, requirements/business rules, schema/seed,
  API contract/OpenAPI, kiến trúc, traceability và mã nguồn hiện tại.
- Rà auth/ownership, upload/idempotency/storage, Resume/Job parsing và recovery,
  criteria/invalidation, Match engine/worker/read/leaderboard, cấu hình CI,
  bản đồ API và khả năng lint/build frontend.

## Lỗi đã sửa

### 1. Deadlock giữa Match terminal và Job criteria update

Worker ban đầu khóa `Resume → Job → MatchResult`. `update_job_criteria()` khóa
Job trước rồi khóa các Match/Resume liên quan. Hai transaction có thể chờ nhau
giữa Job và Resume.

Terminal success và failure hiện dùng chung thứ tự `Job → Resume → MatchResult`.
Mỗi resource được kiểm revision và trạng thái soft-delete sau khi lấy khóa.
Terminal Match UPDATE vẫn kiểm generation, snapshots, algorithm và PROCESSING.
Không đổi schema, seed, công thức scoring hoặc quy tắc revision/generation.

Bổ sung bốn trường hợp PostgreSQL: criteria giữ khóa trước hoặc terminal giữ khóa
trước, kết hợp với COMPLETED hoặc FAILED. Test dùng session độc lập, xác nhận
chờ khóa thật bằng `pg_blocking_pids`, không dùng sleep để đoán thời điểm.
Cả bốn trường hợp xác nhận invalidation mới giữ generation/revision hiện tại
và xóa toàn bộ payload cũ. Failure test cũng từ chối log lỗi terminal CAS.

Kiểm tra khả năng bắt lỗi: tạm phục hồi thứ tự khóa ban đầu làm test mới thất bại
với `DeadlockDetectedError`; sau đó đã phục hồi bản sửa.

### 2. CI bỏ qua integration tests vì không cấu hình PostgreSQL

Backend CI hiện có service PostgreSQL 18 + pgvector, tạo đúng schema và seed
canonical trong database tạm của CI, đặt DATABASE_URL và chạy toàn suite.
Không kết nối database người dùng. Các dependencies queue cũng được cài.

Image dùng `pgvector/pgvector:0.8.6-pg18`, thuộc các tag được tài liệu
[pgvector](https://github.com/pgvector/pgvector/blob/master/README.md#docker) công bố.
Kết quả GitHub Actions của HEAD cuối được kiểm riêng trên PR; bảng dưới là kết
quả chạy cục bộ, không thay cho trạng thái workflow remote.

### 3. Test mặc định cấu hình phụ thuộc môi trường chạy

`Settings(_env_file=None)` vẫn đọc process environment. Khi bật DATABASE_URL cho
integration tests, test defaults cũ thất bại. Test này hiện xóa riêng các biến
Settings trong phạm vi monkeypatch, kiểm mặc định rồi tự khôi phục environment.
Không thay đổi cơ chế cấu hình của ứng dụng.

## Kết quả xác minh

| Kiểm tra | Kết quả |
|---|---|
| Toàn backend với PostgreSQL 18 và pgvector thật | 545 passed, 0 skipped |
| Match worker PostgreSQL và Job criteria PostgreSQL | 22 passed |
| Ruff lint và formatting | Đạt; 128 Python files đã đúng format |
| mypy | Đạt; 83 source files |
| OpenAPI và reliability validator trong workflow PTTK | Đạt |
| Schema/seed và semantic invariants trong workflow PTTK | Đạt |
| PlantUML syntax | 35/35 files đạt |
| Frontend ESLint | Đạt |
| Next.js route type generation và TypeScript | Đạt |
| Next.js production build | Đạt |
| git diff --check | Đạt |

Database test chạy PostgreSQL 18.6 và pgvector 0.8.7, được tạo từ schema/seed
canonical, có đúng 10 bảng và 42 skills. Sau toàn suite không còn test user.
Không dùng ORM create_all hoặc reset database đang dùng của người dùng.

## Phần MVP chưa triển khai

So sánh route/method trong canonical OpenAPI với runtime OpenAPI, chuẩn hóa tên
path parameter: 24/29 nghiệp vụ đã có route. Health là route hỗ trợ thêm.
Con số này chỉ đo sự hiện diện route, không phải tỷ lệ hoàn thành nghiệp vụ.

| Method và route còn thiếu | Ý nghĩa |
|---|---|
| `POST /api/v1/matching/calculate` | Candidate self-match, HR batch validate/prepare/dispatch |
| `PUT /api/v1/resumes/{id}/parsed-data` | Human-in-the-loop CV, revision/embedding/invalidation |
| `GET /api/v1/resumes/{id}/download` | Download file CV theo quyền |
| `PUT /api/v1/jobs/{id}` | Chỉnh nội dung JD, revision/reparse/invalidation |
| `PUT /api/v1/jobs/{id}/weights` | Chỉnh trọng số và invalidate Match |

Match worker chưa được nối vào Celery runtime/dispatcher/recovery. Criteria update
để Match PENDING đúng thiết kế invalidation; chưa có Match dispatcher để xử lý tiếp.
Frontend chỉ có trang scaffold Next.js; chưa có auth, kho CV/JD, matching hoặc
leaderboard nghiệp vụ. Chưa có benchmark matching trên ground truth.

## Giới hạn xác minh và bước tiếp

- Đã kiểm syntax UML; chưa đánh giá khả năng đọc của từng ảnh sơ đồ sau render.
- Test embedding dùng provider/model giả lập. Chưa tải hoặc chạy BGE-M3 thật.
- Celery task/runtime được kiểm bằng automated tests; chưa chạy end-to-end với
  Redis broker và worker thật.
- Chưa kiểm tra deployment production, browser flow hoặc database local của
  người dùng. Rà soát này không phải kiểm định bảo mật toàn diện.
- Recovery PROCESSING sau crash vẫn cần lease/attempt token riêng theo canonical;
  không tự reset PROCESSING về PENDING.

Hai lượt rà Match worker đã thực hiện: (1) implementation → claim/load/compute/
serialize/terminal/races/tests; (2) canonical rules → concurrency/ownership/
database invariants/non-mutation → implementation/tests.

Thứ tự tiếp theo: user review PR Match Worker Core → Match dispatch/recovery →
matching trigger/batch transaction → các API chỉnh sửa còn thiếu → frontend →
end-to-end và benchmark. Giữ các tính năng Advanced ngoài điều kiện hoàn thành MVP.
