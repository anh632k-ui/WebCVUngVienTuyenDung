# PTTK MASTER — WEBCVUNGVIENTUYENDUNG

**Status:** Design baseline v1 — phải audit xong trước khi code nghiệp vụ.

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
      Redis/Celery when background/session features enabled
```

## 3. Actor được khóa

- Guest
- Candidate
- HR
- Admin

Celery Worker, Redis, PostgreSQL, AI model không phải actor nghiệp vụ.

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

`candidate_profiles` là snapshot được bóc từ từng CV và cố ý tách khỏi `users`.

## 5. Naming được khóa

- Resume ownership: `owner_user_id`.
- JD ownership: `recruiter_id`.
- Skill hard/soft taxonomy: `skills.skill_kind = HARD|SOFT`.
- Mức độ bắt buộc trong JD: `job_skills.importance = MANDATORY|OPTIONAL`.
- Không dùng `job_skills.skill_type` để tránh nhầm với hard/soft.
- Embedding: `resume_embedding`, `job_embedding`, hiện `vector(1024)`.

## 6. State được khóa

- Resume parsing: `PENDING|PROCESSING|PARSED|FAILED`.
- Job parsing: `PENDING|PROCESSING|PARSED|FAILED`.
- Job business: `DRAFT|ACTIVE|CLOSED`.
- Match: `PENDING|PROCESSING|COMPLETED|FAILED`.

JD ACTIVE phải đã PARSED.

## 7. API decision được khóa

- Base `/api/v1`.
- Matching single/batch dùng **một endpoint**: `POST /matching/calculate` với `resume_ids[]`.
- Không dùng `/matching/single-match` và `/matching/batch-match`.
- Candidate không có quyền xem leaderboard của người khác.
- HR chỉ quản lý JD do mình sở hữu.
- Candidate/HR chỉ quản lý CV trong kho sở hữu ở MVP.

## 8. Matching decision được khóa

Algorithm version đầu tiên: `hybrid-v1`.

```text
Overall = w_skill * SkillScore
        + w_semantic * SemanticScore
        + w_experience * ExperienceScore
```

Mặc định 0.50 / 0.30 / 0.20, tổng = 1.

- Skill Score: taxonomy/rule matching, MANDATORY nặng hơn OPTIONAL.
- Semantic Score: cosine similarity của embedding cùng model.
- Experience Score: tỷ lệ đáp ứng kinh nghiệm tối thiểu, cap 100%.
- `rank_bm25`: lexical/retrieval/experiment signal trong v1, chưa cộng thẳng vào final score vì raw BM25 không có thang cố định.
- LLM/XAI: explanation/recommendation only, không sửa deterministic scores.

## 9. Thứ tự nguồn chuẩn khi triển khai

Khi code có mâu thuẫn, ưu tiên theo thứ tự:

1. `PTTK_MASTER.md` + requirements/business rules.
2. `05_database/schema.sql` cho persistence.
3. `07_api/api_contract.md` và `07_api/openapi.yaml` cho HTTP contract.
4. Use Case / Activity / Sequence / Architecture.
5. Code implementation.

Nếu muốn thay đổi API/database/enum, **sửa PTTK trước**, audit traceability, sau đó mới sửa code.

## 10. Gate trước khi code

Không bắt đầu ORM/Auth nghiệp vụ trước khi:

- không còn docs cũ mâu thuẫn trong branch;
- OpenAPI parse được;
- PlantUML core không có lỗi cú pháp rõ ràng;
- schema.sql thống nhất với Data Dictionary/PDM;
- Traceability Matrix không chỉ tới endpoint/table không tồn tại;
- có kế hoạch reset database cũ;
- user review Pull Request PTTK.

## 11. Cấu trúc tài liệu

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
