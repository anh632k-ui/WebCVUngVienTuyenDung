# API CONTRACT SPECIFICATION — v1

## 1. Quy ước chung

- Base URL: `/api/v1`.
- JSON UTF-8, trừ upload/download file.
- Access Token: `Authorization: Bearer <token>`.
- UUID dùng cho `users`, `resumes`, `job_descriptions`, `match_results`.
- `skills.id` và các bảng child dùng integer/bigint theo schema.
- Timestamp trả ISO-8601 UTC.
- Backend là nơi quyết định authorization; frontend không phải security boundary.

### Success envelope

```json
{
  "success": true,
  "message": "optional",
  "data": {}
}
```

List response:

```json
{
  "success": true,
  "data": [],
  "meta": {
    "page": 1,
    "limit": 20,
    "total_items": 100,
    "total_pages": 5
  }
}
```

### Error envelope

```json
{
  "success": false,
  "error": {
    "code": "RESOURCE_NOT_READY",
    "message": "CV hoặc JD chưa phân tích xong",
    "details": null
  }
}
```

`204 No Content` không trả body.

## 2. HTTP status chuẩn

| Status | Ý nghĩa |
|---|---|
| 200 | GET/PUT/PATCH thành công |
| 201 | Tạo resource đồng bộ thành công |
| 202 | Tác vụ background đã được nhận |
| 204 | Logout/delete thành công, không body |
| 400 | Business request sai không thuộc schema validation |
| 401 | Chưa xác thực/token không hợp lệ |
| 403 | Xác thực được nhưng không đủ role/quyền |
| 404 | Resource không tồn tại hoặc policy không cho lộ resource |
| 409 | Unique/conflict như email đã tồn tại |
| 413 | Upload > 5 MB |
| 415 | MIME/file format không được hỗ trợ |
| 422 | Payload/query invalid hoặc precondition nghiệp vụ không đạt |

---

# MODULE A — AUTH & ACCOUNT

## POST `/auth/register`

**Access:** Public.

Request:

```json
{
  "email": "candidate@example.com",
  "password": "SecurePassword123@",
  "full_name": "Nguyen Van A",
  "phone_number": "0912345678",
  "role": "CANDIDATE"
}
```

Rules:
- role chỉ `CANDIDATE|HR`.
- email được backend normalize lowercase.
- email unique.
- password tối thiểu 8 ký tự; password policy chi tiết đặt ở schema/service.

Response `201`:

```json
{
  "success": true,
  "data": {
    "id": "uuid",
    "email": "candidate@example.com",
    "full_name": "Nguyen Van A",
    "phone_number": "0912345678",
    "role": "CANDIDATE",
    "is_active": true,
    "created_at": "2026-09-30T00:00:00Z"
  }
}
```

Errors: `409`, `422`.

## POST `/auth/login`

**Access:** Public.

Request:

```json
{
  "email": "candidate@example.com",
  "password": "SecurePassword123@"
}
```

Response `200`:

```json
{
  "success": true,
  "data": {
    "access_token": "jwt",
    "token_type": "Bearer",
    "expires_in": 900,
    "user": {
      "id": "uuid",
      "email": "candidate@example.com",
      "full_name": "Nguyen Van A",
      "role": "CANDIDATE"
    }
  }
}
```

Nếu Refresh Session được bật, backend đồng thời gửi `Set-Cookie` chứa refresh token/session với `HttpOnly`; frontend không tự tạo HttpOnly cookie.

Errors: `401`, `403`, `422`.

## POST `/auth/refresh`

**Access:** Refresh cookie hợp lệ.

- Backend đọc cookie, verify và rotate.
- Response `200` trả Access Token mới.
- Backend gửi `Set-Cookie` refresh token mới.

Errors: `401` cho expired/revoked/replay.

## POST `/auth/logout`

**Access:** Authenticated; refresh cookie nếu module bật.

- Revoke session hiện tại.
- Clear refresh cookie.
- Response `204`.

## PUT `/auth/change-password`

**Access:** Authenticated.

Request:

```json
{
  "current_password": "OldPass123@",
  "new_password": "NewPass123@"
}
```

Response `200`; khi thành công revoke các refresh session cũ theo policy.

Errors: `400` current password sai, `401`, `422`.

## GET `/users/me`

**Access:** Authenticated.

Response `200` với `id,email,full_name,phone_number,role,is_active,created_at,updated_at`.

## PUT `/users/me`

**Access:** Authenticated.

Request:

```json
{
  "full_name": "Nguyen Van A",
  "phone_number": "0987654321"
}
```

Không nhận `role`, `is_active`, `id` từ endpoint này.

## GET `/admin/users`

**Access:** Admin only.

Query: `page`, `limit`, `role`, `is_active`, `keyword`.

Response: paginated users.

## PATCH `/admin/users/{id}`

**Access:** Admin only.

Request hỗ trợ ít nhất một field:

```json
{
  "is_active": false,
  "role": "HR"
}
```

Rule: không cho self-lock/self-demote.

Errors: `400`, `403`, `404`, `422`.

---

# MODULE B — RESUME & NLP

## POST `/resumes/upload`

**Access:** Candidate, HR.

Content-Type: `multipart/form-data`.

Field:
- `file`: PDF hoặc DOCX, <= 5 MB.

Backend kiểm MIME/Magic Bytes và ownership `owner_user_id=current_user.id`.

Response `202`:

```json
{
  "success": true,
  "message": "CV đã được tiếp nhận để phân tích",
  "data": {
    "resume_id": "uuid",
    "file_name": "cv.pdf",
    "file_size": 154200,
    "parsing_status": "PENDING"
  }
}
```

Errors: `413`, `415`, `422`.

## GET `/resumes`

**Access:** Candidate/HR/Admin.

Query:
- `page` default 1.
- `limit` default 20, max 100.
- `keyword` optional.
- `parsing_status=PENDING|PROCESSING|PARSED|FAILED` optional.
- `skill_ids` optional repeated/comma-separated theo implementation OpenAPI.

Scope:
- Candidate/HR: `owner_user_id=current_user.id`, `is_deleted=false`.
- Admin: toàn hệ thống, mặc định loại soft-deleted.

Response paginated resume summary.

## GET `/resumes/{id}`

**Access:** Owner Candidate/HR hoặc Admin.

Response `200`:

```json
{
  "success": true,
  "data": {
    "id": "uuid",
    "file_name": "cv.pdf",
    "parsing_status": "PARSED",
    "is_manually_edited": false,
    "candidate_profile": {
      "full_name": "Nguyen Van A",
      "email": "a@example.com",
      "phone_number": "0900000000",
      "current_title": "Backend Intern",
      "location": "Ho Chi Minh",
      "linkedin_url": null,
      "github_url": null,
      "professional_summary": null
    },
    "skills": [
      {
        "skill_id": 1,
        "name": "Python",
        "skill_kind": "HARD",
        "category": "Language",
        "years_of_experience": 2.0,
        "proficiency_level": "Intermediate"
      }
    ],
    "experiences": [],
    "educations": []
  }
}
```

## GET `/resumes/{id}/status`

**Access:** Owner/Admin.

Response:

```json
{
  "success": true,
  "data": {
    "resume_id": "uuid",
    "parsing_status": "PROCESSING",
    "parsed_at": null,
    "error_message": null
  }
}
```

## PUT `/resumes/{id}/parsed-data`

**Access:** Owner/Admin.

**Precondition:** Resume `PARSED`.

Request:

```json
{
  "candidate_profile": {
    "full_name": "Nguyen Van A",
    "email": "a@example.com",
    "phone_number": "0900000000",
    "current_title": "Backend Developer",
    "location": "Ho Chi Minh",
    "linkedin_url": null,
    "github_url": "https://github.com/example",
    "professional_summary": "..."
  },
  "skills": [
    {
      "skill_id": 1,
      "years_of_experience": 2.0,
      "proficiency_level": "Intermediate"
    }
  ],
  "experiences": [
    {
      "company_name": "Tech Corp",
      "job_title": "Backend Intern",
      "start_date": "2025-06-01",
      "end_date": "2025-12-31",
      "is_current": false,
      "description": "..."
    }
  ],
  "educations": [
    {
      "institution_name": "University",
      "degree": "Engineer",
      "field_of_study": "Information Technology",
      "start_year": 2022,
      "graduation_year": 2026,
      "gpa": 3.4,
      "description": null
    }
  ]
}
```

Backend update trong transaction, set `is_manually_edited=true`, rebuild embedding nếu structured text thay đổi.

Response `200`.

## GET `/resumes/{id}/download`

**Access:** Owner/Admin.

Response `200` binary stream với `Content-Disposition` phù hợp.

## DELETE `/resumes/{id}`

**Access:** Owner/Admin.

Soft delete `is_deleted=true`, `deleted_at=now()`.

Response `204`.

---

# MODULE C — JOB DESCRIPTION

## POST `/jobs`

**Access:** HR/Admin.

Request:

```json
{
  "title": "Backend Python Developer",
  "job_level": "Junior",
  "location": "Ho Chi Minh",
  "raw_content": "...",
  "min_experience_years": 1.0,
  "education_requirement": null,
  "w_skill": 0.5,
  "w_semantic": 0.3,
  "w_experience": 0.2
}
```

Server tạo `status=DRAFT`, `parsing_status=PENDING`, enqueue phân tích JD.

Response `201` với `job_id`, `status`, `parsing_status`.

## GET `/jobs`

**Access:** Authenticated.

Query: `page`, `limit`, `status`, `keyword`, `parsing_status`.

Scope:
- Candidate: chỉ `ACTIVE`, `is_deleted=false`.
- HR: JD do mình sở hữu, có thể lọc DRAFT/ACTIVE/CLOSED.
- Admin: toàn bộ theo filter.

## GET `/jobs/{id}`

**Access:**
- Candidate: chỉ JD ACTIVE.
- HR: JD mình sở hữu.
- Admin: toàn quyền.

## PUT `/jobs/{id}`

**Access:** HR owner/Admin.

Cho phép sửa metadata/raw content. Nếu `raw_content` đổi:
- `parsing_status=PENDING`;
- `is_criteria_verified=false`;
- xóa/invalid embedding cũ;
- enqueue parse lại.

Response `200`.

## PATCH `/jobs/{id}/status`

**Access:** HR owner/Admin.

Request:

```json
{"status": "ACTIVE"}
```

Allowed: `DRAFT|ACTIVE|CLOSED`. Schema yêu cầu JD phải `PARSED` trước khi ACTIVE.

## DELETE `/jobs/{id}`

**Access:** HR owner/Admin.

Soft delete. Response `204`.

## GET `/jobs/{id}/criteria`

**Access:** HR owner/Admin.

Response:

```json
{
  "success": true,
  "data": {
    "job_id": "uuid",
    "min_experience_years": 1.0,
    "education_requirement": "University or equivalent",
    "is_criteria_verified": false,
    "skills": [
      {
        "skill_id": 1,
        "name": "Python",
        "skill_kind": "HARD",
        "importance": "MANDATORY",
        "min_years_required": 1.0
      }
    ]
  }
}
```

## PUT `/jobs/{id}/criteria`

**Access:** HR owner/Admin.

Request:

```json
{
  "min_experience_years": 1.0,
  "education_requirement": "University or equivalent",
  "skills": [
    {
      "skill_id": 1,
      "importance": "MANDATORY",
      "min_years_required": 1.0
    },
    {
      "skill_id": 15,
      "importance": "OPTIONAL",
      "min_years_required": 0.0
    }
  ]
}
```

Transaction update, không cho duplicate `skill_id`, set `is_criteria_verified=true`.

## PUT `/jobs/{id}/weights`

**Access:** HR owner/Admin.

Request:

```json
{
  "w_skill": 0.5,
  "w_semantic": 0.3,
  "w_experience": 0.2,
  "recalculate": true
}
```

Rules: mỗi weight [0,1], tổng = 1.000. Nếu `recalculate=true`, matching hiện có được đưa về lifecycle tính lại theo service policy.

---

# MODULE D — MATCHING & ANALYTICS

## POST `/matching/calculate`

**Access:** Candidate/HR/Admin.

Request dùng chung single/batch:

```json
{
  "job_id": "uuid",
  "resume_ids": ["uuid"]
}
```

Rules:
- `resume_ids` tối thiểu 1, không duplicate.
- Candidate: mọi resume_id phải thuộc Candidate; job phải ACTIVE.
- HR: job phải thuộc HR; mọi resume_id phải thuộc kho HR trong MVP.
- Admin: bypass ownership cho tác vụ quản trị.
- CV/JD phải PARSED, chưa deleted.

Response `202`:

```json
{
  "success": true,
  "data": {
    "job_id": "uuid",
    "match_ids": ["uuid"],
    "total_jobs": 1,
    "status": "QUEUED"
  }
}
```

Errors: `403/404`, `422` resource not ready.

## GET `/matching/{match_id}`

**Access:** Candidate owner CV, HR owner JD, Admin.

Response PROCESSING/PENDING có scores null. Response COMPLETED:

```json
{
  "success": true,
  "data": {
    "id": "uuid",
    "job_id": "uuid",
    "resume_id": "uuid",
    "status": "COMPLETED",
    "overall_score": 86.25,
    "skill_score": 90.0,
    "semantic_score": 82.5,
    "experience_score": 80.0,
    "algorithm_version": "hybrid-v1",
    "calculated_at": "2026-09-30T00:05:00Z"
  }
}
```

## GET `/matching/{match_id}/gap-analysis`

**Access:** như GET match.

Response:

```json
{
  "success": true,
  "data": {
    "match_id": "uuid",
    "scores": {
      "overall": 86.25,
      "skill": 90.0,
      "semantic": 82.5,
      "experience": 80.0
    },
    "matched_skills": [],
    "missing_skills": [
      {
        "skill_id": 15,
        "name": "Docker",
        "skill_kind": "HARD",
        "importance": "OPTIONAL",
        "criticality": "MINOR",
        "gap_type": "MISSING",
        "recommendation": "Học Docker fundamentals và thực hành container hóa API."
      }
    ],
    "summary": "..."
  }
}
```

LLM explanation nếu có là field bổ sung, không thay đổi scores.

## GET `/jobs/{id}/leaderboard`

**Access:** HR owner/Admin. Candidate bị từ chối.

Query:
- `page` default 1.
- `limit` default 50, max 100.
- `min_score` optional 0–100.

Chỉ lấy Match `COMPLETED`, sort `overall_score DESC`.

## GET `/jobs/{id}/export`

**Access:** HR owner/Admin.

Query `format=pdf|excel`.

Advanced feature. Nếu chưa cài đặt thì route không được quảng bá như tính năng hoàn thành.

---

# 3. Quy tắc đồng bộ với code

1. Pydantic request/response schema phải map tên field đúng tài liệu này.
2. ORM phải map đúng `docs/05_database/schema.sql`.
3. Không tạo route `/matching/single-match` hoặc `/matching/batch-match`; chỉ dùng `/matching/calculate`.
4. Không dùng `skill_type` cho `job_skills`; field chuẩn là `importance`. `skills.skill_kind` mới là HARD/SOFT.
5. `owner_user_id` là field chuẩn của `resumes`; không quay lại tên `user_id` nếu không cập nhật toàn bộ PTTK.
