# API CONTRACT SPECIFICATION — MVP v1

## 1. Quy ước chung

- Base URL: `/api/v1`.
- JSON UTF-8, trừ upload/download file.
- Access Token: `Authorization: Bearer <token>`.
- Refresh Token nếu bật: backend gửi/đọc bằng HttpOnly Cookie.
- UUID dùng cho `users`, `resumes`, `job_descriptions`, `match_results`.
- `skills.id` và các bảng child dùng integer/bigint theo schema.
- Timestamp trả ISO-8601 UTC.
- Backend quyết định authentication/authorization/ownership; frontend không phải security boundary.
- OpenAPI MVP chỉ expose các route thuộc MVP. Feature Advanced như export PDF/Excel chỉ thêm vào OpenAPI khi được triển khai thật.

### Success envelope

```json
{
  "success": true,
  "message": "optional",
  "data": {}
}
```

### Paginated envelope

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
    "message": "Tài nguyên chưa sẵn sàng",
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
| 409 | Unique/conflict, ví dụ email đã tồn tại |
| 413 | Upload > 5 MB |
| 415 | MIME/file format không được hỗ trợ |
| 422 | Payload/query invalid hoặc business precondition không đạt |

---

# MODULE A — AUTH & ACCOUNT

## POST `/auth/register`

**Access:** Public.

Request: `RegisterRequest`.

Rules:
- `role` chỉ `CANDIDATE|HR`.
- backend normalize email lowercase.
- database enforce unique không phân biệt hoa/thường bằng index `LOWER(email)`.
- password tối thiểu 8 ký tự; hash trước persistence.

Response `201`: `UserResponse`.

Errors: `409`, `422`.

## POST `/auth/login`

**Access:** Public.

Request: `LoginRequest`.

Response `200`: `LoginResponse` gồm `access_token`, `token_type`, `expires_in`, `user`.

Nếu Refresh Session được bật, backend đồng thời gửi `Set-Cookie` chứa refresh token/session với `HttpOnly`; frontend không tự tạo HttpOnly Cookie.

Errors: `401`, `403`, `422`.

## POST `/auth/refresh`

**Access:** Refresh Cookie hợp lệ.

- Backend đọc Cookie, verify, kiểm session/revoke và rotate.
- Response `200`: `TokenResponse`.
- Backend gửi `Set-Cookie` refresh token mới.

Errors: `401` cho expired/revoked/replay.

## POST `/auth/logout`

**Access:** Authenticated.

- Revoke refresh session hiện tại nếu module refresh bật.
- Clear refresh cookie.
- Response `204`.

## PUT `/auth/change-password`

**Access:** Authenticated.

Request: `ChangePasswordRequest`.

Thành công: update password hash; revoke refresh sessions cũ theo policy.

Response `200`: `MessageResponse`.

Errors: `400`, `401`, `422`.

## GET `/users/me`

**Access:** Authenticated.

Response `200`: `UserResponse`.

## PUT `/users/me`

**Access:** Authenticated.

Request: `UserUpdateRequest`.

Không nhận `role`, `is_active`, `id` từ endpoint này.

Response `200`: `UserResponse`.

## GET `/admin/users`

**Access:** Admin only.

Query: `page`, `limit`, `role`, `is_active`, `keyword`.

Response `200`: `PaginatedUsersResponse`.

## PATCH `/admin/users/{id}`

**Access:** Admin only.

Request: `AdminUserPatchRequest`, ít nhất một field.

Rule: không cho self-lock/self-demote.

Response `200`: `UserResponse`.

Errors: `400`, `403`, `404`, `422`.

---

# MODULE B — SKILL TAXONOMY

## GET `/skills`

**Access:** Authenticated Candidate/HR/Admin.

Mục đích: autocomplete/tra cứu taxonomy chuẩn khi review CV/JD. MVP không expose CRUD skill cho user thường.

Query: `keyword`, `skill_kind=HARD|SOFT`, `category`, `page`, `limit`.

Response `200`: `PaginatedSkillsResponse`.

---

# MODULE C — RESUME & NLP

## POST `/resumes/upload`

**Access:** Candidate, HR.

Content-Type: `multipart/form-data`, field `file`.

Rules:
- PDF/DOCX, <= 5 MB.
- backend kiểm size + MIME/Magic Bytes;
- sinh `storage_key` an toàn;
- `owner_user_id=current_user.id`;
- tạo Resume `PENDING`;
- kích hoạt parse task/background mechanism.

Response `202`: `ResumeUploadResponse`.

Errors: `413`, `415`, `422`.

## GET `/resumes`

**Access:** Candidate/HR/Admin.

Query: `page`, `limit`, `keyword`, `parsing_status`, `skill_ids`.

Scope:
- Candidate/HR: `owner_user_id=current_user.id`, `is_deleted=false`.
- Admin: toàn hệ thống, mặc định loại soft-deleted.

Response `200`: `PaginatedResumesResponse`.

## GET `/resumes/{id}`

**Access:** Owner Candidate/HR hoặc Admin.

Response `200`: `ResumeDetailResponse` gồm metadata + `candidate_profile` + skills + experiences + educations.

## GET `/resumes/{id}/status`

**Access:** Owner/Admin.

Response `200`: `ResumeStatusResponse`.

## PUT `/resumes/{id}/parsed-data`

**Access:** Owner/Admin.

**Precondition:** Resume `PARSED`.

Request: `ResumeParsedDataUpdate`.

Rules:
- validate taxonomy skill IDs, duplicate skill IDs, date ranges;
- cập nhật aggregate trong transaction;
- tái sinh `resume_embedding` khi canonical structured text thay đổi;
- set `is_manually_edited=true`;
- **invalidate mọi Match hiện hành của resume**: `status=PENDING`, clear scores/evidence/error/`calculated_at`.

Response `200`: `ResumeDetailResponse`.

## GET `/resumes/{id}/download`

**Access:** Owner/Admin.

Response `200` binary stream với `Content-Disposition` phù hợp.

## DELETE `/resumes/{id}`

**Access:** Owner/Admin.

Soft delete: `is_deleted=true`, `deleted_at=now()`.

Response `204`.

---

# MODULE D — JOB DESCRIPTION

## POST `/jobs`

**Access:** HR/Admin.

Request: `JobCreateRequest`.

Rules:
- mỗi weight trong `[0,1]`, tổng `1.000`;
- server tạo `status=DRAFT`, `parsing_status=PENDING`;
- pipeline trích `min_experience_years`, `education_requirement`, `job_skills` và embedding từ JD.

Response `201`: `JobResponse`.

## GET `/jobs`

**Access:** Authenticated.

Query: `page`, `limit`, `status`, `keyword`, `parsing_status`.

Scope:
- Candidate: backend luôn scope về JD `ACTIVE`, chưa xóa.
- HR: chỉ JD do mình sở hữu; có thể lọc DRAFT/ACTIVE/CLOSED.
- Admin: toàn bộ theo filter.

Response `200`: `PaginatedJobsResponse`.

## GET `/jobs/{id}`

**Access:** Candidate chỉ JD ACTIVE; HR chỉ JD mình sở hữu; Admin toàn quyền.

Response `200`: `JobResponse`.

## PUT `/jobs/{id}`

**Access:** HR owner/Admin.

Request: `JobUpdateRequest`.

Nếu `raw_content` đổi:
- đưa `status=DRAFT`;
- đặt `parsing_status=PENDING`;
- `is_criteria_verified=false`;
- clear `job_embedding`, `embedding_model`, `parsed_at`;
- invalidate mọi Match hiện hành của JD;
- enqueue parse lại.

Response `200`: `JobResponse`.

## PATCH `/jobs/{id}/status`

**Access:** HR owner/Admin.

Request: `JobStatusRequest`.

Chỉ cho `ACTIVE` khi:
- `parsing_status=PARSED`;
- `is_criteria_verified=true`;
- chưa soft-delete;
- có embedding hợp lệ;
- có ít nhất một `job_skill` hợp lệ.

Response `200`: `JobResponse`.

Nếu chưa sẵn sàng: `422`.

## DELETE `/jobs/{id}`

**Access:** HR owner/Admin.

Soft delete. Response `204`.

## GET `/jobs/{id}/criteria`

**Access:** HR owner/Admin.

Response `200`: `JobCriteriaResponse`.

## PUT `/jobs/{id}/criteria`

**Access:** HR owner/Admin.

**Precondition:** JD `PARSED`.

Request: `JobCriteriaRequest`.

Rules:
- `skills` có ít nhất 1 phần tử;
- không duplicate `skill_id`;
- mọi `skill_id` tồn tại trong taxonomy;
- `importance` chỉ `MANDATORY|OPTIONAL`;
- transaction save + `is_criteria_verified=true`;
- **invalidate mọi Match hiện hành của JD** và clear stale payload.

Response `200`: `JobCriteriaResponse`.

## PUT `/jobs/{id}/weights`

**Access:** HR owner/Admin.

Request: `WeightsRequest`.

Rules:
- mỗi weight `[0,1]`;
- tổng `1.000`;
- **luôn invalidate** mọi Match hiện hành của JD;
- nếu `recalculate=true`, enqueue tính lại ngay;
- nếu `false`, Match vẫn `PENDING` và không xuất hiện trên leaderboard cho tới khi được tính lại.

Response `200`: `JobResponse`.

---

# MODULE E — MATCHING & ANALYTICS

## POST `/matching/calculate`

**Access:** Candidate/HR/Admin.

Request: `MatchCalculateRequest` dùng chung single/batch.

Rules chung:
- `resume_ids` tối thiểu 1, không duplicate;
- CV/JD chưa soft-delete;
- CV `PARSED`, có `resume_embedding` + `embedding_model`;
- JD `PARSED`, criteria verified, có `job_embedding` + `embedding_model`, >=1 `job_skill`;
- CV/JD embedding cùng model/dimension.

Role/ownership:
- Candidate: mọi resume ID thuộc Candidate; JD `ACTIVE`.
- HR: JD thuộc HR; mọi resume ID thuộc kho HR.
- Admin: toàn quyền.

Khi upsert pair `(job_id,resume_id)`, backend reset toàn bộ stale payload trước enqueue:

```text
status=PENDING
all scores=NULL
matched_skills=[]
missing_skills=[]
gap_analysis_summary=NULL
error_message=NULL
calculated_at=NULL
```

Response `202`: `MatchTriggerResponse`.

Ví dụ:

```json
{
  "success": true,
  "data": {
    "job_id": "uuid",
    "match_ids": ["uuid"],
    "total_matches": 1,
    "status": "PENDING"
  }
}
```

`QUEUED` không phải MatchStatus.

Errors: `403`, `404`, `422`.

## GET `/matching`

**Access:** Candidate/HR/Admin.

Mục đích: danh sách **kết quả matching hiện hành**. MVP chỉ có một row cho mỗi `(job_id,resume_id)`; endpoint này không phải lịch sử nhiều attempt.

Query: `page`, `limit`, `job_id`, `resume_id`, `status`.

Scope:
- Candidate: Match có resume thuộc Candidate.
- HR: Match chỉ khi JD thuộc HR **và** resume cũng thuộc kho HR.
- Admin: toàn bộ.

Response `200`: `PaginatedMatchesResponse`.

## GET `/matching/{match_id}`

**Access:**
- Candidate: resume của Match thuộc Candidate.
- HR: JD của Match thuộc HR **và** resume thuộc kho HR.
- Admin: toàn quyền.

Response `200`: `MatchResponse`.

PENDING/PROCESSING/FAILED: scores có thể `null`.

## GET `/matching/{match_id}/gap-analysis`

**Access:** giống GET Match.

**Precondition:** Match `COMPLETED`.

Response `200`: `GapAnalysisResponse`.

LLM explanation nếu bật không được thay đổi scores hoặc matched set.

## GET `/jobs/{id}/leaderboard`

**Access:** HR owner/Admin. Candidate bị từ chối.

Query: `page`, `limit`, `min_score`.

Rules:
- chỉ Match `COMPLETED`;
- HR chỉ thấy row có JD thuộc HR **và** `resume.owner_user_id=current_user.id`;
- Candidate self-match với JD của HR không xuất hiện cho HR trong MVP;
- sort `overall_score DESC`.

Response `200`: `LeaderboardResponse`.

---

# MODULE F — ADVANCED, CHƯA THUỘC OPENAPI MVP

## GET `/jobs/{id}/export?format=pdf|excel`

Dự kiến cho HR owner/Admin khi module xuất báo cáo được triển khai.

**Không expose trong `openapi.yaml` MVP và không trình bày như tính năng đã hoàn thành khi chưa code.** Khi triển khai phải bổ sung route vào OpenAPI, test authorization và cập nhật Traceability.

---

# 3. Quy tắc đồng bộ với code

1. Pydantic request/response schema phải dùng đúng naming tài liệu này/OpenAPI.
2. ORM map đúng `docs/05_database/schema.sql`.
3. Matching single/batch chỉ dùng `/matching/calculate`.
4. `skills.skill_kind=HARD|SOFT`; `job_skills.importance=MANDATORY|OPTIONAL`.
5. `owner_user_id` là field chuẩn của `resumes`.
6. Autocomplete skill lấy từ `/skills` hoặc cache của cùng API.
7. Criteria JD phải verified và có ít nhất một skill trước ACTIVE/matching.
8. `hybrid-v1` dùng cosine cho Semantic Score; BM25 chỉ diagnostic/experiment.
9. HR Match visibility bắt buộc kiểm **cả ownership JD và ownership CV** trong MVP.
10. Mọi thay đổi làm score stale phải invalidate row trước khi leaderboard tiếp tục sử dụng.
11. JSON endpoint chính phải có response schema thực trong OpenAPI, không chỉ `description`.
12. Nếu thay đổi route/enum/persistence field, cập nhật PTTK + Traceability trước khi code.
