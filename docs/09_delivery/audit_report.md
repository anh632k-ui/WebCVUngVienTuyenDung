# FINAL PTTK AUDIT REPORT — RELIABILITY + FINGERPRINT LOCK

## Phạm vi
Requirements -> BFD -> Use Case -> Activity -> Sequence -> Database -> Architecture/AI -> API Contract/OpenAPI -> Traceability -> Delivery Gate.

## Baseline canonical
- 10 bảng nghiệp vụ.
- Resume owner=`owner_user_id`; JD owner=`recruiter_id`.
- Candidate self-match không lộ cho HR.
- `skill_kind=HARD|SOFT`; `importance=MANDATORY|OPTIONAL`.
- vector(1024), cosine HNSW.
- current Match unique `(job_id,resume_id)`.
- Resume/JD persist `create_request_fingerprint` SHA-256; không thêm bảng idempotency thứ 11.

## Các lỗi vòng 1-3 đã xử lý
Candidate profile thiếu; parse/business state lẫn; scores NOT NULL khi pending; route matching lệch; API/OpenAPI lệch; privacy HR/Candidate; stale payload; email CI uniqueness; advanced export exposure; stale worker revision/generation; taxonomy bootstrap; role lifecycle; preprocessing provenance; JD transitions; Admin-create ambiguity; batch atomicity; missing-date scoring; error lifecycle.

## Review sau vòng 3 — reliability/concurrency

### R1. Duplicate delivery có thể cùng claim một revision/generation — FIXED
Đã khóa exclusive CAS:
- CV/JD parse claim: `expected_revision + PENDING + is_deleted=false -> PROCESSING`;
- CV/JD terminal: `expected_revision + PROCESSING + is_deleted=false -> PARSED|FAILED`;
- Match claim: expected generation + stored snapshots + linked revisions + linked not-deleted + `PENDING -> PROCESSING`;
- Match terminal: same expected values + linked not-deleted + `PROCESSING -> COMPLETED|FAILED`.

`rowcount=0` luôn được coi là stale/duplicate/deleted-resource và discard.

### R2. Parse dispatcher lỗi sau DB commit có thể để resource PENDING vô hạn — FIXED
- Resume/JD commit trước;
- best-effort immediate dispatch;
- dispatcher lỗi không rollback resource đã persist;
- startup/periodic Parse Recovery Sweeper scan PENDING + `is_deleted=false` quá grace window và re-dispatch current revision;
- recovery không tăng revision, không đổi state trước enqueue;
- MVP không blind-reset PROCESSING timeout khi chưa có lease/attempt token.

### R3. Retry create sau lost response có thể tạo Resume/JD thứ hai — FIXED
Hai create endpoint bắt buộc `Idempotency-Key` UUID. Resource id derive bằng UUIDv5 từ stable namespace + actor + canonical route + key.

Application namespace bị khóa literal:
`bd7b1f30-b2de-549c-a8dd-8d742ee5bc12`.

### R4. Cùng Idempotency-Key bị reuse cho payload khác — FIXED
Đây là reliability gap cuối được phát hiện sau khi review branch topology.

- Resume fingerprint = SHA-256 raw file bytes.
- Job fingerprint = SHA-256 canonical validated create payload after defaults.
- fingerprint persist ở `resumes.create_request_fingerprint` / `job_descriptions.create_request_fingerprint`.
- same deterministic id + same fingerprint => return existing resource;
- same deterministic id + different fingerprint => `409 IDEMPOTENCY_KEY_REUSED`;
- concurrent deterministic-PK loser phải re-read persisted row rồi compare fingerprint.

Job canonicalization v1 được khóa trong PTTK/API Contract: Unicode NFC; trim title/job_level/location; empty location->null; raw_content normalize CRLF/CR->LF; weights fixed 3 decimals; UTF-8 JSON fixed key order/no extra whitespace.

### R5. Resume storage orphan có thể nhận nhầm file — FIXED
Canonical storage key: `resumes/{resume_id}/source`, put-if-absent/no-overwrite.

Nếu object đã tồn tại mà DB row chưa tồn tại:
- compute SHA-256 bytes object;
- bằng request fingerprint => reuse object + retry DB insert;
- khác => 409 `IDEMPOTENCY_KEY_REUSED`, tuyệt đối không overwrite.

Nếu storage write thành công nhưng DB insert fail, object được phép tồn tại như orphan tạm; retry cùng logical upload dùng fingerprint reconciliation. Không delete mù trong error path vì có thể có request concurrent hợp lệ.

### R6. Nghĩa revision chưa thống nhất — FIXED
`revision` là **version của input/computation request**:
- initial resource revision=1;
- input/reparse mới => service tăng revision trước enqueue;
- direct scoring-input mutation tăng revision trong transaction;
- worker của chính expected revision không tăng revision khi terminal commit;
- recovery/re-dispatch cùng computation giữ nguyên revision.

### R7. Soft-delete race sau worker claim — FIXED
- parse claim + terminal CAS đều yêu cầu `is_deleted=false`;
- Match claim + terminal CAS yêu cầu cả linked Resume/JD chưa xóa;
- terminal multi-table parse transaction lock/check delete-state trước persist;
- Parse Recovery chỉ scan not-deleted PENDING;
- current Match/Gap/Leaderboard filter linked Resume/JD đã soft-delete.

## Artefact đã đồng bộ
- `PTTK_MASTER.md`.
- Functional/Business Rules.
- Use Case specifications.
- CV/JD/Matching Activity.
- CV/JD/Matching Sequence.
- `schema.sql`, Data Dictionary, PDM.
- System/Component Architecture.
- Matching algorithm.
- API Contract + OpenAPI contract surface.
- Traceability Matrix.
- Implementation Gate.
- GitHub Actions semantic validator.

CDM/LDM/ERD không cần thêm entity/relationship vì fingerprint là physical reliability metadata nằm trên hai resource hiện có; số bảng vẫn đúng 10.

## Static validation gate
Workflow cuối phải PASS đồng thời:
1. OpenAPI syntax + route boundary.
2. Required Idempotency-Key trên 2 create endpoints.
3. 409 `IdempotencyConflict` cho fingerprint mismatch.
4. CandidateProfile nullable, revision/generation/provenance.
5. PlantUML syntax toàn bộ.
6. 10 bảng/schema/seed/HNSW/error-state invariants.
7. đúng 2 persisted `create_request_fingerprint` + SHA-256 format checks.
8. Exclusive CAS phrases/state guards trong CV/JD/Matching Sequence.
9. Delete-aware claim/terminal guards.
10. Parse Recovery + stable UUIDv5 namespace + fingerprint/orphan reconciliation.
11. Traceability FR-43..48.

## Runtime checks còn lại sau merge
PTTK tĩnh chỉ chuyển `IMPLEMENTATION_READY` sau:
1. PR merge.
2. Reset DB.
3. Run schema + taxonomy seed.
4. Duplicate parse task smoke test: đúng 1 worker claim.
5. Duplicate Match task smoke test: đúng 1 worker claim; không race terminal.
6. Soft-delete race: delete sau claim, terminal write phải fail/rollback.
7. Dispatcher failure smoke test: resource PENDING và recovery re-dispatch.
8. Same key + same Resume file/JD canonical payload => cùng resource id, không duplicate.
9. Same key + different fingerprint => 409 `IDEMPOTENCY_KEY_REUSED`.
10. Concurrent deterministic create => PK loser re-read + compare fingerprint.
11. Resume orphan storage same fingerprint => reuse/retry DB; different fingerprint => 409/no overwrite.
12. Restart/deploy cùng code constant => same actor/route/key vẫn derive cùng UUIDv5 id.
13. Stale old revision/generation không overwrite version mới.

## Branch audit
Canonical branch là `pttk-sync-v2`. `pttk-sync-v2-r3-work` và `pttk-sync-v2-safety-copy` chỉ là ancestor/checkpoint, không chứa commit độc lập cần merge ngược vào canonical branch.

## Kết luận
Các blocker static đã biết hiện được khóa ở mức thiết kế: stale-version guard, exclusive state CAS, duplicate-delivery safety, delete-aware terminal writes, fingerprinted idempotent create, deterministic storage reconciliation, post-commit parse recovery và canonical revision semantics. Phần còn lại cần chứng minh bằng runtime integration/concurrency tests sau khi merge và dựng database thật.
