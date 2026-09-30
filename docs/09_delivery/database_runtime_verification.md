# DATABASE RUNTIME VERIFICATION

## 1. Môi trường kiểm thử

- Database: `webcv_ungvien`
- PostgreSQL: `18.1`
- Extension `uuid-ossp`: `1.1`
- Extension `vector`: `0.8.6`
- Schema source: `docs/05_database/schema.sql`
- Seed source: `docs/05_database/skill_taxonomy_seed.sql`

## 2. Bootstrap database

Database thực nghiệm cũ đã được reset hoàn toàn.

Canonical schema được dựng lại từ:

1. `docs/05_database/schema.sql`
2. `docs/05_database/skill_taxonomy_seed.sql`

Kết quả:

- Tổng số bảng: `10`
- Skill taxonomy: `42`
- HARD skills: `36`
- SOFT skills: `6`

## 3. Canonical tables

Các bảng đã xác nhận:

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

## 4. Reliability columns

Đã xác nhận tồn tại:

### resumes

- `revision`
- `create_request_fingerprint`
- `embedding_preprocessing_version`

### job_descriptions

- `revision`
- `create_request_fingerprint`
- `embedding_preprocessing_version`

### match_results

- `generation`
- `resume_revision`
- `job_revision`
- `embedding_preprocessing_version`

## 5. Critical indexes

Đã xác nhận:

- `uq_users_email_ci`
- `idx_resumes_embedding_hnsw`
- `idx_jobs_embedding_hnsw`
- `uq_job_resume_match`
- `idx_match_job_leaderboard`
- `idx_match_resume`

HNSW embedding indexes sử dụng `vector_cosine_ops`.

## 6. Constraint smoke tests

Các test sau đã được thực thi trực tiếp trên PostgreSQL.

### User

- Invalid role -> rejected.
- Email khác hoa/thường nhưng cùng giá trị -> rejected bởi `uq_users_email_ci`.

### Resume

- File lớn hơn 5 MB -> rejected.
- Invalid SHA-256 request fingerprint -> rejected.
- `revision < 1` -> rejected.
- `PARSED` thiếu parse payload/provenance -> rejected.
- `FAILED` không có error message -> rejected.

### Job Description

- Invalid SHA-256 request fingerprint -> rejected.
- `revision < 1` -> rejected.
- Matching weights tổng khác `1.000` -> rejected.
- `ACTIVE` khi chưa `PARSED` và chưa verify criteria -> rejected.

### Match Result

- `generation < 1` -> rejected.
- `resume_revision < 1` -> rejected.
- `job_revision < 1` -> rejected.
- `COMPLETED` thiếu score/provenance/calculated_at -> rejected.
- `PENDING` còn score cũ -> rejected.
- `FAILED` không có error message -> rejected.

## 7. Rollback verification

Các smoke test được thực hiện trong transaction và rollback sau kiểm thử.

Trạng thái cuối:

- `users`: 0 test rows
- `resumes`: 0 test rows
- `job_descriptions`: 0 test rows
- `match_results`: 0 test rows

Không còn dữ liệu test trong database.

## 8. Kết luận

Canonical database bootstrap và runtime database constraint gate đã PASS.

Các logic sau chưa được coi là runtime-tested tại giai đoạn này vì thuộc service/backend layer:

- idempotency same-key/same-fingerprint;
- `409 IDEMPOTENCY_KEY_REUSED`;
- concurrent deterministic create;
- stale parse revision CAS;
- stale match generation CAS;
- soft-delete race;
- dispatcher recovery;
- Resume storage orphan reconciliation;
- Candidate/HR ownership and privacy scope.

Các mục này phải được kiểm thử sau khi backend tương ứng được triển khai.