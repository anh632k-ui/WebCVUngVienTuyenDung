# FINAL PTTK AUDIT REPORT — ROUND 3

## Phạm vi
Requirements -> BFD -> Use Case -> Activity -> Sequence -> Database -> Architecture/AI -> API Contract/OpenAPI -> Traceability -> Delivery Gate.

## Baseline canonical
- 10 bảng nghiệp vụ.
- Resume owner=`owner_user_id`; JD owner=`recruiter_id`.
- Candidate self-match không lộ cho HR.
- `skill_kind=HARD|SOFT`; `importance=MANDATORY|OPTIONAL`.
- vector(1024), cosine HNSW.
- current Match unique `(job_id,resume_id)`.

## Các lỗi vòng 1-2 đã xử lý
Candidate profile thiếu; parse/business state lẫn; scores NOT NULL khi pending; route matching lệch; API/OpenAPI lệch; privacy HR/Candidate; stale payload; email CI uniqueness; advanced export exposure.

## Semantic review vòng 3 — lỗi và cách khóa

### 1. Stale background worker race — FIXED
Thêm `resumes.revision`, `job_descriptions.revision`, `match_results.generation`, `resume_revision`, `job_revision`.
Parse task dùng expected_revision. Match task dùng expected generation + revisions. Conditional terminal write; mismatch/rowcount=0 => stale task discard.

### 2. Skill Taxonomy bootstrap — FIXED
Thêm `05_database/skill_taxonomy_seed.sql`. Reset environment bắt buộc chạy schema rồi seed. Pipeline không tự insert skill lạ.

### 3. Role lifecycle — FIXED
Admin đổi Candidate<->HR chỉ khi target không có Resume/JD chưa soft-delete. Conflict 409. Không tự reinterpret ownership.

### 4. CandidateProfile cardinality — FIXED
Canonical 0..1 ở requirements/CDM/LDM/PDM/ERD. OpenAPI `candidate_profile` nullable.

### 5. Refresh Token scope — FIXED
Refresh/logout server-side chuyển Advanced, không nằm OpenAPI MVP. MVP login dùng Access Token JWT.

### 6. Preprocessing version — FIXED
Resume/JD lưu `embedding_preprocessing_version`; Match COMPLETED lưu provenance model + preprocessing. Matching yêu cầu CV/JD cùng cả hai.

### 7. JD status transitions — FIXED
Allowed: same-state, DRAFT->ACTIVE, ACTIVE->DRAFT|CLOSED, CLOSED->DRAFT|ACTIVE. DRAFT->CLOSED => 422.

### 8. Admin tạo JD ambiguity — FIXED
POST /jobs chỉ HR. Admin chỉ override quản trị JD đã tồn tại.

### 9. Batch matching atomicity/dispatch — FIXED
Validate entire batch before mutation; one transaction prepare all rows; dispatch after commit. Dispatcher failure => 503; PENDING rows retry-safe nhờ generation.

### 10. Experience missing dates — FIXED
Chỉ interval định lượng; current end=today; missing start hoặc non-current missing end bị exclude. required>0 mà không có interval định lượng => 0.

### 11. Error lifecycle — FIXED
FAILED bắt buộc error_message; non-FAILED phải NULL. Non-COMPLETED clear stale score/evidence/embedding provenance/calculated_at.

## Runtime checks còn lại
PTTK tĩnh chỉ chuyển `IMPLEMENTATION_READY` sau:
1. CI HEAD cuối vòng 3 PASS.
2. PR merge.
3. Reset DB.
4. Run schema + taxonomy seed.
5. Smoke test constraints/indexes/error states/revisions.

## Kết luận
Sau vòng 3, các blocker đã biết về concurrency, bootstrap taxonomy, role transition, cardinality, Advanced auth scope, embedding reproducibility, status transition, JD ownership, batch semantics, missing-date scoring và error lifecycle đã được khóa trong tài liệu canonical.
