# API CONTRACT SPECIFICATION — MVP v1 (Post-Round-3 Reliability Lock)

Base URL `/api/v1`. JSON UTF-8. JWT Bearer Access Token. Refresh session là Advanced và không nằm trong OpenAPI MVP.

## Quy ước response
Success: `{ "success": true, "data": ... }`.
Error: `{ "success": false, "error": {"code":"...","message":"...","details":null} }`.
`204` không body.

Status chuẩn: 200/201/202/204, 400, 401, 403, 404, 409, 413, 415, 422, 503.

## Idempotency-Key cho create endpoint
`POST /resumes/upload` và `POST /jobs` bắt buộc header:

```http
Idempotency-Key: <UUID>
```

Một key đại diện cho **một logical create request**. Server derive resource UUID deterministic bằng UUIDv5 từ application namespace + actor id + route + key. Retry cùng actor/route/key trả cùng resource, không tạo duplicate. Muốn tạo resource mới phải dùng key mới.

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
Candidate/HR. **Required `Idempotency-Key` UUID**. PDF/DOCX <=5MB. Backend kiểm MIME/magic bytes, safe storage key.

Persistence flow:
1. derive deterministic `resume_id` từ actor+route+Idempotency-Key;
2. lookup resource trước create side-effect;
3. nếu đã tồn tại: trả cùng Resume, không overwrite storage; nếu vẫn PENDING có thể best-effort re-dispatch current revision;
4. nếu chưa tồn tại: storage `put-if-absent`/no-overwrite, tạo Resume `PENDING`, `revision=1`, commit persistence;
5. best-effort dispatch parse `(resume_id,current_revision)`.

Nếu dispatcher lỗi sau commit, **không rollback Resume và không tạo Resume khác**. Server log lỗi, Resume giữ `PENDING`; internal Parse Recovery Sweeper sẽ re-dispatch current revision. Endpoint vẫn trả `202 ResumeUploadResponse` của resource đã persist.

## GET `/resumes`
Candidate/HR chỉ owner; Admin all. Mặc định chỉ `is_deleted=false`. Filter pagination/status/skills.

## GET `/resumes/{id}`
Owner/Admin, resource chưa soft-delete. `candidate_profile` **nullable** vì cardinality 0..1.

## GET `/resumes/{id}/status`
Owner/Admin, resource chưa soft-delete.

## PUT `/resumes/{id}/parsed-data`
Owner/Admin; Resume PARSED và chưa soft-delete.
Transaction:
1. validate aggregate/taxonomy/date;
2. regenerate embedding;
3. increment `resume.revision` **trong mutation trước khi coi đây là input version mới**;
4. persist `embedding_model` + `embedding_preprocessing_version`;
5. invalidate all related Match: generation++, refresh revision snapshots, PENDING, clear scores/evidence/error/embedding provenance/calculated_at.

## GET `/resumes/{id}/download`
Owner/Admin, resource chưa xóa, binary.

## DELETE `/resumes/{id}`
Owner/Admin, soft delete, 204. Sau khi soft-delete, async parse/matching terminal write mới phải bị delete-aware CAS chặn.

### Resume parse worker contract
`revision` là version của input/computation request. Initial create dùng revision 1; reparse input mới tăng revision **trước enqueue**. Worker của chính `expected_revision` không tăng revision khi commit.

Exclusive claim:
```text
UPDATE resumes
SET parsing_status='PROCESSING', updated_at=NOW()
WHERE id=:id
  AND revision=:expected_revision
  AND parsing_status='PENDING'
  AND is_deleted=FALSE
```
Chỉ `rowcount=1` được tiếp tục.

Terminal success/failure chỉ từ cùng claim:
```text
WHERE id=:id
  AND revision=:expected_revision
  AND parsing_status='PROCESSING'
  AND is_deleted=FALSE
```
Success nhiều bảng phải transaction + lock/check state/revision/delete-state trước khi persist aggregate. `rowcount=0` => stale/duplicate/deleted-resource discard.

# D. Job

## POST `/jobs`
**HR only**. Admin không tạo JD mới dưới identity Admin. **Required `Idempotency-Key` UUID**.

Server derive deterministic `job_id` từ HR+route+key, create `DRAFT/PENDING/revision=1` nếu chưa tồn tại; retry cùng key trả cùng JD. Commit DB trước dispatch. Dispatcher lỗi => JD vẫn PENDING, log lỗi và Parse Recovery Sweeper re-dispatch; response vẫn `201 JobResponse` của resource đã persist.

## GET `/jobs`
Candidate chỉ ACTIVE và chưa xóa; HR chỉ own chưa xóa; Admin all theo scope quản trị.

## GET `/jobs/{id}`
Candidate ACTIVE chưa xóa; HR own chưa xóa; Admin theo quyền quản trị.

## PUT `/jobs/{id}`
HR owner/Admin existing JD, chưa soft-delete.
Nếu `raw_content` đổi, transaction:
- revision++ **trước computation/reparse mới**;
- DRAFT/PENDING, verified=false;
- clear embedding/model/preprocessing/parsed_at;
- invalidate Match generation++ + refresh snapshots;
- commit rồi best-effort enqueue parse(new revision).
Dispatcher lỗi không rollback update; recovery sẽ re-dispatch JD PENDING.

## PATCH `/jobs/{id}/status`
HR owner/Admin, JD chưa xóa.
Allowed transitions:
- same state: idempotent;
- DRAFT->ACTIVE;
- ACTIVE->DRAFT|CLOSED;
- CLOSED->DRAFT|ACTIVE;
- DRAFT->CLOSED => 422.
ACTIVE cần PARSED + verified + embedding/model/preprocessing + >=1 job_skill.

## DELETE `/jobs/{id}`
Soft delete, 204. Sau delete, parse/matching terminal write mới phải bị CAS chặn và current analytics phải loại JD này.

## GET/PUT `/jobs/{id}/criteria`
HR owner/Admin, JD chưa xóa. PUT requires PARSED, >=1 skill, valid taxonomy, no duplicates.
Transaction: save criteria, verified=true, revision++, invalidate Match generation++ + refresh snapshots.

## PUT `/jobs/{id}/weights`
HR owner/Admin, JD chưa xóa. Each [0,1], sum=1.
Transaction: weights, revision++, invalidate Match generation++ + refresh snapshots.
`recalculate=true` dispatches tasks after commit.

### JD parse worker contract
Exclusive claim chỉ:
```text
revision=expected_revision AND parsing_status='PENDING' AND is_deleted=FALSE
```
Terminal PARSED/FAILED chỉ:
```text
revision=expected_revision AND parsing_status='PROCESSING' AND is_deleted=FALSE
```
Worker commit parsed output không tăng revision. Duplicate delivery chỉ một worker claim được. Concurrent soft-delete làm terminal CAS fail/rollback.

# E. Parse Recovery Sweeper
Internal component, không expose route MVP.

- startup/periodic scan Resume/JD `PENDING`, `is_deleted=false`, `updated_at` cũ hơn configurable grace window;
- re-dispatch `(resource_id,current_revision)`;
- không tăng revision, không đổi status trước enqueue;
- duplicate re-dispatch an toàn vì exclusive claim yêu cầu `PENDING`;
- MVP **không** tự reset stale `PROCESSING` về PENDING; worker-crash recovery muốn làm phải bổ sung lease/attempt token để không resurrect race.

# F. Matching

## POST `/matching/calculate`
Candidate/HR/Admin. Request:
```json
{"job_id":"uuid","resume_ids":["uuid"]}
```
Preconditions:
- entire batch ownership valid;
- Resume/JD PARSED và `is_deleted=false`;
- JD verified + >=1 skill;
- embeddings present;
- same `embedding_model` and `embedding_preprocessing_version`;
- Candidate requires JD ACTIVE.

### Atomic batch rule
Validate **all IDs first**, gồm deleted-state. One invalid item => 403/404/422, no Match mutation.
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

### Matching worker exclusive CAS
Claim chỉ được `PENDING -> PROCESSING` khi:
- `generation=expected_generation`;
- stored `resume_revision/job_revision` snapshots khớp expected;
- linked Resume/JD current revisions khớp expected;
- linked Resume/JD `is_deleted=false`.

Chỉ worker có `rowcount=1` được tính.

Terminal `PROCESSING -> COMPLETED|FAILED` cũng phải kiểm lại cùng generation/snapshots/linked revisions, current status `PROCESSING` và linked resources vẫn chưa soft-delete. Duplicate delivery/stale/deleted task `rowcount=0` => discard. Vì chỉ một worker claim PENDING, FAILED và COMPLETED không được race trên cùng generation.

FAILED requires error_message. Non-FAILED error_message must be null.

## GET `/matching`
Current results only, not attempt history. Candidate by resume owner; HR requires both JD+CV ownership; Admin all. Query phải join/filter linked Resume/JD `is_deleted=false`.

## GET `/matching/{match_id}`
Same scope và linked resources phải chưa soft-delete. Response includes generation/revision/provenance fields for diagnostics.

## GET `/matching/{match_id}/gap-analysis`
Same scope, Match COMPLETED, linked resources chưa xóa.

## GET `/jobs/{id}/leaderboard`
HR owner/Admin. Only COMPLETED. HR only CV in own pool. JD và Resume phải `is_deleted=false`. Candidate denied.

# G. Advanced
- `/jobs/{id}/export` PDF/Excel — not OpenAPI MVP.
- Refresh Token Rotation endpoints — not OpenAPI MVP.
- LLM/XAI explanation optional.

# Scoring contract
- Skill: MANDATORY=2, OPTIONAL=1.
- Semantic: cosine; same model+preprocessing.
- Experience: only quantifiable date intervals; current uses today; missing start or non-current missing end excluded. If required>0 and no quantifiable interval => score 0.
- Overall: JD weights.

# Đồng bộ code
ORM maps schema; no `create_all()`; no new endpoint/table/enum/concurrency semantic unless PTTK updated first.
