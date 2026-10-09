# YÊU CẦU CHỨC NĂNG

## A. Account
- **FR-01** Guest đăng ký Candidate/HR.
- **FR-02** Login nhận JWT Access Token.
- **FR-03 [Advanced]** Refresh Token Rotation/HttpOnly session + server-side logout khi module được bật; không thuộc OpenAPI MVP.
- **FR-04** Xem/cập nhật `/users/me`.
- **FR-05** Đổi mật khẩu.
- **FR-06** Admin list/lock/unlock/change role; role change bị 409 nếu user còn resource nghiệp vụ chưa xóa.

## B. CV/NLP
- **FR-07** Upload PDF/DOCX <=5MB.
- **FR-08** Lưu file an toàn + Resume PENDING revision=1.
- **FR-09** Extract text/entities/skills/experience/education.
- **FR-10** Normalize taxonomy + embedding lưu model/preprocessing version.
- **FR-11** Xem detail; candidate_profile có thể null trước PARSED.
- **FR-12** Human-in-the-loop: tăng resume revision trong direct mutation, regenerate embedding, invalidate Match.
- **FR-13** Quản lý kho CV theo ownership.
- **FR-14** Xem parse status.
- **FR-15** Parse/reparse worker dùng `expected_revision`; claim độc quyền chỉ từ PENDING và terminal write chỉ từ PROCESSING của cùng revision; cả claim/terminal yêu cầu Resume chưa soft-delete. Worker commit kết quả không tăng revision thêm.

## C. JD/NLP
- **FR-16** Chỉ HR tạo JD mới; recruiter_id=current HR.
- **FR-17** HR/Admin quản lý JD theo quyền.
- **FR-18** Candidate xem JD ACTIVE.
- **FR-19** Parse JD: experience/education/skill + MANDATORY/OPTIONAL.
- **FR-20** Sinh embedding model + preprocessing version.
- **FR-21** Review criteria; direct mutation tăng revision trước input version mới + invalidate Match.
- **FR-22** HR owner/Admin update đủ ba weights cho Job non-deleted, current `PARSED`; mỗi accepted PUT tăng revision + invalidate toàn bộ Match atomically. `recalculate=true` best-effort publish immutable tasks sau commit; persistence thành công luôn trả 200.
- **FR-23** Enforce status transition matrix và readiness trước ACTIVE.
- **FR-24** JD parse worker dùng `expected_revision`; claim độc quyền PENDING->PROCESSING, terminal chỉ từ PROCESSING cùng revision; cả claim/terminal yêu cầu JD chưa soft-delete; worker terminal commit không tăng revision.

## D. Matching
- **FR-25** Candidate self-match private.
- **FR-26** HR batch match JD mình + CV kho mình.
- **FR-27** Skill Score.
- **FR-28** Semantic Score cosine; BM25 diagnostic v1.
- **FR-29** Experience Score theo interval định lượng, không đoán missing dates.
- **FR-30** Overall Score.
- **FR-31** Lưu matched/missing/gap evidence.
- **FR-32** Match visibility strict ownership.
- **FR-33** Leaderboard HR chỉ CV kho HR.
- **FR-34** Invalidation tăng generation + clear stale payload + refresh revision snapshots.
- **FR-35** Match worker claim độc quyền PENDING->PROCESSING bằng expected generation + snapshot revisions + linked resource revisions; terminal COMPLETED/FAILED chỉ từ PROCESSING với cùng expected values; linked Resume/JD phải vẫn chưa soft-delete.
- **FR-36** Batch validation all-or-nothing + atomic prepare transaction; dispatch failure trả 503 và retry-safe.
- **FR-37** Danh sách current matches theo ownership và chỉ resource chưa soft-delete.

## E. Supporting / Advanced
- **FR-38** `GET /skills` taxonomy lookup.
- **FR-39 [Advanced]** Export PDF/Excel.
- **FR-40 [Advanced]** LLM/XAI explanation.
- **FR-41** Benchmark MAE/Pearson/NDCG@K/Precision@K khi có ground truth.
- **FR-42** Environment bootstrap seed Skill Taxonomy trước khi chạy NLP.

## F. Reliability
- **FR-43** `POST /resumes/upload` và `POST /jobs` bắt buộc `Idempotency-Key` UUID. Resource id derive bằng UUIDv5 từ application namespace cố định `bd7b1f30-b2de-549c-a8dd-8d742ee5bc12`, actor id, canonical route và key.
- **FR-44** DB/resource persistence commit trước parse dispatch; dispatcher lỗi sau commit để resource PENDING và không rollback resource đã tạo.
- **FR-45** Parse Recovery Sweeper startup/periodic re-dispatch Resume/JD PENDING quá grace window bằng current revision; không tăng revision và không tự reset PROCESSING trong MVP.
- **FR-46** Concurrent soft-delete phải làm parse/match worker claim hoặc terminal CAS thất bại; không worker nào được ghi output mới vào Resume/JD đã soft-delete, và current matching/leaderboard phải loại resource đã xóa.
- **FR-47** Mỗi Resume/JD create persist `create_request_fingerprint` SHA-256. Resume hash raw file bytes; Job hash canonical validated create payload sau defaults. Retry cùng key + cùng fingerprint trả cùng resource; cùng key + fingerprint khác trả `409 IDEMPOTENCY_KEY_REUSED`.
- **FR-48** Resume storage dùng deterministic no-overwrite key theo resume id. Nếu storage object tồn tại nhưng DB row chưa có, service phải hash object để reconcile: fingerprint giống thì reuse/retry insert, fingerprint khác thì 409; concurrent deterministic-PK insert loser phải re-read row và compare fingerprint.
