# FINAL PTTK AUDIT REPORT — POST ROUND 3 RELIABILITY LOCK

## Phạm vi
Requirements -> BFD -> Use Case -> Activity -> Sequence -> Database -> Architecture/AI -> API Contract/OpenAPI -> Traceability -> Delivery Gate.

## Baseline canonical
- 10 bảng nghiệp vụ.
- Resume owner=`owner_user_id`; JD owner=`recruiter_id`.
- Candidate self-match không lộ cho HR.
- `skill_kind=HARD|SOFT`; `importance=MANDATORY|OPTIONAL`.
- vector(1024), cosine HNSW.
- current Match unique `(job_id,resume_id)`.

## Các lỗi vòng 1-3 đã xử lý
Candidate profile thiếu; parse/business state lẫn; scores NOT NULL khi pending; route matching lệch; API/OpenAPI lệch; privacy HR/Candidate; stale payload; email CI uniqueness; advanced export exposure; stale worker revision/generation; taxonomy bootstrap; role lifecycle; preprocessing provenance; JD transitions; Admin-create ambiguity; batch atomicity; missing-date scoring; error lifecycle.

## Review sau vòng 3 — các lỗi reliability còn lại và cách khóa

### R1. Duplicate delivery có thể cùng claim một revision/generation — FIXED
Đã khóa exclusive CAS:
- CV/JD parse claim: `expected_revision + PENDING + is_deleted=false -> PROCESSING`;
- CV/JD terminal: `expected_revision + PROCESSING + is_deleted=false -> PARSED|FAILED`;
- Match claim: expected generation + stored snapshots + linked revisions + linked not-deleted + `PENDING -> PROCESSING`;
- Match terminal: same expected values + linked not-deleted + `PROCESSING -> COMPLETED|FAILED`.

`rowcount=0` luôn được coi là stale/duplicate/deleted-resource và discard. Vì chỉ một worker claim được PENDING, duplicate task không còn race FAILED/PARSED hoặc FAILED/COMPLETED.

### R2. Parse dispatcher lỗi sau DB commit có thể để resource PENDING vô hạn — FIXED
Đã tách persistence khỏi dispatch:
- Resume/JD commit trước;
- best-effort immediate dispatch;
- dispatcher lỗi không rollback resource đã persist;
- resource giữ PENDING;
- startup/periodic Parse Recovery Sweeper scan PENDING + `is_deleted=false` quá grace window và re-dispatch current revision.

Recovery không tăng revision, không đổi state trước enqueue. Duplicate re-dispatch an toàn nhờ exclusive CAS.

MVP cố ý **không** blind-reset PROCESSING timeout về PENDING khi chưa có lease/attempt token, vì cách đó có thể resurrect worker cũ và tái tạo race.

### R3. Retry create sau lost response có thể tạo Resume/JD thứ hai — FIXED
`POST /resumes/upload` và `POST /jobs` bắt buộc `Idempotency-Key` UUID.
Server derive deterministic resource UUIDv5 từ application namespace + actor + route + key. Retry cùng logical request dùng cùng resource id; không tạo duplicate row.

Riêng CV: service lookup deterministic resource trước side-effect, retry existing key không ghi đè storage; storage create dùng put-if-absent/no-overwrite.

### R4. Nghĩa revision chưa thống nhất — FIXED
`revision` được khóa là **version của input/computation request**:
- initial resource revision=1;
- input/reparse mới => service tăng revision trước enqueue;
- direct scoring-input mutation tăng revision trong transaction;
- worker của chính expected revision không tăng revision khi terminal commit;
- recovery/re-dispatch cùng computation giữ nguyên revision.

### R5. Soft-delete race sau worker claim — FIXED
Self-review cuối phát hiện: nếu Resume/JD bị soft-delete sau khi worker đã claim nhưng trước terminal write, version có thể vẫn khớp và worker cũ có thể ghi output lên resource đã xóa.

Đã khóa:
- parse claim + terminal CAS đều yêu cầu `is_deleted=false`;
- Match claim + terminal CAS yêu cầu cả linked Resume/JD chưa xóa;
- terminal multi-table parse transaction lock/check delete-state trước persist;
- Parse Recovery chỉ scan not-deleted PENDING;
- current Match/Gap/Leaderboard filter linked Resume/JD đã soft-delete.

Concurrent delete do đó làm terminal CAS fail/rollback thay vì ghi kết quả mới lên resource đã xóa.

## Artefact đã đồng bộ
- `PTTK_MASTER.md`.
- Functional/NFR/Business Rules.
- Use Case specifications.
- CV/JD/Matching Activity.
- CV/JD/Matching Sequence.
- System/Component Architecture.
- Matching algorithm.
- API Contract + OpenAPI contract surface.
- Traceability Matrix.
- Implementation Gate.
- GitHub Actions semantic validator.

## Static validation gate
Workflow cuối phải PASS đồng thời:
1. OpenAPI syntax + route boundary.
2. Required Idempotency-Key trên 2 create endpoints.
3. CandidateProfile nullable, revision/generation/provenance.
4. PlantUML syntax toàn bộ.
5. 10 bảng/schema/seed/HNSW/error-state invariants.
6. Exclusive CAS phrases/state guards trong CV/JD/Matching Sequence.
7. Delete-aware claim/terminal guards.
8. Parse Recovery + Idempotency architecture.
9. Traceability FR-43/44/45/46 reliability links.

## Runtime checks còn lại sau merge
PTTK tĩnh chỉ chuyển `IMPLEMENTATION_READY` sau:
1. PR merge.
2. Reset DB.
3. Run schema + taxonomy seed.
4. Duplicate parse task smoke test: đúng 1 worker claim.
5. Duplicate Match task smoke test: đúng 1 worker claim; không race terminal.
6. Soft-delete race: delete sau claim, terminal write phải fail/rollback.
7. Current analytics không trả resource đã xóa.
8. Dispatcher failure smoke test: resource PENDING và recovery re-dispatch.
9. Retry same Idempotency-Key: cùng resource id, không duplicate row/file.
10. Stale old revision/generation không overwrite version mới.

## Kết luận
Các blocker static đã biết hiện được khóa ở mức thiết kế: stale-version guard, exclusive state CAS, duplicate-delivery safety, delete-aware terminal writes, idempotent create, post-commit parse recovery và canonical revision semantics. Phần còn lại cần chứng minh bằng runtime integration/concurrency tests sau khi merge và dựng database thật.
