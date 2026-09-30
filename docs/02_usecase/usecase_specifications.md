# ĐẶC TẢ USE CASE CHI TIẾT — RELIABILITY LOCK

## UC01 — Đăng ký
Actor Guest. Tạo Candidate/HR; normalize email; password hash; 201. Trùng email 409.

## UC02 — Đăng nhập
Actor Guest. Xác minh credential + `is_active`; trả JWT Access Token + user. MVP không bắt buộc refresh session.

## UC03 — Refresh/Logout [Advanced]
Không nằm trong OpenAPI MVP. Khi bật: backend quản lý refresh token HttpOnly, rotation/revoke; frontend không đọc refresh token bằng JS.

## UC04 — Profile
Authenticated user GET/PUT `/users/me`; không nhận role/id từ body.

## UC05 — Đổi mật khẩu
Verify mật khẩu hiện tại, hash mật khẩu mới. Nếu Advanced refresh session bật thì revoke sessions cũ.

## UC06 — Quản trị user
Admin list/lock/unlock/change role.
- Không self-lock/self-demote.
- Đổi `CANDIDATE <-> HR` chỉ khi target user không có Resume hoặc JD chưa soft-delete.
- Nếu còn resource: `409 ROLE_CHANGE_CONFLICT`; không tự reinterpret ownership.

## UC07 — Upload CV
Candidate/HR upload PDF/DOCX <=5MB.
- Request bắt buộc `Idempotency-Key` UUID.
- Backend validate file thật rồi tính `request_fingerprint = SHA-256(raw file bytes)` lowercase hex.
- Dùng application UUIDv5 namespace cố định `bd7b1f30-b2de-549c-a8dd-8d742ee5bc12` để derive deterministic `resume_id` từ actor + canonical route + key.
- Nếu deterministic Resume đã tồn tại:
  - fingerprint giống `create_request_fingerprint` => trả cùng Resume, không overwrite storage; có thể best-effort re-dispatch nếu vẫn PENDING;
  - fingerprint khác => `409 IDEMPOTENCY_KEY_REUSED`.
- Nếu DB chưa có Resume, storage key canonical là `resumes/{resume_id}/source`, dùng put-if-absent/no-overwrite.
  - object chưa tồn tại => ghi bytes;
  - object đã tồn tại => compute SHA-256 object; giống request fingerprint thì reuse, khác thì 409 và không overwrite.
- INSERT Resume `PENDING`, `revision=1`, persist `create_request_fingerprint`; nếu concurrent INSERT thua PK race thì re-read row và compare fingerprint trước khi return existing/409.
- Commit rồi best-effort enqueue `(resume_id,current_revision)`.
- Dispatcher lỗi sau commit: Resume vẫn PENDING, response vẫn 202; internal recovery sweeper re-dispatch sau grace window.
- Nếu storage write đã thành công nhưng DB insert thất bại, object được giữ làm orphan tạm; retry cùng key/file sẽ hash-verify rồi reuse, không nhận nhầm file khác.

## UC08 — Phân tích CV
Task nhận `(resume_id, expected_revision)`.

`revision` là version của input/computation request. Initial parse dùng revision 1; nếu input/reparse đổi thì service tăng revision trước enqueue. Worker của chính expected revision không tăng revision khi commit.

Exclusive flow:
1. CAS claim `PENDING -> PROCESSING` chỉ khi `resumes.revision=expected_revision AND is_deleted=false`.
2. `rowcount=0` => stale/duplicate/deleted task discard.
3. extract/preprocess -> entity/skill -> embed.
4. terminal transaction lock/check vẫn `PROCESSING + expected_revision + is_deleted=false`.
5. persist aggregate + embedding provenance; CAS terminal `PROCESSING -> PARSED` với cùng guard.
6. lỗi xử lý chỉ được CAS `PROCESSING -> FAILED` cùng expected revision và chưa xóa.

Nhờ status/deleted-state CAS, duplicate delivery cùng revision chỉ một worker claim được và concurrent soft-delete làm terminal write fail/rollback.
PARSED lưu `embedding_model` + `embedding_preprocessing_version`.

## UC09 — Rà soát CV
Owner/Admin chỉnh parsed data của Resume PARSED, chưa xóa.
Trong transaction:
1. validate aggregate;
2. regenerate canonical embedding;
3. `resume.revision += 1` vì đây là direct input mutation;
4. save structured data + model/preprocessing;
5. invalidate Match liên quan: generation++, refresh revision snapshots, PENDING, clear stale payload.
`candidate_profile` là 0..1; response detail cho phép null khi chưa có profile.

## UC10 — Kho CV
List/detail/status/download/soft-delete theo owner; Admin override. Sau soft-delete, resource bị loại khỏi nghiệp vụ active/current và mọi async terminal write mới phải fail guard.

## UC11 — Quản lý JD
**Create chỉ HR**.
- POST bắt buộc `Idempotency-Key` UUID.
- Apply defaults/validation rồi canonicalize payload v1: Unicode NFC; trim title/job_level/location; empty location -> null; normalize raw_content line endings CRLF/CR -> LF; serialize weights fixed 3 decimals; UTF-8 JSON với key order cố định/no extra whitespace.
- `request_fingerprint = SHA-256(canonical JSON)` lowercase hex.
- Dùng stable namespace `bd7b1f30-b2de-549c-a8dd-8d742ee5bc12` để derive deterministic `job_id` từ actor + canonical route + key.
- Nếu JD deterministic đã tồn tại: fingerprint giống => trả same JD; fingerprint khác => `409 IDEMPOTENCY_KEY_REUSED`.
- Nếu chưa tồn tại: tạo `recruiter_id=current_user.id`, DRAFT/PENDING, revision=1, persist `create_request_fingerprint`.
- Nếu concurrent INSERT thua deterministic PK race: re-read row, compare fingerprint; same => return existing, different => 409.
- Commit rồi best-effort enqueue parse(expected_revision=1).
- Dispatcher lỗi sau commit giữ JD PENDING và recovery sweeper sẽ re-dispatch.

Admin có thể quản trị JD đã tồn tại nhưng không tạo JD dưới identity Admin.

Update raw_content:
- chỉ JD chưa xóa;
- revision++ **trước** reparse mới;
- DRAFT/PENDING;
- verified=false;
- clear embedding/model/preprocessing/parsed_at;
- invalidate Match + generation++ + refresh snapshots;
- commit;
- best-effort enqueue parse với revision mới; dispatcher lỗi được recovery xử lý.

Status transition:
- DRAFT->ACTIVE khi ready;
- ACTIVE->DRAFT|CLOSED;
- CLOSED->DRAFT|ACTIVE khi ready;
- same-state idempotent;
- DRAFT->CLOSED => 422.

## UC12 — Phân tích JD
Task `(job_id,expected_revision)`.
- CAS claim `PENDING -> PROCESSING` chỉ khi revision expected và `is_deleted=false`.
- duplicate/stale/deleted claim rowcount 0 => discard.
- extract criteria -> taxonomy -> MANDATORY/OPTIONAL -> embedding.
- terminal PARSED/FAILED chỉ từ `PROCESSING` của cùng expected revision và JD vẫn chưa xóa.
- worker không tăng revision khi commit output.
- soft-delete trong lúc worker chạy => terminal CAS fail và transaction output phải rollback/discard.

## UC13 — Rà soát criteria JD
HR owner/Admin, JD PARSED và chưa xóa.
Validate >=1 skill, taxonomy IDs, no duplicate.
Trong transaction: save criteria, verified=true, `job.revision += 1`, invalidate related Match + generation++ + refresh snapshots.

## UC14 — Xem JD đang tuyển
Candidate list/detail JD ACTIVE chưa xóa.

## UC15 — Tính tương thích
Candidate: CV mình + JD ACTIVE. HR: JD mình + CV kho mình. Admin vận hành. Resume/JD phải đều chưa soft-delete.

### Batch semantics
1. Validate **toàn bộ** `resume_ids` trước mutation, gồm deleted-state.
2. Một item invalid => cả request fail, không sửa Match nào.
3. Một transaction upsert tất cả Match: generation++, snapshot current Resume/JD revisions, PENDING, clear stale payload.
4. Commit.
5. Dispatch task từng Match sau commit.
6. Nếu dispatch lỗi => `503 TASK_DISPATCH_FAILED`; các PENDING row retry-safe. Retry request tăng generation nên task cũ không overwrite.

Worker payload: match_id + expected generation + expected resume/job revisions + algorithm version.

Exclusive worker flow:
1. CAS claim `PENDING -> PROCESSING` chỉ khi generation, stored snapshots, linked Resume/JD revisions đều khớp expected **và linked Resume/JD `is_deleted=false`**.
2. Chỉ `rowcount=1` được compute.
3. terminal COMPLETED/FAILED chỉ từ `PROCESSING` khi cùng expected generation/snapshots/linked revisions vẫn còn hiệu lực và linked resources vẫn chưa xóa.
4. duplicate/stale/deleted-resource task `rowcount=0` => discard.

FAILED và COMPLETED cùng đòi exclusive PROCESSING claim nên không được race terminal state trên cùng generation.

## UC16 — Kết quả hiện hành & Skill Gap
Candidate đọc Match CV mình. HR chỉ khi JD và CV đều thuộc HR. Admin override.
Current Match queries phải join/filter Resume và JD chưa soft-delete. COMPLETED hiển thị score/evidence; PENDING/PROCESSING/FAILED hiển thị lifecycle. FAILED phải có error.

## UC17 — Leaderboard
HR owner JD/Admin. Chỉ Match COMPLETED, JD chưa xóa và Resume chưa xóa; HR chỉ CV kho mình; sort overall DESC.

## UC18 — Trọng số
HR owner/Admin. Validate từng weight [0,1], tổng=1; JD phải chưa xóa.
Trong transaction: update weights, `job.revision += 1`, invalidate Match + generation++ + refresh snapshots.
`recalculate=true` chỉ dispatch ngay; false để PENDING.

## UC19 — Export [Advanced]
PDF/Excel dự kiến; không nằm OpenAPI MVP.

# Idempotency — cross-cutting UC07/11
- Stable UUIDv5 namespace là literal `bd7b1f30-b2de-549c-a8dd-8d742ee5bc12`, không rotate giữa deployment.
- Persist SHA-256 fingerprint trên resource hiện có, không thêm bảng thứ 11.
- Same key + same fingerprint => same resource.
- Same key + different fingerprint => `409 IDEMPOTENCY_KEY_REUSED`.
- Deterministic PK conflict phải re-read persisted resource và compare fingerprint.
- Resume orphan storage phải hash-verify trước reuse; mismatch không overwrite.

# Parse Recovery — cross-cutting UC07/08/11/12
Internal component, không expose API route:
- startup/periodic scan Resume/JD `PENDING`, `is_deleted=false`, `updated_at` quá grace window;
- enqueue lại `(resource_id,current_revision)`;
- không tăng revision và không đổi state trước enqueue;
- duplicate re-dispatch an toàn vì claim CAS đòi PENDING;
- MVP không tự reset PROCESSING timeout nếu chưa có lease/attempt token.

# Quy tắc scoring hỗ trợ UC15
- Skill: MANDATORY=2, OPTIONAL=1.
- Semantic: cosine, chỉ khi model + preprocessing version giống nhau.
- Experience: chỉ interval định lượng; thiếu start_date hoặc non-current thiếu end_date thì không cộng. Nếu required>0 mà không có interval định lượng => 0.
- Overall theo weights JD.
