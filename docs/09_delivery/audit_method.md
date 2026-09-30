# AUDIT METHOD — PTTK FINAL REVIEW

Mục tiêu audit là chứng minh rằng bộ PTTK có thể dùng làm baseline để code mà không phải đoán lại nghiệp vụ, route, field hoặc state.

## 1. Audit nguồn yêu cầu

- Đối chiếu `dc.docx` với `scope.md`, functional/non-functional requirements.
- Không biến phần Advanced (OCR/LLM/XAI/export/Redis-Celery tùy giai đoạn) thành điều kiện làm hỏng MVP.
- Giữ đúng định hướng FastAPI + Next.js + PostgreSQL/pgvector + NLP/embedding/matching.

## 2. Audit nghiệp vụ & quyền riêng tư

Kiểm tra chéo:

`Scope -> Actors/Roles/Ownership -> Business Rules -> Functional Requirements -> Use Case`.

Không chấp nhận:
- Actor có quyền ở Use Case nhưng API từ chối hoặc ngược lại.
- Candidate xem leaderboard ứng viên khác.
- HR truy cập CV/JD ngoài ownership MVP.
- HR đọc Candidate self-match chỉ vì HR sở hữu JD.
- HR đọc Match nếu chỉ sở hữu một phía; trong MVP phải đồng thời JD và CV thuộc scope HR.
- JD ACTIVE/matching khi criteria chưa verified hoặc chưa có job_skill hợp lệ.

## 3. Audit luồng & invalidation

Đối chiếu:

`Use Case -> Activity -> Sequence -> API Contract`.

Mỗi route MVP xuất hiện trong Activity/Sequence phải tồn tại trong API Contract/OpenAPI. Mỗi lifecycle phải dùng enum canonical:

- Resume/Job parsing: `PENDING|PROCESSING|PARSED|FAILED`.
- Job business: `DRAFT|ACTIVE|CLOSED`.
- Match: `PENDING|PROCESSING|COMPLETED|FAILED`.
- `QUEUED` không phải MatchStatus.

Khi CV/JD/criteria/weights/model/algorithm thay đổi làm score stale, tất cả artefact phải cùng mô tả canonical invalidation: Match về `PENDING`, clear bốn scores, matched/missing evidence, gap summary, error và `calculated_at`. `recalculate=true` chỉ quyết định enqueue ngay.

## 4. Audit dữ liệu

Đối chiếu:

`Business Rules -> CDM -> LDM -> PDM -> Data Dictionary -> schema.sql`.

Kiểm tra tối thiểu:
- đúng 10 bảng canonical;
- PK/FK/cardinality/unique;
- `owner_user_id`, `recruiter_id`;
- email unique không phân biệt hoa/thường bằng index `LOWER(email)`;
- `skill_kind` khác `importance`;
- vector(1024) + HNSW cosine indexes;
- scores nullable trước COMPLETED;
- non-COMPLETED Match không được giữ stale score/evidence/timestamp;
- unique `(job_id,resume_id)` được hiểu là **một current result**, không gọi nhầm là history nhiều attempt;
- constraints cho state/weights/soft delete/parsed payload.

Invariant liên bảng như “JD có >=1 job_skill” hoặc “HR sở hữu đồng thời JD và CV khi đọc Match” được service/API enforce và phải ghi rõ trong Business Rules/API, không giả vờ là CHECK constraint bảng cha.

## 5. Audit API

Đối chiếu `api_contract.md <-> openapi.yaml`:

- route/method;
- auth/role/ownership;
- request fields + enum;
- response schema;
- status codes;
- preconditions;
- naming khớp schema canonical.

Quy tắc thêm:
- JSON success endpoint chính `200/201/202` phải có response schema thực.
- Feature Advanced chưa bật, ví dụ export PDF/Excel, không được expose trong OpenAPI MVP.
- Match trigger trả `PENDING/total_matches`, không `QUEUED/total_jobs`.

OpenAPI phải parse/validate bằng `openapi-spec-validator` và CI semantic checks.

## 6. Audit thuật toán AI/NLP

- `hybrid-v1` phải có công thức xác định, thang score 0–100.
- CV/JD embeddings phải cùng model/version.
- BM25 trong v1 chỉ diagnostic/retrieval/experiment, không âm thầm thay công thức Overall.
- LLM/XAI không sửa deterministic scores.
- Failure phải ghi state FAILED/error thay vì sinh dữ liệu giả.
- Human-in-the-loop làm dữ liệu matching thay đổi phải invalidate Match cũ.

## 7. Audit UML

Tất cả `.puml` phải vượt `plantuml -checkonly` trong GitHub Actions. Những sơ đồ quan trọng cần render được trên môi trường phát triển trước khi đưa vào báo cáo.

## 8. Audit traceability

`08_traceability/traceability_matrix.md` phải cho phép truy ngược:

`FR -> UC -> Activity/Sequence -> API -> Database`.

Không được có route/table/field legacy còn sót. Các rule cross-cutting về privacy, invalidation, case-insensitive email và response schemas phải có dòng traceability riêng.

## 9. Audit deployment gate

PTTK chỉ chuyển từ `DESIGN_LOCKED_FOR_REVIEW` sang `IMPLEMENTATION_READY` sau khi:

1. CI PTTK ở HEAD cuối cùng pass;
2. Pull Request được user review/merge;
3. database cũ được reset theo kế hoạch;
4. `schema.sql` chạy thành công trên PostgreSQL 18 + pgvector và verification pass.
