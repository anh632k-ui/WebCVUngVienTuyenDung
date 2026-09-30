# PHÂN TÍCH & THIẾT KẾ HỆ THỐNG — WebCVUngVienTuyenDung

Bộ PTTK rebuild từ đề cương `dc.docx`, mục tiêu thật của đề tài AI/NLP và các vòng semantic/reliability review trước khi code.

## Thứ tự đọc
1. `PTTK_MASTER.md` — quyết định canonical.
2. `00_requirements/` — scope, actor/ownership, FR/NFR, business rules, state.
3. `01_bfd/`.
4. `02_usecase/`.
5. `03_activity/`.
6. `04_sequence/`.
7. `05_database/` — CDM/LDM/PDM/ERD/Data Dictionary/`schema.sql`/`skill_taxonomy_seed.sql`.
8. `06_architecture/` — system/component/AI pipeline/matching.
9. `07_api/` — API Contract + OpenAPI.
10. `08_traceability/`.
11. `09_delivery/` — audit/reset/gate.

## Quy tắc implementation
- PTTK quyết định database; DB thực nghiệm cũ không làm nguồn chuẩn.
- Chạy `schema.sql` rồi **bắt buộc** chạy `skill_taxonomy_seed.sql` khi bootstrap môi trường mới.
- Không dùng ORM `create_all()` để tự phát minh schema.
- Không code endpoint/table/enum/concurrency/idempotency semantic ngoài PTTK nếu chưa sửa traceability.
- Advanced hiện gồm Refresh Token Rotation/HttpOnly session, OCR fallback, LLM/XAI và Export; không expose route Advanced trong OpenAPI MVP khi chưa bật.
- Candidate self-match private; HR chỉ xem Match/Leaderboard khi cả JD và CV thuộc HR.
- CandidateProfile là 0..1/Resume.
- CV/JD embedding phải cùng model + preprocessing version.
- `revision` là input/computation version: tăng trước computation mới; worker của đúng expected revision không tăng khi commit output.
- CV/JD parse worker phải exclusive CAS: claim chỉ `PENDING -> PROCESSING`, terminal chỉ từ `PROCESSING` cùng expected revision; cả hai yêu cầu `is_deleted=false`.
- Match worker phải exclusive CAS: claim chỉ từ PENDING với expected generation/revisions + linked not-deleted; COMPLETED/FAILED chỉ từ PROCESSING cùng expected values + linked not-deleted.
- Concurrent soft-delete phải làm async terminal write fail/rollback; current Match/Gap/Leaderboard không trả linked resource đã xóa.
- `POST /resumes/upload` và `POST /jobs` bắt buộc `Idempotency-Key` UUID và dùng UUIDv5 namespace cố định `bd7b1f30-b2de-549c-a8dd-8d742ee5bc12`.
- Resume/JD persist `create_request_fingerprint` SHA-256. Resume hash raw file bytes; Job hash canonical validated create payload sau defaults.
- Retry cùng key + cùng fingerprint trả cùng resource; cùng key + fingerprint khác trả `409 IDEMPOTENCY_KEY_REUSED`.
- CV storage key create là deterministic `resumes/{resume_id}/source`; dùng put-if-absent/no-overwrite. Storage orphan chỉ reuse sau khi SHA-256 object khớp request fingerprint; mismatch không overwrite.
- Concurrent deterministic create thua PK race phải re-read row và compare fingerprint trước khi trả existing/409.
- Resume/JD persistence commit trước parse dispatch. Dispatcher lỗi để resource PENDING và Parse Recovery Sweeper re-dispatch current revision nếu resource chưa xóa.
- Recovery không tăng revision và không blind-reset PROCESSING trong MVP nếu chưa có lease/attempt token.
- Batch matching validate toàn bộ trước mutation; atomic prepare; dispatch sau commit; dispatch failure retry-safe.
- FAILED phải có error; non-FAILED không giữ stale error.
- `GET /matching` là current results, không phải attempt history.

## Branch canonical
- `pttk-sync-v2`: nhánh canonical và là head của PR #1.
- `pttk-sync-v2-r3-work`: checkpoint/ancestor sau review vòng 3.
- `pttk-sync-v2-safety-copy`: checkpoint/ancestor cũ hơn.
Hai branch phụ không chứa commit độc lập cần merge ngược vào canonical branch.

## Implementation Ready gate
Chỉ chuyển `IMPLEMENTATION_READY` sau:
1. GitHub Actions ở HEAD cuối PASS;
2. PR review/merge;
3. reset `webcv_ungvien`;
4. chạy schema + taxonomy seed;
5. smoke-test constraints/indexes/seed;
6. integration/concurrency test duplicate delivery, stale revision/generation, soft-delete race, parse recovery, fingerprinted idempotent create, Resume orphan reconciliation, ownership và role transition PASS.

Mục tiêu: web application vẫn hoàn chỉnh nếu các module Advanced chưa triển khai, trong khi lõi CV/JD/NLP/embedding/matching/Skill Gap bám đúng tên đề tài.
