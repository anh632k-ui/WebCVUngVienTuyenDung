# BUSINESS RULES

## Auth / Account
- **BR-AUTH-01** Email normalize lowercase; DB unique `LOWER(email)`.
- **BR-AUTH-02** Self-register chỉ `CANDIDATE|HR`; ADMIN do quản trị.
- **BR-AUTH-03** `is_active=false` không đăng nhập/gọi API protected.
- **BR-AUTH-04** Chỉ lưu password hash.
- **BR-AUTH-05** Admin không self-lock/self-demote.
- **BR-AUTH-06** MVP dùng Access Token JWT. Refresh Rotation/HttpOnly session là Advanced, không nằm OpenAPI MVP.
- **BR-AUTH-07** Đổi `CANDIDATE <-> HR` chỉ khi user không có Resume/JD chưa soft-delete; nếu có trả `409 ROLE_CHANGE_CONFLICT`.

## CV
- **BR-CV-01** PDF/DOCX, tối đa 5 MB; kiểm file thực tế.
- **BR-CV-02** Parse state `PENDING|PROCESSING|PARSED|FAILED`.
- **BR-CV-03** Matching chỉ dùng Resume PARSED, chưa xóa.
- **BR-CV-04** Candidate profile thuộc một resume cụ thể; cardinality 0..1.
- **BR-CV-05** Owner được Human-in-the-loop; mutation ảnh hưởng scoring phải regenerate embedding.
- **BR-CV-06** Resume có `revision>=1`; revision là version của canonical input/computation request. Tăng revision trước computation mới hoặc trong direct mutation làm scoring input đổi. Worker của chính `expected_revision` không tăng revision lần nữa khi commit.
- **BR-CV-07** Parse/reparse task mang `expected_revision`. Claim độc quyền chỉ khi `revision=expected_revision AND parsing_status='PENDING'`; terminal SUCCESS/FAILED chỉ từ `PROCESSING` của cùng revision. `rowcount=0` => stale/duplicate task discard.
- **BR-CV-08** Soft delete mặc định; query nghiệp vụ bỏ `is_deleted=true`.
- **BR-CV-09** Candidate/HR chỉ thấy CV kho mình.
- **BR-CV-10** Khi revision CV đổi, mọi Match liên quan phải invalidate và tăng generation.
- **BR-CV-11** `POST /resumes/upload` bắt buộc `Idempotency-Key` UUID. Resource id được derive deterministic từ actor+route+key; retry cùng key không tạo Resume thứ hai.
- **BR-CV-12** Sau DB commit mới dispatch parse. Nếu dispatcher lỗi, Resume vẫn PENDING và recovery sweeper re-dispatch `(resume_id,current_revision)` mà không tăng revision. Duplicate delivery an toàn nhờ BR-CV-07.

## Skill Taxonomy
- **BR-SKILL-01** `normalized_name` unique; `skill_kind=HARD|SOFT`.
- **BR-SKILL-02** Không tự insert skill lạ trong pipeline production.
- **BR-SKILL-03** Môi trường mới phải chạy `skill_taxonomy_seed.sql` sau schema.
- **BR-SKILL-04** Unique `(resume_id,skill_id)` và `(job_id,skill_id)`.

## JD
- **BR-JOB-01** Business state `DRAFT|ACTIVE|CLOSED`; parse state tách riêng.
- **BR-JOB-02** Chỉ HR tạo JD mới; `recruiter_id=current_user.id`. Admin không tạo JD mới dưới identity Admin.
- **BR-JOB-03** Candidate chỉ xem/match JD ACTIVE chưa xóa.
- **BR-JOB-04** HR chỉ sửa JD mình; Admin override quản trị.
- **BR-JOB-05** `importance=MANDATORY|OPTIONAL`.
- **BR-JOB-06** Raw content đổi: tăng `revision` trước reparse, status DRAFT, parse PENDING, verified=false, clear embedding/model/preprocessing/parsed_at, invalidate Match rồi enqueue parse với `expected_revision=new_revision`. Worker commit kết quả không tăng revision thêm.
- **BR-JOB-07** Weights mỗi số [0,1], tổng 1.
- **BR-JOB-08** ACTIVE/matching cần PARSED + verified + embedding/model/preprocessing hợp lệ + >=1 job_skill.
- **BR-JOB-09** Criteria/weights/direct re-embedding mutation làm scoring input đổi phải tăng JD revision và invalidate Match.
- **BR-JOB-10** Status transitions qua API: DRAFT->ACTIVE; ACTIVE->DRAFT|CLOSED; CLOSED->DRAFT|ACTIVE; same-state idempotent. DRAFT->CLOSED invalid (`422`).
- **BR-JOB-11** Parse task mang `expected_revision`. Claim độc quyền chỉ khi `revision=expected_revision AND parsing_status='PENDING'`; terminal SUCCESS/FAILED chỉ từ `PROCESSING` của cùng revision. `rowcount=0` => stale/duplicate task discard.
- **BR-JOB-12** `POST /jobs` bắt buộc `Idempotency-Key` UUID; server derive deterministic JD id từ actor+route+key để retry không tạo JD thứ hai.
- **BR-JOB-13** Sau DB commit mới dispatch parse. Dispatcher lỗi không rollback resource; JD giữ PENDING và recovery sweeper re-dispatch current revision, không tăng revision.

## Matching
- **BR-MATCH-01** Precondition: Resume/JD PARSED, cùng embedding model + preprocessing version, criteria verified, >=1 job_skill, quyền hợp lệ.
- **BR-MATCH-02** State `PENDING|PROCESSING|COMPLETED|FAILED`.
- **BR-MATCH-03** COMPLETED phải có 4 scores 0..100, provenance embedding và calculated_at.
- **BR-MATCH-04** Một current row cho `(job_id,resume_id)`, không attempt history.
- **BR-MATCH-05** Skill: MANDATORY weight 2, OPTIONAL 1.
- **BR-MATCH-06** Semantic = cosine cùng model/preprocessing; BM25 không blend vào hybrid-v1.
- **BR-MATCH-07** Experience: merge interval có start_date; current dùng ngày hiện tại; interval thiếu start hoặc non-current thiếu end không được đoán/không tính. Nếu required>0 mà không có interval định lượng được -> 0.
- **BR-MATCH-08** Overall = weighted Skill/Semantic/Experience.
- **BR-MATCH-09** Candidate đọc Match CV mình; HR chỉ đọc khi cả JD+CV thuộc HR; Admin override.
- **BR-MATCH-10** Leaderboard HR chỉ CV kho HR.
- **BR-MATCH-11** Invalidate trong cùng transaction: tăng generation, refresh `resume_revision/job_revision` snapshot theo resource hiện tại, PENDING, clear scores/evidence/error/embedding provenance/calculated_at.
- **BR-MATCH-12** Trigger success trả PENDING; QUEUED không phải domain status.
- **BR-MATCH-13** Match worker claim độc quyền `PENDING -> PROCESSING` chỉ khi generation + snapshot revisions + linked resource revisions đều khớp expected. Terminal COMPLETED/FAILED chỉ từ `PROCESSING` với cùng expected values. Duplicate/stale task `rowcount=0` phải discard; không được race FAILED/COMPLETED.
- **BR-MATCH-14** Batch validate all-or-nothing trước mutation; upsert rows trong một DB transaction.
- **BR-MATCH-15** Dispatch xảy ra sau DB commit. Dispatcher failure -> `503 TASK_DISPATCH_FAILED`; prepared PENDING rows retry-safe nhờ generation.
- **BR-MATCH-16** Error-state: FAILED bắt buộc có `error_message`; PENDING/PROCESSING/COMPLETED bắt buộc `error_message=NULL`.

## AI/NLP
- **BR-AI-01** Human-in-the-loop; không bịa dữ liệu khi thiếu bằng chứng.
- **BR-AI-02** LLM/XAI chỉ explanation/recommendation.
- **BR-AI-03** Resume/JD PARSED phải có cùng baseline dimension; lưu cả `embedding_model` và `embedding_preprocessing_version`.
- **BR-AI-04** Thay model/preprocessing tạo computation/input version mới: service tăng resource revision trước enqueue/re-embedding; worker đúng expected revision không tăng revision khi terminal commit.

## Reliability / Recovery
- **BR-REL-01** `POST /resumes/upload` và `POST /jobs` dùng `Idempotency-Key` để network/client retry không tạo duplicate resource.
- **BR-REL-02** Parse dispatcher failure sau commit không biến persistence thành failure; resource giữ PENDING và được internal recovery re-dispatch.
- **BR-REL-03** Recovery sweeper chỉ re-dispatch resource `PENDING` quá grace window, không mutate revision/state trước enqueue.
- **BR-REL-04** Không tự reset stale `PROCESSING` về PENDING trong MVP; worker-crash lease recovery cần attempt/lease token riêng để tránh resurrect worker race.

## Data
- **BR-DATA-01** TIMESTAMPTZ.
- **BR-DATA-02** Soft-delete không hard cascade ngay.
- **BR-DATA-03** Multi-table mutation + revision/invalidation nằm trong transaction.
