# KẾ HOẠCH RESET DATABASE SAU KHI PTTK ĐƯỢC DUYỆT

DB `webcv_ungvien` hiện là bản thực nghiệm. Sau merge PTTK, reset hoàn toàn rồi bootstrap từ thiết kế mới.

## Điều kiện trước reset
- PR được review/merge.
- CI HEAD cuối PASS.
- Xác nhận DB cũ không có dữ liệu cần giữ.

## Drop/Create
Kết nối database quản trị `postgres`:
```sql
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname='webcv_ungvien' AND pid<>pg_backend_pid();
DROP DATABASE IF EXISTS webcv_ungvien;
CREATE DATABASE webcv_ungvien;
```

Sau đó kết nối `webcv_ungvien` và chạy theo đúng thứ tự:
1. `docs/05_database/schema.sql`
2. `docs/05_database/skill_taxonomy_seed.sql`

Không được bỏ qua seed: NLP/JD criteria phụ thuộc taxonomy.

## Verification

### Extensions
```sql
SELECT extname, extversion FROM pg_extension
WHERE extname IN ('uuid-ossp','vector') ORDER BY extname;
```

### 10 tables
Expected: users, skills, resumes, candidate_profiles, resume_skills, resume_experiences, resume_educations, job_descriptions, job_skills, match_results.

### Taxonomy seed
```sql
SELECT COUNT(*) AS skill_count FROM skills;
SELECT skill_kind, COUNT(*) FROM skills GROUP BY skill_kind ORDER BY skill_kind;
```
Expected baseline > 0 HARD và > 0 SOFT; seed hiện có hơn 30 skill.

### Critical columns
```sql
SELECT table_name, column_name
FROM information_schema.columns
WHERE table_schema='public'
  AND column_name IN (
    'revision','generation','resume_revision','job_revision',
    'embedding_preprocessing_version'
  )
ORDER BY table_name,column_name;
```
Phải thấy revision ở Resume/JD; generation + revision snapshots ở Match; preprocessing version ở Resume/JD/Match.

### Indexes
- `uq_users_email_ci` LOWER(email)
- `idx_resumes_embedding_hnsw`
- `idx_jobs_embedding_hnsw`
- unique current Match pair

### Constraint smoke tests trong transaction rồi ROLLBACK
- role invalid bị reject;
- email khác hoa/thường bị reject;
- file >5MB bị reject;
- Resume/JD PARSED thiếu model/preprocessing bị reject;
- Resume/JD FAILED không có error bị reject;
- Job ACTIVE chưa ready bị reject;
- weights tổng !=1 bị reject;
- Match COMPLETED thiếu score/provenance/calculated_at bị reject;
- Match PENDING còn score/evidence/provenance bị reject;
- Match FAILED không có error bị reject;
- generation/revision <1 bị reject.

## Service-level tests sau khi có backend
DB CHECK không thể enforce cross-table/concurrency logic. Integration tests bắt buộc:
1. Old JD parse task với revision cũ không overwrite revision mới.
2. Old Match task với generation cũ không overwrite Match mới.
3. Candidate/HR privacy scope.
4. Role change có resource -> 409.
5. DRAFT->CLOSED -> 422.
6. Một resume invalid trong batch -> không Match row nào mutate.
7. Dispatcher failure -> 503; retry request an toàn.

## Sau verification
venv -> async SQLAlchemy connection -> ORM map schema có sẵn -> Pydantic/OpenAPI -> Auth MVP -> Skills -> Resume/Job -> AI/NLP -> Matching -> Frontend.

Không dùng ORM `create_all()` để phát minh schema mới.
