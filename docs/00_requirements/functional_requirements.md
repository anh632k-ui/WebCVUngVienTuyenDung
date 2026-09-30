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
- **FR-08** `POST /resumes/upload` dùng `Idempotency-Key` UUID; retry cùng logical request không tạo duplicate Resume. Lưu file an toàn + Resume PENDING revision=1.
- **FR-09** Extract text/entities/skills/experience/education.
- **FR-10** Normalize taxonomy + embedding lưu model/preprocessing version.
- **FR-11** Xem detail; candidate_profile có thể null trước PARSED.
- **FR-12** Human-in-the-loop: tăng resume revision trong mutation, regenerate embedding, invalidate Match.
- **FR-13** Quản lý kho CV theo ownership.
- **FR-14** Xem parse status.
- **FR-15** Parse/reparse worker dùng `expected_revision`; claim độc quyền chỉ từ PENDING và terminal write chỉ từ PROCESSING của cùng revision. Worker commit kết quả không tăng revision thêm.
- **FR-16** Dispatcher lỗi sau commit không tạo Resume thứ hai; Resume PENDING được internal recovery sweeper re-dispatch cùng current revision.

## C. JD/NLP
- **FR-17** Chỉ HR tạo JD mới; `recruiter_id=current HR`; `POST /jobs` dùng `Idempotency-Key` để retry không tạo duplicate JD.
- **FR-18** HR/Admin quản lý JD theo quyền.
- **FR-19** Candidate xem JD ACTIVE.
- **FR-20** Parse JD: experience/education/skill + MANDATORY/OPTIONAL.
- **FR-21** Sinh embedding model + preprocessing version.
- **FR-22** Review criteria; direct mutation tăng revision trước khi các match/recompute mới và invalidate Match.
- **FR-23** Update weights; mutation tăng revision + invalidate Match.
- **FR-24** Enforce status transition matrix và readiness trước ACTIVE.
- **FR-25** JD parse worker dùng `expected_revision`; claim độc quyền PENDING->PROCESSING, terminal chỉ từ PROCESSING cùng revision; terminal commit không tăng revision.
- **FR-26** Dispatcher lỗi sau commit để JD ở PENDING; recovery sweeper re-dispatch current revision mà không tạo JD mới.

## D. Matching
- **FR-27** Candidate self-match private.
- **FR-28** HR batch match JD mình + CV kho mình.
- **FR-29** Skill Score.
- **FR-30** Semantic Score cosine; BM25 diagnostic v1.
- **FR-31** Experience Score theo interval định lượng, không đoán missing dates.
- **FR-32** Overall Score.
- **FR-33** Lưu matched/missing/gap evidence.
- **FR-34** Match visibility strict ownership.
- **FR-35** Leaderboard HR chỉ CV kho HR.
- **FR-36** Invalidation tăng generation + clear stale payload + refresh revision snapshots.
- **FR-37** Match worker claim độc quyền PENDING->PROCESSING bằng expected generation + snapshot revisions + linked resource revisions; terminal COMPLETED/FAILED chỉ từ PROCESSING với cùng expected values.
- **FR-38** Batch validation all-or-nothing + atomic prepare transaction; dispatch failure trả 503 và retry-safe.
- **FR-39** Danh sách current matches theo ownership.

## E. Supporting / Advanced
- **FR-40** `GET /skills` taxonomy lookup.
- **FR-41 [Advanced]** Export PDF/Excel.
- **FR-42 [Advanced]** LLM/XAI explanation.
- **FR-43** Benchmark MAE/Pearson/NDCG@K/Precision@K khi có ground truth.
- **FR-44** Environment bootstrap seed Skill Taxonomy trước khi chạy NLP.
- **FR-45** Parse recovery sweep xử lý các Resume/JD còn PENDING quá grace window bằng re-dispatch cùng current revision; không tự reset PROCESSING trong MVP.
