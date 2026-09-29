# KẾ HOẠCH RESET DATABASE SAU KHI PTTK ĐƯỢC DUYỆT

## Mục tiêu

Database `webcv_ungvien` hiện tại là bản thực nghiệm được tạo trước khi PTTK đồng bộ. Do chưa có dữ liệu nghiệp vụ cần bảo toàn, sau khi PR PTTK được review/merge sẽ **reset schema/database và tạo lại từ `docs/05_database/schema.sql`** thay vì viết migration vá chồng lên thiết kế cũ.

## Điều kiện trước khi reset

Không thực hiện reset cho tới khi:

- Pull Request PTTK đã được user review.
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

Script tự tạo `uuid-ossp` và `vector` nếu PostgreSQL server đã có extension package pgvector.

## Verification bắt buộc

### Extensions

```sql
SELECT extname, extversion
FROM pg_extension
WHERE extname IN ('uuid-ossp', 'vector')
ORDER BY extname;
```

### Số bảng nghiệp vụ

Expected = 10.

```sql
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_type = 'BASE TABLE'
ORDER BY table_name;
```

Expected tables:

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

### Foreign keys

Kiểm tra FK bằng pgAdmin hoặc query `information_schema.table_constraints` / `key_column_usage`.

### Check constraints

Đặc biệt phải có:

- role enum-like check.
- parsing status checks.
- job status check.
- job weights sum = 1.000.
- active JD phải parsed.
- score range [0,100].
- completed match phải có đủ scores + calculated_at.

### HNSW indexes

```sql
SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname='public'
  AND indexname IN ('idx_resumes_embedding_hnsw','idx_jobs_embedding_hnsw');
```

## Sau reset

Thứ tự triển khai backend:

1. Python venv + requirements.
2. `.env`.
3. Async SQLAlchemy connection test.
4. ORM Models **map vào schema đã tồn tại**; không dùng `create_all()` để tự phát minh schema.
5. Pydantic schemas từ API Contract/OpenAPI.
6. Auth.
7. Resume/Job CRUD.
8. AI/NLP.
9. Matching.
10. Frontend integration.

## Không được làm

- Không chạy `schema.sql` mới đè lên schema cũ rồi hy vọng tự khớp.
- Không dùng ORM `create_all()` để tạo thêm cột/bảng ngoài PTTK.
- Không giữ cả `user_id` cũ và `owner_user_id` mới song song.
- Không tạo `skill_type` trong `job_skills`; field chuẩn là `importance`.
