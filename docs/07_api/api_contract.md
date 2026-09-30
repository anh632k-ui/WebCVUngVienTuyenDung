# API CONTRACT SPECIFICATION — MVP v1 (Round-3 Locked)

Base URL `/api/v1`. JSON UTF-8. JWT Bearer Access Token. Refresh session là Advanced và không nằm trong OpenAPI MVP.

## Quy ước response
Success: `{ "success": true, "data": ... }`.
Error: `{ "success": false, "error": {"code":"...","message":"...","details":null} }`.
`204` không body.

Status chuẩn: 200/201/202/204, 400, 401, 403, 404, 409, 413, 415, 422, 503.

# A. Auth & Account

## POST `/auth/register`
Public. Candidate/HR only. Normalize email lowercase. `201 UserResponse`; 409 duplicate; 422 invalid.

## POST `/auth/login`
Public. Trả Access Token + User. MVP không set refresh cookie. `200 LoginResponse`; 401/403/422.

## PUT `/auth/change-password`
Authenticated. Verify current password. `200 MessageResponse`.

## GET/PUT `/users/me`
Authenticated. PUT chỉ full_name/phone_number.

## GET `/admin/users`
Admin. Pagination/filter.

## PATCH `/admin/users/{id}`
Admin. `is_active` và/hoặc `role`.
Rules:
- không self-lock/self-demote;
- đổi Candidate<->HR chỉ khi target user không sở hữu Resume/JD chưa soft-delete;
- conflict trả `409 ROLE_CHANGE_CONFLICT`.

## Advanced Auth — chưa expose MVP
`POST /auth/refresh`, `POST /auth/logout` server-side chỉ thêm khi bật Refresh Rotation. Backend phải dùng HttpOnly Cookie.

# B. Skills
## GET `/skills`
Authenticated. Query keyword/skill_kind/category/page/limit. Read-only taxonomy.
Môi trường mới phải seed `skill_taxonomy_seed.sql`; pipeline không tự insert skill lạ.

# C. Resume

## POST `/resumes/upload`
Candidate/HR. PDF/DOCX <=5MB. Backend kiểm MIME/magic bytes, safe storage key.
Tạo Resume `PENDING`, `revision=1`; enqueue parse `(resume_id,expected_revision=1)`.
`202 ResumeUploadResponse`.

## GET `/resumes`
Candidate/HR chỉ owner; Admin all. Filter pagination/status/skills.

## GET `/resumes/{id}`
Owner/Admin. `candidate_profile` **nullable** vì cardinality 0..1.

## GET `/resumes/{id}/status`
Owner/Admin.

## PUT `/resumes/{id}/parsed-data`
Owner/Admin; Resume PARSED.
Transaction:
1. validate aggregate/taxonomy/date;
2. regenerate embedding;
3. increment `resume.revision`;
4. persist `embedding_model` + `embedding_preprocessing_version`;
5. invalidate all related Match: generation++, PENDING, clear scores/evidence/error/embedding provenance/calculated_at.

## GET `/resumes/{id}/download`
Owner/Admin, binary.

## DELETE `/resumes/{id}`
Owner/Admin, soft delete, 204.

# D. Job

## POST `/jobs`
**HR only**. Admin không tạo JD mới dưới identity Admin.
Server set `recruiter_id=current_user.id`, DRAFT/PENDING/revision=1; enqueue parse expected_revision=1.
`201 JobResponse`.

## GET `/jobs`
Candidate chỉ ACTIVE; HR chỉ own; Admin all.

## GET `/jobs/{id}`
Candidate ACTIVE; HR own; Admin all.

## PUT `/jobs/{id}`
HR owner/Admin existing JD.
Nếu `raw_content` đổi, transaction:
- revision++;
- DRAFT/PENDING, verified=false;
- clear embedding/model/preprocessing/parsed_at;
- invalidate Match generation++;
- commit rồi enqueue parse(new revision).

## PATCH `/jobs/{id}/status`
HR owner/Admin.
Allowed transitions:
- same state: idempotent;
- DRAFT->ACTIVE;
- ACTIVE->DRAFT|CLOSED;
- CLOSED->DRAFT|ACTIVE;
- DRAFT->CLOSED => 422.
ACTIVE cần PARSED + verified + embedding/model/preprocessing + >=1 job_skill.

## DELETE `/jobs/{id}`
Soft delete, 204.

## GET/PUT `/jobs/{id}/criteria`
HR owner/Admin. PUT requires PARSED, >=1 skill, valid taxonomy, no duplicates.
Transaction: save criteria, verified=true, revision++, invalidate Match generation++.

## PUT `/jobs/{id}/weights`
HR owner/Admin. Each [0,1], sum=1.
Transaction: weights, revision++, invalidate Match generation++.
`recalculate=true` dispatches tasks after commit.

# E. Matching

## POST `/matching/calculate`
Candidate/HR/Admin. Request:
```json
{"job_id":"uuid","resume_ids":["uuid"]}
```
Preconditions:
- entire batch ownership valid;
- Resume/JD PARSED/not deleted;
- JD verified + >=1 skill;
- embeddings present;
- same `embedding_model` and `embedding_preprocessing_version`;
- Candidate requires JD ACTIVE.

### Atomic batch rule
Validate **all IDs first**. One invalid item => 403/404/422, no Match mutation.
Then one DB transaction upserts all pairs:
- generation++;
- snapshot current resume_revision/job_revision;
- PENDING;
- clear stale score/evidence/error/embedding provenance/calculated_at.
Commit before dispatch.

### Dispatch rule
Dispatch task payload: `match_id, expected_generation, expected_resume_revision, expected_job_revision, algorithm_version`.
If any dispatcher call fails after commit: `503 TASK_DISPATCH_FAILED`. Prepared PENDING rows remain retry-safe; retry increments generation.

Success `202 MatchTriggerResponse`:
```json
{"success":true,"data":{"job_id":"uuid","match_ids":["uuid"],"total_matches":1,"status":"PENDING"}}
```

### Worker terminal-write rule
Worker may set PROCESSING/COMPLETED/FAILED only by conditional update on expected generation. Before terminal write, re-check linked Resume/JD revisions. Mismatch/rowcount=0 => stale task discard.
FAILED requires error_message. Non-FAILED error_message must be null.

## GET `/matching`
Current results only, not attempt history. Candidate by resume owner; HR requires both JD+CV ownership; Admin all.

## GET `/matching/{match_id}`
Same scope. Response includes generation/revision/provenance fields for diagnostics.

## GET `/matching/{match_id}/gap-analysis`
Same scope, Match COMPLETED.

## GET `/jobs/{id}/leaderboard`
HR owner/Admin. Only COMPLETED. HR only CV in own pool. Candidate denied.

# F. Advanced
- `/jobs/{id}/export` PDF/Excel — not OpenAPI MVP.
- Refresh Token Rotation endpoints — not OpenAPI MVP.
- LLM/XAI explanation optional.

# Scoring contract
- Skill: MANDATORY=2, OPTIONAL=1.
- Semantic: cosine; same model+preprocessing.
- Experience: only quantifiable date intervals; current uses today; missing start or non-current missing end excluded. If required>0 and no quantifiable interval => score 0.
- Overall: JD weights.

# Đồng bộ code
ORM maps schema; no `create_all()`; no new endpoint/table/enum unless PTTK updated first.
