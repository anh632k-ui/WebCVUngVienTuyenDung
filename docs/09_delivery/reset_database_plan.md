# KẾ HOẠCH RESET DATABASE SAU KHI PTTK ĐƯỢC DUYỆT

## Mục tiêu

Database `webcv_ungvien` hiện tại là bản thực nghiệm được tạo trước khi PTTK đồng bộ. Do chưa có dữ liệu nghiệp vụ cần bảo toàn, sau khi PR PTTK được review/merge sẽ **reset database và tạo lại từ `docs/05_database/schema.sql`** thay vì viết migration vá chồng lên thiết kế cũ.

## Điều kiện trước khi reset

Không thực hiện reset cho tới khi:

- Pull Request PTTK đã được user review/merge.
- GitHub Actions `Validate PTTK` PASS ở HEAD cuối cùng.
- `schema.sql` đã được audit lần cuối.
- Đã xác nhận database cũ không chứa dữ liệu cần giữ.
- Có backup nếu muốn giữ snapshot phục vụ đối chiếu.

## Phương án ưu tiên — drop/create database

Đăng nhập PostgreSQL bằng database quản trị khác (`postgres`), không kết nối trực tiếp vào DB đang drop.

```sql
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = 'webcv_ungvien'
  AND pid <> pg_backend_pid();

DROP DATABASE IF EXISTS webcv_ungvien;
CREATE DATABASE webcv_ungvien;
```

Sau đó kết nối `webcv_ungvien` và chạy toàn bộ:

```text
docs/05_database/schema.sql
```

Script tự tạo `uuid-ossp` và `vector` nếu PostgreSQL server đã có package pgvector tương thích.

## Verification bắt buộc

### 1. Extensions

```sql
SELECT extname, extversion
FROM pg_extension
WHERE extname IN ('uuid-ossp', 'vector')
ORDER BY extname;
```

Phải có cả `uuid-ossp` và `vector`.

### 2. Số bảng nghiệp vụ

Expected = **10**.

```sql
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_type = 'BASE TABLE'
ORDER BY table_name;
```

Expected:

```text
candidate_profiles
job_descriptions
job_skills
match_results
resume_educations
resume_experiences
resume_skills
resumes
skills
users
```

### 3. Foreign keys / unique constraints / unique indexes

Kiểm tra FK/UNIQUE bằng pgAdmin hoặc `information_schema`/`pg_indexes`. Các quan hệ chính phải tồn tại đúng PDM: ownership user->resume/JD, resume aggregate, taxonomy links, và cặp Match `(job_id,resume_id)` unique.

Email account phải có unique index không phân biệt hoa/thường:

```sql
SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname='public'
  AND indexname='uq_users_email_ci';
```

`indexdef` phải chứa `lower((email)::text)` hoặc biểu diễn tương đương PostgreSQL của `LOWER(email)`.

### 4. Check constraints

Đặc biệt phải có:

- role `CANDIDATE|HR|ADMIN`;
- Resume/Job parsing status;
- Job business status;
- job weights tổng `1.000`;
- Resume PARSED phải có raw text + embedding/model + parsed timestamp;
- Job PARSED phải có embedding/model + parsed timestamp;
- Job ACTIVE phải `PARSED` và `is_criteria_verified=true`;
- soft-delete phải có `deleted_at`;
- Match scores nằm trong `[0,100]` khi có giá trị;
- Match COMPLETED phải có đủ bốn scores + `calculated_at`;
- Match không phải COMPLETED phải clear stale scores/evidence/`calculated_at` theo `chk_noncompleted_match_cleared`.

**Lưu ý:** invariant “JD phải có ít nhất một `job_skill` trước ACTIVE/matching” và quyền HR phải đồng thời sở hữu JD + CV là service/API invariant, không nên ép bằng CHECK cross-table.

### 5. HNSW indexes

```sql
SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname='public'
  AND indexname IN (
    'idx_resumes_embedding_hnsw',
    'idx_jobs_embedding_hnsw'
  )
ORDER BY indexname;
```

Phải thấy `vector_cosine_ops` cho cả hai embedding indexes.

### 6. Smoke-test constraints sau reset

Sau khi schema chạy thành công, nên thử trong transaction rồi rollback:

- insert user role sai -> phải bị từ chối;
- insert hai user `Test@Example.com` và `test@example.com` -> bản ghi thứ hai phải bị unique-index từ chối;
- insert Resume file > 5 MB -> phải bị từ chối;
- đặt Job ACTIVE khi chưa PARSED/verified -> phải bị từ chối;
- đặt tổng weights khác 1 -> phải bị từ chối;
- đặt Match COMPLETED khi score còn NULL -> phải bị từ chối;
- đặt Match PENDING nhưng còn score/evidence cũ -> phải bị `chk_noncompleted_match_cleared` từ chối.

## Sau reset

Thứ tự triển khai backend:

1. Python venv + requirements.
2. `.env`.
3. Async SQLAlchemy connection test.
4. ORM Models **map vào schema đã tồn tại**; không dùng `create_all()` để tự phát minh schema.
5. Pydantic request/response schemas từ API Contract/OpenAPI.
6. Auth + Users + Skill Taxonomy lookup.
7. Resume CRUD/Human-in-the-loop + match invalidation.
8. Job CRUD/criteria/weights + match invalidation.
9. AI/NLP pipeline.
10. Matching + Skill Gap + leaderboard với strict ownership.
11. Frontend integration.

## Không được làm

- Không chạy `schema.sql` mới đè lên schema cũ rồi hy vọng tự khớp.
- Không dùng ORM `create_all()` để tạo thêm cột/bảng ngoài PTTK.
- Không giữ cả `user_id` cũ và `owner_user_id` mới song song.
- Không tạo `skill_type` trong `job_skills`; field chuẩn là `importance`.
- Không coi `GET /matching` là lịch sử nhiều attempt; MVP chỉ giữ current result cho mỗi pair.
- Không cho HR đọc Candidate self-match nếu CV không thuộc kho HR.
- Không thêm endpoint/table/enum mới trong code nếu chưa cập nhật PTTK + Traceability trước.
