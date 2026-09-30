# PTTK MASTER — WEBCVUNGVIENTUYENDUNG

**Status:** `DESIGN_LOCKED_FOR_REVIEW` — chỉ code sau khi PR được duyệt, schema canonical chạy thật và Skill Taxonomy đã seed.

## 1. Căn cứ phạm vi
Nguồn yêu cầu là đề cương `dc.docx`: Web Application, FastAPI async, Next.js/Tailwind, PostgreSQL + pgvector, xử lý PDF/DOCX, NLP/Information Extraction, Sentence Transformers, keyword/BM25, semantic similarity, Matching Score, Skill Gap và đánh giá thực nghiệm khi có ground truth.

MVP ưu tiên hệ thống chạy chắc. Refresh Token Rotation, OCR fallback, LLM/XAI và Export PDF/Excel là **Advanced**; không được làm điều kiện sống còn của MVP.

## 2. Actor và ownership
- Guest, Candidate, HR, Admin.
- `resumes.owner_user_id`: chủ kho CV.
- `job_descriptions.recruiter_id`: HR sở hữu JD.
- Candidate chỉ dùng CV mình; HR chỉ dùng CV trong kho mình và JD mình; Admin có quyền giám sát.
- Candidate self-match với JD của HR **không** cấp quyền cho HR xem CV/kết quả.
- MVP chưa có `applications`; muốn mở chia sẻ CV phải sửa PTTK trước.

### Role transition
Admin chỉ đổi `CANDIDATE <-> HR` khi user **không còn resource nghiệp vụ chưa xóa** có thể đổi nghĩa ownership:
- không có Resume `is_deleted=false`;
- không có JD `is_deleted=false`.
Nếu vi phạm trả `409 ROLE_CHANGE_CONFLICT`. Admin không tự khóa/tự hạ quyền chính mình.

## 3. Database canonical
Có đúng **10 bảng**:
`users`, `skills`, `resumes`, `candidate_profiles`, `resume_skills`, `resume_experiences`, `resume_educations`, `job_descriptions`, `job_skills`, `match_results`.

`candidate_profiles` là quan hệ **0..1 cho mỗi Resume**: PENDING/FAILED có thể chưa có profile; PARSED thường có snapshot.

Các field/version quan trọng:
- Resume/JD có `revision BIGINT`.
- Resume/JD có `embedding_model` + `embedding_preprocessing_version`.
- Match có `generation`, snapshot `resume_revision`, `job_revision`, `embedding_model`, `embedding_preprocessing_version`, `algorithm_version`.
- `skills.skill_kind = HARD|SOFT`.
- `job_skills.importance = MANDATORY|OPTIONAL`.
- embedding là `vector(1024)` cho baseline bge-m3.

## 4. Bootstrap Skill Taxonomy
Sau `schema.sql` phải chạy `05_database/skill_taxonomy_seed.sql`.
Pipeline **không tự INSERT skill lạ** vào taxonomy. Seed là dữ liệu khởi tạo bắt buộc của môi trường demo, không phải bảng thứ 11.

## 5. State canonical
- Resume parse: `PENDING|PROCESSING|PARSED|FAILED`.
- Job parse: `PENDING|PROCESSING|PARSED|FAILED`.
- Job business: `DRAFT|ACTIVE|CLOSED`.
- Match: `PENDING|PROCESSING|COMPLETED|FAILED`.

Job transition qua API:
- DRAFT -> ACTIVE khi ready.
- ACTIVE -> DRAFT hoặc CLOSED.
- CLOSED -> DRAFT hoặc ACTIVE khi ready.
- same-state idempotent.
- DRAFT -> CLOSED bị từ chối `422`.

JD ACTIVE/matching cần PARSED + verified + embedding hợp lệ + >=1 `job_skill`.

## 6. Auth MVP
MVP chỉ bắt buộc Access Token JWT.
`/auth/refresh` và server-side refresh-session logout thuộc **Advanced** và không nằm trong OpenAPI MVP. UI MVP logout bằng cách bỏ Access Token phía client. Khi bật Advanced, refresh token phải do backend `Set-Cookie; HttpOnly`.

## 7. Matching `hybrid-v1`
`Overall = w_skill*Skill + w_semantic*Semantic + w_experience*Experience`, mặc định `0.50/0.30/0.20`.

- Skill: weighted coverage MANDATORY=2, OPTIONAL=1.
- Semantic: `100 * clamp(cosine,0,1)`; CV/JD phải cùng model + preprocessing version.
- Experience: merge các interval có ngày xác định; current job dùng ngày hiện tại. Experience thiếu `start_date`, hoặc thiếu `end_date` khi `is_current=false`, không được đoán và không cộng vào total. Nếu JD yêu cầu >0 nhưng không có interval định lượng được thì ExperienceScore=0.
- BM25 chỉ diagnostic/retrieval/experiment ở v1.
- LLM/XAI chỉ explanation/recommendation.

## 8. Concurrency / stale-worker guard
Đây là invariant bắt buộc.

### Resource revision
Mỗi thay đổi làm input scoring thay đổi phải tăng revision:
- CV parsed/manual-edit/re-embedding -> `resumes.revision += 1`.
- JD raw content/criteria/weights/re-embedding -> `job_descriptions.revision += 1`.

Parse task nhận `expected_revision`. Worker chỉ commit kết quả nếu resource vẫn ở revision đó; task cũ bị discard.

### Match generation
Mỗi trigger/invalidate Match tăng `match_results.generation`.
Task matching nhận:
`match_id, expected_generation, expected_resume_revision, expected_job_revision, algorithm_version`.

Worker chỉ ghi PROCESSING/COMPLETED/FAILED nếu generation và revision vẫn khớp. Trước commit COMPLETED phải re-check linked Resume/JD revision. Nếu mismatch hoặc conditional UPDATE ảnh hưởng 0 row, task được coi là stale và **không được overwrite** row mới.

## 9. Canonical invalidation
Khi score stale:
- tăng resource revision tương ứng;
- tăng Match generation;
- `status=PENDING`;
- 4 scores=NULL;
- `matched_skills=[]`, `missing_skills=[]`;
- `gap_analysis_summary=NULL`;
- `error_message=NULL`;
- `embedding_model=NULL`, `embedding_preprocessing_version=NULL`;
- `calculated_at=NULL`;
- `updated_at=NOW()`.

`recalculate=true` chỉ quyết định dispatch ngay.

## 10. Batch matching
- Validate **toàn bộ** `resume_ids` trước khi mutate.
- Nếu một item fail ownership/readiness -> cả request fail, không sửa Match nào.
- Chuẩn bị/upsert toàn batch trong **một transaction**.
- Sau commit mới dispatch task.
- Nếu dispatcher lỗi, endpoint trả `503 TASK_DISPATCH_FAILED`; rows đã chuẩn bị có thể còn PENDING và request có thể retry an toàn. Lần retry tăng generation nên task cũ không overwrite task mới.

## 11. Nguồn chuẩn khi code
1. `PTTK_MASTER.md` + requirements/business rules.
2. `05_database/schema.sql` + `skill_taxonomy_seed.sql`.
3. `07_api/api_contract.md` + `openapi.yaml`.
4. Use Case / Activity / Sequence / Architecture.
5. Code.

Muốn đổi API/database/enum/concurrency rule phải sửa PTTK trước.

## 12. Gate trước code
- CI OpenAPI/PlantUML/schema/design PASS.
- PR review/merge.
- reset DB cũ.
- chạy `schema.sql`.
- chạy `skill_taxonomy_seed.sql`.
- verify 10 tables, constraints, HNSW, taxonomy seed.
- smoke-test revision/generation/error-state constraints.
- sau đó mới ORM/Auth/API/AI.
