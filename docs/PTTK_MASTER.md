# PTTK MASTER — WEBCVUNGVIENTUYENDUNG

**Status:** `DESIGN_LOCKED_FOR_REVIEW` — baseline nội bộ đã được đồng bộ; chỉ triển khai code sau khi Pull Request được review và database mới được tạo từ schema canonical.

## 1. Căn cứ phạm vi

Nguồn gốc yêu cầu là đề cương `dc.docx`, trong đó xác định:

- sản phẩm là Web Application;
- backend Python/FastAPI RESTful async;
- frontend ReactJS/Next.js + Tailwind;
- PostgreSQL + pgvector;
- xử lý PDF/DOCX;
- Information Extraction/NLP;
- Sentence Transformers (`bge-m3` hoặc tương đương);
- keyword matching/BM25;
- semantic similarity;
- Matching Score, Skill Gap;
- BFD, Use Case, Sequence Diagram, ERD;
- thực nghiệm MAE/Pearson/NDCG@K/Precision@K khi có ground truth.

Tên đề tài chính thức trong đề cương vẫn được giữ. Thiết kế kỹ thuật hướng tới hệ thống AI/NLP mạnh hơn nhưng không biến LLM thành điều kiện sống còn của MVP.

## 2. Kiến trúc được khóa

```text
Next.js App Router + TypeScript + Tailwind
                  |
              REST / JSON
                  |
               FastAPI
                  |
      Router -> Service -> ORM/AI
             /             \
PostgreSQL 18 + pgvector   File Storage
             \
      Redis/Celery khi background/session features được bật
```

## 3. Actor được khóa

- Guest
- Candidate
- HR
- Admin

Celery Worker, Redis, PostgreSQL và AI model là thành phần kỹ thuật, không phải actor nghiệp vụ.

## 4. Aggregate / bảng được khóa

Database canonical có **10 bảng**:

1. `users`
2. `skills`
3. `resumes`
4. `candidate_profiles`
5. `resume_skills`
6. `resume_experiences`
7. `resume_educations`
8. `job_descriptions`
9. `job_skills`
10. `match_results`

`candidate_profiles` là snapshot nghề nghiệp được bóc từ từng CV và cố ý tách khỏi profile tài khoản trong `users`.

## 5. Naming được khóa

- Resume ownership: `owner_user_id`.
- JD ownership: `recruiter_id`.
- Skill hard/soft taxonomy: `skills.skill_kind = HARD|SOFT`.
- Mức độ bắt buộc trong JD: `job_skills.importance = MANDATORY|OPTIONAL`.
- Không dùng `job_skills.skill_type` để tránh nhầm với hard/soft.
- Embedding: `resume_embedding`, `job_embedding`, hiện `vector(1024)`.
- Model/version embedding được lưu ở `embedding_model` để không trộn vector từ model khác nhau.
- Email account được normalize lowercase ở application và enforce unique không phân biệt hoa/thường bằng index `LOWER(email)`.

## 6. State được khóa

- Resume parsing: `PENDING|PROCESSING|PARSED|FAILED`.
- Job parsing: `PENDING|PROCESSING|PARSED|FAILED`.
- Job business: `DRAFT|ACTIVE|CLOSED`.
- Match: `PENDING|PROCESSING|COMPLETED|FAILED`.

JD chỉ được `ACTIVE` khi đã `PARSED`, `is_criteria_verified=true`, embedding hợp lệ và có ít nhất một `job_skill` hợp lệ.

`QUEUED` **không** phải MatchStatus. Response trigger matching dùng `status=PENDING`; nếu cần mô tả trạng thái task queue thì dùng field khác.

## 7. Ownership & privacy decision được khóa

- Candidate chỉ quản lý CV của mình.
- HR chỉ quản lý CV trong kho do chính HR upload trong MVP.
- HR chỉ quản lý JD do mình sở hữu.
- Candidate self-match với JD của HR **không cấp quyền cho HR xem CV/kết quả đó**.
- Candidate đọc Match nếu resume thuộc Candidate.
- HR đọc Match/Skill Gap/Leaderboard chỉ khi JD thuộc HR **và** resume cũng thuộc kho HR.
- Admin toàn quyền.
- MVP chưa có `applications`; nếu sau này có quy trình nộp/chia sẻ CV thì phải sửa PTTK trước khi mở rộng quyền HR.

## 8. API decision được khóa

- Base URL: `/api/v1`.
- Matching single/batch dùng **một endpoint**: `POST /matching/calculate` với `resume_ids[]`.
- Không dùng `/matching/single-match` hoặc `/matching/batch-match`.
- `GET /matching` cung cấp **danh sách kết quả hiện hành** theo ownership; MVP không lưu nhiều attempt cho cùng `(job_id,resume_id)`.
- `GET /skills` là nguồn taxonomy duy nhất cho autocomplete/Human-in-the-loop ở CV và JD.
- Candidate không được xem leaderboard của ứng viên khác.
- Refresh Token HttpOnly nếu bật phải do backend gửi bằng `Set-Cookie`.
- `/jobs/{id}/export` là Advanced và **không nằm trong OpenAPI MVP v1** cho tới khi được triển khai/bật thật.

## 9. Matching decision được khóa

Algorithm version đầu tiên: `hybrid-v1`.

```text
Overall = w_skill * SkillScore
        + w_semantic * SemanticScore
        + w_experience * ExperienceScore
```

Mặc định `0.50 / 0.30 / 0.20`, tổng = 1.

Matching chỉ chạy khi CV/JD đã `PARSED`, embedding hợp lệ, criteria JD đã verified và JD có ít nhất một `job_skill`; Candidate còn yêu cầu JD phải `ACTIVE`.

- Skill Score: taxonomy/rule matching, MANDATORY nặng hơn OPTIONAL.
- Semantic Score: cosine similarity của embedding cùng model.
- Experience Score: tỷ lệ đáp ứng kinh nghiệm tối thiểu, cap 100%.
- `rank_bm25`: lexical/retrieval/experiment signal trong `hybrid-v1`, **không** cộng thẳng vào Semantic/Final Score. Muốn blend BM25 phải cập nhật công thức + đổi `algorithm_version`.
- LLM/XAI: explanation/recommendation only; không sửa deterministic scores.

## 10. Match invalidation được khóa

Mỗi cặp `(job_id,resume_id)` có một row kết quả hiện hành. Khi criteria/weights/JD raw content/CV parsed data/embedding model/algorithm thay đổi, kết quả cũ phải bị invalidate ngay.

Canonical invalidate:

```text
status=PENDING
all four scores=NULL
matched_skills=[]
missing_skills=[]
gap_analysis_summary=NULL
error_message=NULL
calculated_at=NULL
updated_at=NOW()
```

Nếu `recalculate=true` thì enqueue tính lại ngay; nếu không, row vẫn `PENDING` và không được leaderboard sử dụng cho đến khi tính lại.

## 11. Thứ tự nguồn chuẩn khi triển khai

Khi code có mâu thuẫn, ưu tiên theo thứ tự:

1. `PTTK_MASTER.md` + requirements/business rules.
2. `05_database/schema.sql` cho persistence.
3. `07_api/api_contract.md` và `07_api/openapi.yaml` cho HTTP contract.
4. Use Case / Activity / Sequence / Architecture.
5. Code implementation.

Muốn thay đổi API/database/enum phải **sửa PTTK trước**, audit traceability, sau đó mới sửa code.

## 12. Gate trước khi code

Trước khi viết ORM/Auth nghiệp vụ:

- legacy docs mâu thuẫn đã được loại khỏi branch;
- OpenAPI phải parse được và không có local `$ref` hỏng;
- OpenAPI phải khai báo response schema cho các endpoint JSON chính, không chỉ có `description`;
- PlantUML phải vượt static syntax audit và nên render thử trên máy phát triển;
- `schema.sql` phải thống nhất với Data Dictionary/PDM;
- Traceability Matrix không chỉ tới endpoint/table không tồn tại;
- ownership/privacy của Match phải nhất quán giữa Requirements -> API -> Service design;
- stale Match phải bị invalidate theo rule canonical;
- Pull Request PTTK phải được user review/merge;
- database thực nghiệm cũ phải được reset và tạo lại từ schema canonical;
- `schema.sql` phải chạy thành công trên PostgreSQL 18 + pgvector trước khi viết ORM.

## 13. Cấu trúc tài liệu

```text
docs/
├── 00_requirements/
├── 01_bfd/
├── 02_usecase/
├── 03_activity/
├── 04_sequence/
├── 05_database/
├── 06_architecture/
├── 07_api/
├── 08_traceability/
├── 09_delivery/
├── PTTK_MASTER.md
└── README.md
```
