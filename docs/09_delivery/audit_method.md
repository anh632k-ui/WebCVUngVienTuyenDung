# AUDIT METHOD — PTTK FINAL REVIEW

Mục tiêu audit là chứng minh rằng bộ PTTK có thể dùng làm baseline để code mà không phải đoán lại nghiệp vụ, route, field hoặc state.

## 1. Audit nguồn yêu cầu

- Đối chiếu `dc.docx` với `scope.md`, functional/non-functional requirements.
- Không biến phần Advanced (OCR/LLM/XAI/export/Redis-Celery tùy giai đoạn) thành điều kiện làm hỏng MVP.
- Giữ đúng định hướng FastAPI + Next.js + PostgreSQL/pgvector + NLP/embedding/matching.

## 2. Audit nghiệp vụ

Kiểm tra chéo:

`Scope -> Actors/Roles/Ownership -> Business Rules -> Functional Requirements -> Use Case`.

Không chấp nhận:
- Actor có quyền ở Use Case nhưng API từ chối hoặc ngược lại.
- Candidate xem leaderboard ứng viên khác.
- HR truy cập CV/JD ngoài ownership MVP.
- JD ACTIVE/matching khi criteria chưa verified.

## 3. Audit luồng

Đối chiếu:

`Use Case -> Activity -> Sequence -> API Contract`.

Mỗi route xuất hiện trong Activity/Sequence phải tồn tại trong API Contract/OpenAPI. Mỗi lifecycle phải dùng enum canonical:

- Resume/Job parsing: `PENDING|PROCESSING|PARSED|FAILED`.
- Job business: `DRAFT|ACTIVE|CLOSED`.
- Match: `PENDING|PROCESSING|COMPLETED|FAILED`.

## 4. Audit dữ liệu

Đối chiếu:

`Business Rules -> CDM -> LDM -> PDM -> Data Dictionary -> schema.sql`.

Kiểm tra tối thiểu:
- đúng 10 bảng canonical;
- PK/FK/cardinality/unique;
- `owner_user_id`, `recruiter_id`;
- `skill_kind` khác `importance`;
- vector(1024) + HNSW cosine indexes;
- scores nullable trước COMPLETED;
- constraints cho state/weights/soft delete/parsed payload.

Invariant liên bảng như “JD có >=1 job_skill” được service/API enforce và phải ghi rõ trong Business Rules/API, không giả vờ là CHECK constraint bảng cha.

## 5. Audit API

Đối chiếu `api_contract.md <-> openapi.yaml`:

- route/method;
- auth/role/ownership;
- request fields + enum;
- status codes;
- preconditions;
- naming khớp schema canonical.

OpenAPI phải parse/validate bằng `openapi-spec-validator`.

## 6. Audit thuật toán AI/NLP

- `hybrid-v1` phải có công thức xác định, thang score 0–100.
- CV/JD embeddings phải cùng model/version.
- BM25 trong v1 chỉ diagnostic/retrieval/experiment, không âm thầm thay công thức Overall.
- LLM/XAI không sửa deterministic scores.
- Failure phải ghi state FAILED/error thay vì sinh dữ liệu giả.

## 7. Audit UML

Tất cả `.puml` phải vượt `plantuml -checkonly` trong GitHub Actions. Những sơ đồ quan trọng cần render được trên môi trường phát triển trước khi đưa vào báo cáo.

## 8. Audit traceability

`08_traceability/traceability_matrix.md` phải cho phép truy ngược:

`FR -> UC -> Activity/Sequence -> API -> Database`.

Không được có route/table/field legacy còn sót.

## 9. Audit deployment gate

PTTK chỉ chuyển từ `DESIGN_LOCKED_FOR_REVIEW` sang `IMPLEMENTATION_READY` sau khi:

1. CI PTTK pass;
2. Pull Request được user review/merge;
3. database cũ được reset theo kế hoạch;
4. `schema.sql` chạy thành công trên PostgreSQL 18 + pgvector và verification pass.
