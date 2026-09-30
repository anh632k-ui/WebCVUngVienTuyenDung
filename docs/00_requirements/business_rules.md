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
- **BR-CV-06** Resume có `revision>=1`. Mọi thay đổi canonical parsed data/model/preprocessing làm input scoring đổi phải tăng revision.
- **BR-CV-07** Parse/reparse task mang `expected_revision`; worker chỉ commit nếu Resume revision vẫn khớp.
- **BR-CV-08** Soft delete mặc định; query nghiệp vụ bỏ `is_deleted=true`.
- **BR-CV-09** Candidate/HR chỉ thấy CV kho mình.
- **BR-CV-10** Khi revision CV đổi, mọi Match liên quan phải invalidate và tăng generation.

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
- **BR-JOB-06** Raw content đổi: tăng `revision`, status DRAFT, parse PENDING, verified=false, clear embedding/model/preprocessing/parsed_at, invalidate Match rồi enqueue parse với expected_revision.
- **BR-JOB-07** Weights mỗi số [0,1], tổng 1.
- **BR-JOB-08** ACTIVE/matching cần PARSED + verified + embedding/model/preprocessing hợp lệ + >=1 job_skill.
- **BR-JOB-09** Criteria/weights/re-embedding đổi phải tăng JD revision và invalidate Match.
- **BR-JOB-10** Status transitions qua API: DRAFT->ACTIVE; ACTIVE->DRAFT|CLOSED; CLOSED->DRAFT|ACTIVE; same-state idempotent. DRAFT->CLOSED invalid (`422`).
- **BR-JOB-11** Parse task mang expected_revision; stale worker không được commit.

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
- **BR-MATCH-11** Invalidate: tăng generation, PENDING, clear scores/evidence/error/provenance/calculated_at.
- **BR-MATCH-12** Trigger success trả PENDING; QUEUED không phải domain status.
- **BR-MATCH-13** Match có `generation>=1`, snapshot `resume_revision`, `job_revision`. Worker phải compare generation+revisions trước PROCESSING và trước terminal write; stale task discard.
- **BR-MATCH-14** Batch validate all-or-nothing trước mutation; upsert rows trong một DB transaction.
- **BR-MATCH-15** Dispatch xảy ra sau DB commit. Dispatcher failure -> `503 TASK_DISPATCH_FAILED`; prepared PENDING rows retry-safe nhờ generation.
- **BR-MATCH-16** Error-state: FAILED bắt buộc có `error_message`; PENDING/PROCESSING/COMPLETED bắt buộc `error_message=NULL`.

## AI/NLP
- **BR-AI-01** Human-in-the-loop; không bịa dữ liệu khi thiếu bằng chứng.
- **BR-AI-02** LLM/XAI chỉ explanation/recommendation.
- **BR-AI-03** Resume/JD PARSED phải có cùng baseline dimension; lưu cả `embedding_model` và `embedding_preprocessing_version`.
- **BR-AI-04** Thay model/preprocessing phải tăng resource revision và invalidate Match.

## Data
- **BR-DATA-01** TIMESTAMPTZ.
- **BR-DATA-02** Soft-delete không hard cascade ngay.
- **BR-DATA-03** Multi-table mutation + revision/invalidation nằm trong transaction.
