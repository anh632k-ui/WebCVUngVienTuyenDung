# ĐẶC TẢ USE CASE CHI TIẾT — BASELINE VÒNG 3

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
Candidate/HR upload PDF/DOCX <=5MB. Backend validate file thật, lưu safe key, tạo Resume `PENDING`, `revision=1`, enqueue parse với `expected_revision=1`, trả 202.

## UC08 — Phân tích CV
Task nhận `(resume_id, expected_revision)`.
Flow: conditional claim PROCESSING -> extract/preprocess -> entity/skill -> embed -> conditional terminal commit nếu `resumes.revision=expected_revision`.
Nếu revision khác: task stale, discard, không ghi FAILED lên version mới.
PARSED lưu `embedding_model` + `embedding_preprocessing_version`.

## UC09 — Rà soát CV
Owner/Admin chỉnh parsed data của Resume PARSED.
Trong transaction:
1. validate aggregate;
2. regenerate canonical embedding;
3. `resume.revision += 1`;
4. save structured data + model/preprocessing;
5. invalidate Match liên quan: generation++, PENDING, clear stale payload.
`candidate_profile` là 0..1; response detail cho phép null khi chưa có profile.

## UC10 — Kho CV
List/detail/status/download/soft-delete theo owner; Admin override.

## UC11 — Quản lý JD
**Create chỉ HR**. POST tạo `recruiter_id=current_user.id`, DRAFT/PENDING, revision=1, enqueue parse(expected_revision=1).
Admin có thể quản trị JD đã tồn tại nhưng không tạo JD dưới identity Admin.

Update raw_content:
- revision++;
- DRAFT/PENDING;
- verified=false;
- clear embedding/model/preprocessing/parsed_at;
- invalidate Match + generation++;
- enqueue parse với revision mới.

Status transition:
- DRAFT->ACTIVE khi ready;
- ACTIVE->DRAFT|CLOSED;
- CLOSED->DRAFT|ACTIVE khi ready;
- same-state idempotent;
- DRAFT->CLOSED => 422.

## UC12 — Phân tích JD
Task `(job_id,expected_revision)`.
Extract criteria -> taxonomy -> MANDATORY/OPTIONAL -> embedding.
Terminal write chỉ nếu JD revision vẫn expected; stale task discard.

## UC13 — Rà soát criteria JD
HR owner/Admin, JD PARSED.
Validate >=1 skill, taxonomy IDs, no duplicate.
Trong transaction: save criteria, verified=true, `job.revision += 1`, invalidate related Match + generation++.

## UC14 — Xem JD đang tuyển
Candidate list/detail JD ACTIVE chưa xóa.

## UC15 — Tính tương thích
Candidate: CV mình + JD ACTIVE. HR: JD mình + CV kho mình. Admin vận hành.

### Batch semantics
1. Validate **toàn bộ** `resume_ids` trước mutation.
2. Một item invalid => cả request fail, không sửa Match nào.
3. Một transaction upsert tất cả Match: generation++, snapshot current Resume/JD revisions, PENDING, clear stale payload.
4. Commit.
5. Dispatch task từng Match sau commit.
6. Nếu dispatch lỗi => `503 TASK_DISPATCH_FAILED`; các PENDING row retry-safe. Retry request tăng generation nên task cũ không overwrite.

Worker payload: match_id + expected generation + expected resume/job revisions + algorithm version.
Before PROCESSING và trước terminal write phải compare expected values. Rowcount 0 => stale task discard.

## UC16 — Kết quả hiện hành & Skill Gap
Candidate đọc Match CV mình. HR chỉ khi JD và CV đều thuộc HR. Admin override.
COMPLETED hiển thị score/evidence; PENDING/PROCESSING/FAILED hiển thị lifecycle. FAILED phải có error.

## UC17 — Leaderboard
HR owner JD/Admin. Chỉ Match COMPLETED; HR chỉ CV kho mình; sort overall DESC.

## UC18 — Trọng số
HR owner/Admin. Validate từng weight [0,1], tổng=1.
Trong transaction: update weights, `job.revision += 1`, invalidate Match + generation++.
`recalculate=true` chỉ dispatch ngay; false để PENDING.

## UC19 — Export [Advanced]
PDF/Excel dự kiến; không nằm OpenAPI MVP.

# Quy tắc scoring hỗ trợ UC15
- Skill: MANDATORY=2, OPTIONAL=1.
- Semantic: cosine, chỉ khi model + preprocessing version giống nhau.
- Experience: chỉ interval định lượng; thiếu start_date hoặc non-current thiếu end_date thì không cộng. Nếu required>0 mà không có interval định lượng => 0.
- Overall theo weights JD.
