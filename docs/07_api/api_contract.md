# API CONTRACT SPECIFICATION — MVP v1 (Reliability + Fingerprint Lock)

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

Application UUIDv5 namespace là literal cố định:

```text
bd7b1f30-b2de-549c-a8dd-8d742ee5bc12
```

Không generate namespace mới theo startup/deployment. Deterministic resource id:

```text
UUIDv5(APP_IDEMPOTENCY_NAMESPACE,
      actor_id + "\n" + canonical_route + "\n" + lower(Idempotency-Key))
```

Mỗi resource create persist `create_request_fingerprint` SHA-256 lowercase hex 64 ký tự.
- Resume fingerprint = SHA-256(raw file bytes).
- Job fingerprint = SHA-256(canonical validated create payload sau defaults).

Retry cùng actor/route/key:
- fingerprint giống persisted fingerprint => trả đúng resource hiện hữu, không create side-effect mới;
- fingerprint khác => `409 IDEMPOTENCY_KEY_REUSED`;
- concurrent deterministic PK conflict => re-read row, compare fingerprint rồi return existing/409.

### Job canonical payload v1
- fields: `title`, `job_level`, `location`, `raw_content`, `w_skill`, `w_semantic`, `w_experience`;
- server apply defaults + validation trước hashing;
- Unicode NFC;
- trim outer whitespace `title/job_level/location`; empty `location` -> null;
- normalize `raw_content` CRLF/CR -> LF, giữ nội dung còn lại;
- weights serialize fixed 3 decimals;
- UTF-8 JSON, fixed key order, no extra whitespace.

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
- `role` chỉ nhận `CANDIDATE` hoặc `HR`; endpoint chỉ hỗ trợ `CANDIDATE <-> HR`;
- không thể gán role `ADMIN`, và target đang là `ADMIN` không thể bị đổi role tại endpoint này;
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
Candidate/HR. **Required `Idempotency-Key` UUID**. PDF/DOCX <=5MB. Backend đọc bytes, kiểm MIME/magic bytes, rồi tính SHA-256 raw bytes làm request fingerprint.

Persistence flow:
1. derive deterministic `resume_id` từ fixed namespace + actor + canonical route `/api/v1/resumes/upload` + Idempotency-Key;
2. lookup resource trước create side-effect;
3. nếu Resume đã tồn tại:
   - fingerprint giống => trả cùng Resume, không overwrite storage; nếu vẫn PENDING có thể best-effort re-dispatch current revision;
   - fingerprint khác => `409 IDEMPOTENCY_KEY_REUSED`;
4. nếu DB row chưa tồn tại, canonical storage key = `resumes/{resume_id}/source`;
5. storage `put-if-absent`/no-overwrite:
   - object mới => giữ object;
   - object đã tồn tại => compute SHA-256 bytes object; giống request fingerprint thì reuse, khác => `409 IDEMPOTENCY_KEY_REUSED` và không overwrite;
6. INSERT Resume `PENDING`, `revision=1`, persist `create_request_fingerprint`;
7. nếu INSERT thua concurrent deterministic-PK race: re-read row, compare fingerprint; giống => same existing resource, khác => 409;
8. commit persistence;
9. best-effort dispatch parse `(resume_id,current_revision)`.

Nếu storage write thành công nhưng DB insert thất bại, object được phép còn lại như orphan tạm thời. Retry cùng key/file phải hash-verify object hiện hữu trước reuse. Không cleanup/overwrite mù vì có thể có concurrent request hợp lệ.

Nếu dispatcher lỗi sau commit, **không rollback Resume và không tạo Resume khác**. Server log lỗi, Resume giữ `PENDING`; internal Parse Recovery Sweeper sẽ re-dispatch current revision. Endpoint vẫn trả `202 ResumeUploadResponse` của resource đã persist.

Responses:
- `202 ResumeUploadResponse` — created/existing cùng fingerprint;
- `409 IDEMPOTENCY_KEY_REUSED` — cùng key/deterministic id nhưng request/storage/persisted fingerprint khác;
- 413/415/422 theo validation.

## GET `/resumes`
Candidate/HR chỉ owner; Admin all; chỉ trả Resume có `is_deleted=false`. Query hỗ trợ `page`, `limit`, `keyword`, `parsing_status`; các filter được kết hợp với scope ownership/deleted-state.

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

Flow:
1. validate request + apply defaults;
2. canonicalize create payload v1 và SHA-256 -> request fingerprint;
3. derive deterministic `job_id` từ fixed namespace + HR id + canonical route `/api/v1/jobs` + key;
4. existing JD + same fingerprint => trả same JD; existing JD + different fingerprint => `409 IDEMPOTENCY_KEY_REUSED`;
5. nếu chưa có row: create `DRAFT/PENDING/revision=1`, persist `create_request_fingerprint`;
6. deterministic-PK concurrent loser re-read row và compare fingerprint trước return existing/409;
7. commit DB trước dispatch;
8. dispatcher lỗi => JD vẫn PENDING, log lỗi và Parse Recovery Sweeper re-dispatch.

Responses:
- `201 JobResponse` — created/existing cùng fingerprint;
- `409 IDEMPOTENCY_KEY_REUSED` — cùng key nhưng canonical create fingerprint khác;
- 403/422 theo authorization/validation.

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
HR owner/Admin, JD chưa xóa và current `PARSED`; trạng thái parse khác trả `422 JOB_NOT_READY`. Cả ba JSON-number weights bắt buộc, mỗi số [0,1], fit chính xác `NUMERIC(4,3)`, tổng Decimal đúng `1.000`; `recalculate` là strict boolean, mặc định false.
Transaction khóa Job -> Resume UUID -> Match ID: weights, revision++, invalidate toàn bộ Match generation++ + refresh snapshots + clear stale payload. Mỗi accepted PUT luôn là mutation kể cả cùng weights; không tạo Match, reparse hoặc đổi embedding/criteria/status/fingerprint.
`recalculate=true` best-effort dispatches immutable five-field tasks after commit. Publication failure được log generic, không rollback, không dừng batch và response vẫn `200 JobResponse`; false chỉ tắt immediate publication. Policy này không đổi `POST /matching/calculate` 503.

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
ORM maps schema; no `create_all()`; no new endpoint/table/enum/concurrency/idempotency semantic unless PTTK updated first.
