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

## 8. Revision, generation và exclusive CAS
Đây là invariant bắt buộc.

### 8.1 Nghĩa canonical của resource revision
`revision` là **phiên bản input/computation request** của Resume/JD.

Quy tắc:
- resource mới bắt đầu `revision=1`; parse ban đầu dùng `expected_revision=1`;
- trước khi yêu cầu một computation/reparse mới do input thay đổi, service tăng revision **trước khi enqueue**;
- manual mutation trực tiếp làm scoring input đổi cũng tăng revision trong transaction mutation;
- worker xử lý đúng `expected_revision` **không tăng revision thêm lần nữa khi commit kết quả**;
- retry/re-dispatch cùng computation giữ nguyên revision.

Ví dụ JD raw content đổi revision 5 -> 6, service commit input mới ở revision 6 rồi enqueue parser `expected_revision=6`; parser thành công vẫn để JD ở revision 6.

### 8.2 Parse worker exclusive CAS
Duplicate queue delivery phải an toàn. Chỉ một worker được claim một revision:

```text
CLAIM:
UPDATE Resume/JD ... SET parsing_status='PROCESSING', updated_at=NOW()
WHERE id=? AND revision=expected_revision AND parsing_status='PENDING'
```

Chỉ worker có `rowcount=1` được tiếp tục. Worker khác `rowcount=0` phải discard.

Terminal write phải bắt buộc từ đúng PROCESSING của cùng revision:

```text
TERMINAL SUCCESS/FAILED:
WHERE id=? AND revision=expected_revision AND parsing_status='PROCESSING'
```

Với success nhiều bảng, worker mở transaction, lock/check resource vẫn `PROCESSING + expected_revision`, ghi aggregate rồi terminal update. Nếu check/CAS fail thì rollback toàn bộ và discard. Nhờ vậy duplicate delivery không thể race FAILED/COMPLETED.

### 8.3 Match worker exclusive CAS
Mỗi trigger/invalidate Match tăng `match_results.generation` và refresh snapshot `resume_revision`, `job_revision` về revision hiện tại.

Task nhận:
`match_id, expected_generation, expected_resume_revision, expected_job_revision, algorithm_version`.

Claim chỉ hợp lệ khi row còn `PENDING`, generation/snapshots khớp và linked Resume/JD revisions vẫn khớp expected. Chỉ một worker được chuyển `PENDING -> PROCESSING`.

Terminal COMPLETED/FAILED chỉ hợp lệ từ `PROCESSING` với cùng generation/snapshots và linked revisions vẫn khớp. `rowcount=0` => stale/duplicate task discard, không overwrite generation mới.

## 9. Idempotent creation và parse dispatch recovery
### 9.1 Idempotent POST create
`POST /resumes/upload` và `POST /jobs` bắt buộc header `Idempotency-Key` dạng UUID cho mỗi logical create.

Server tạo resource UUID deterministic bằng UUIDv5 từ `(application namespace, actor_id, route, Idempotency-Key)`.
- retry cùng actor+route+key -> cùng resource id, không tạo duplicate;
- key chỉ được tái sử dụng cho cùng logical request;
- client tạo key mới khi thực sự muốn tạo resource mới.

### 9.2 Dispatch sau commit
DB/resource creation commit trước, dispatch parse sau. Nếu immediate dispatcher lỗi:
- resource vẫn ở `PENDING`;
- server log lỗi dispatch;
- endpoint vẫn trả resource đã tạo (`202` Resume, `201` Job) vì persistence đã thành công;
- client **không cần POST tạo lại chỉ để cứu queue**.

Nếu response mạng bị mất và client retry POST, cùng `Idempotency-Key` trả cùng resource, không duplicate.

### 9.3 Parse Recovery Sweeper
MVP có recovery/re-dispatch nội bộ cho Resume/JD `PENDING` chưa được claim:
- periodic/startup sweep chọn resource `PENDING`, chưa xóa, `updated_at` cũ hơn grace window cấu hình;
- enqueue lại `(resource_id, current_revision)` mà **không tăng revision** và không đổi payload;
- duplicate re-dispatch an toàn vì exclusive CAS chỉ cho một worker claim `PENDING -> PROCESSING`.

Recovery này xử lý failure **trước khi worker claim** (dispatcher/broker/transient delivery). Recovery stuck `PROCESSING` sau worker crash cần lease/attempt token riêng và thuộc hardening sau MVP; không được reset mù PROCESSING về PENDING vì có thể gây race worker cũ.

## 10. Canonical invalidation
Khi score stale, trong cùng transaction:
- tăng resource revision tương ứng **trước computation mới** nếu mutation đó thay input resource;
- tăng Match generation;
- refresh `match_results.resume_revision = resumes.revision` và `match_results.job_revision = job_descriptions.revision`;
- `status=PENDING`;
- 4 scores=NULL;
- `matched_skills=[]`, `missing_skills=[]`;
- `gap_analysis_summary=NULL`;
- `error_message=NULL`;
- `embedding_model=NULL`, `embedding_preprocessing_version=NULL`;
- `calculated_at=NULL`;
- `updated_at=NOW()`.

## 11. Batch matching
- Validate **toàn bộ** `resume_ids` trước khi mutate.
- Nếu một item fail ownership/readiness -> cả request fail, không sửa Match nào.
- Chuẩn bị/upsert toàn batch trong **một transaction**; mỗi row generation++ và snapshot revision hiện tại.
- Sau commit mới dispatch task.
- Nếu dispatcher lỗi, endpoint trả `503 TASK_DISPATCH_FAILED`; rows đã chuẩn bị có thể còn PENDING và request có thể retry an toàn. Lần retry tăng generation nên task cũ không overwrite task mới.

## 12. Nguồn chuẩn khi code
1. `PTTK_MASTER.md` + requirements/business rules.
2. `05_database/schema.sql` + `skill_taxonomy_seed.sql`.
3. `07_api/api_contract.md` + `openapi.yaml`.
4. Use Case / Activity / Sequence / Architecture.
5. Code.

Muốn đổi API/database/enum/concurrency rule phải sửa PTTK trước.

## 13. Gate trước code
- CI OpenAPI/PlantUML/schema/design PASS.
- PR review/merge.
- reset DB cũ.
- chạy `schema.sql`.
- chạy `skill_taxonomy_seed.sql`.
- verify 10 tables, constraints, HNSW, taxonomy seed.
- smoke-test duplicate delivery CAS, stale revision/generation, parse recovery re-dispatch và idempotent create.
- sau đó mới ORM/Auth/API/AI.
