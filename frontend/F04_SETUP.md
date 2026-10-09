# F04 – HR quản lý JD

F04 frontend tích hợp qua Next.js BFF, chỉ cho role HR. Route UI /hr/jobs và /hr/jobs/[id]. Cookie chứa JWT do F02 quản lý và không lộ cho browser. Backend FastAPI là nguồn kiểm tra cuối cùng về ownership, revision, trạng thái PARSED, taxonomy và transaction.

- Tạo JD: POST /api/v1/jobs với `Idempotency-Key` UUID, payload title/job_level/location/raw_content và default 0.500/0.300/0.200. Nếu request timeout, không thay UUID khi retry cùng payload.
- CRUD: GET list/detail, PUT fields, DELETE soft-delete, PATCH status DRAFT/ACTIVE/CLOSED. ACTIVE cần PARSED, verified và các điều kiện khác trên backend.
- Criteria: GET/PUT chỉ khi PARSED, cần tối thiểu một skill ID từ taxonomy, importance MANDATORY/OPTIONAL và years NUMERIC(4,1).
- Weights: PUT đủ 3 JSON-number thập phân tối đa 3 chữ số, sum 1.000. Mỗi accepted PUT tăng revision, invalidate mọi linked match (kể cả khi không đổi weights). `recalculate=true` là best-effort publish sau commit, không đảm bảo hoàn thành ngay.

BFF route: /api/hr/jobs (GET/POST), /api/hr/jobs/[id] (GET/PUT/DELETE), /criteria (GET/PUT), /weights (PUT), /status (PATCH).

Test `npm test` bao gồm unit + SEO/Auth/Resume regression + production HTTP mock FastAPI cho JD. Đây không phải real DB/worker integration.

Cần Codex kiểm tra khi có backend/PostgreSQL/worker: parse JD, criteria từ taxonomy thật, xác minh ACTIVE guard, create idempotency replay, concurrency/revision, weights invalidation+recalculate dispatch, soft delete and ownership giữa hai HR, trình duyệt responsive/keyboard.
