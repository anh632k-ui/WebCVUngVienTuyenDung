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
- Resume/JD có `create_request_fingerprint VARCHAR(64)` để bind deterministic create id với logical request ban đầu.
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
Soft-deleted Resume/JD không được parse, match, xuất hiện trong current Match list/leaderboard hoặc nhận terminal worker write mới.

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
WHERE id=?
  AND revision=expected_revision
  AND parsing_status='PENDING'
  AND is_deleted=FALSE
```

Chỉ worker có `rowcount=1` được tiếp tục. Worker khác `rowcount=0` phải discard.

Terminal write phải bắt buộc từ đúng PROCESSING của cùng revision và resource vẫn chưa bị soft-delete:

```text
TERMINAL SUCCESS/FAILED:
WHERE id=?
  AND revision=expected_revision
  AND parsing_status='PROCESSING'
  AND is_deleted=FALSE
```

Với success nhiều bảng, worker mở transaction, lock/check resource vẫn `PROCESSING + expected_revision + is_deleted=false`, ghi aggregate rồi terminal update. Nếu check/CAS fail thì rollback toàn bộ và discard.

### 8.3 Match worker exclusive CAS
Mỗi trigger/invalidate Match tăng `match_results.generation` và refresh snapshot `resume_revision`, `job_revision` về revision hiện tại.

Task nhận:
`match_id, expected_generation, expected_resume_revision, expected_job_revision, algorithm_version`.

Claim chỉ hợp lệ khi row còn `PENDING`, generation/snapshots khớp, linked Resume/JD revisions khớp expected **và cả hai linked resource `is_deleted=false`**. Chỉ một worker được chuyển `PENDING -> PROCESSING`.

Terminal COMPLETED/FAILED chỉ hợp lệ từ `PROCESSING` với cùng generation/snapshots, linked revisions vẫn khớp và linked resources vẫn chưa xóa. `rowcount=0` => stale/duplicate/deleted-resource task discard.

### 8.4 Match dispatch và PENDING recovery
- Celery task nội bộ `match.calculate` nhận đúng 5 trường ở mục 8.3; không gửi scoring context hoặc embedding qua queue.
- API startup/periodic sweep chọn Match `PENDING`, `updated_at` quá configurable grace window, algorithm được worker hiện tại hỗ trợ, snapshots khớp linked revisions và cả Resume/JD chưa soft-delete.
- Sweep chỉ đọc DB; đóng selection session trước khi enqueue lại nguyên generation, snapshots và algorithm đã lưu. Không tăng generation/revision, không đổi status, payload hoặc `updated_at`.
- Duplicate delivery hoặc mutation/delete sau selection vẫn phải qua exclusive claim/terminal CAS ở mục 8.3. Không giữ DB lock trong lúc gọi broker.
- Broker chưa cấu hình thì dispatch là no-op và recovery không chạy. Lỗi một publication không dừng phần còn lại của batch; lỗi sweep được retry ở interval sau.
- Chỉ recovery work chưa được claim; không reset `PROCESSING` khi chưa có lease/attempt token.

## 9. Idempotent creation, request fingerprint và parse dispatch recovery
### 9.1 Stable application namespace
Hai create endpoint dùng **một application UUIDv5 namespace cố định**, literal trong code/config source và không thay đổi theo deployment:

`bd7b1f30-b2de-549c-a8dd-8d742ee5bc12`

Không generate namespace ngẫu nhiên khi startup và không lấy namespace từ biến môi trường có thể đổi. Resource id được derive:

```text
UUIDv5(APP_IDEMPOTENCY_NAMESPACE,
      actor_id + "\n" + canonical_route + "\n" + lower(Idempotency-Key UUID string))
```

Canonical route chỉ là `/api/v1/resumes/upload` hoặc `/api/v1/jobs`.

### 9.2 Request fingerprint SHA-256
`POST /resumes/upload` và `POST /jobs` bắt buộc `Idempotency-Key` UUID. Mỗi deterministic resource persist `create_request_fingerprint` lowercase hex 64 ký tự.

**Resume fingerprint:** `SHA-256(raw file bytes)` sau khi backend đã đọc file để validate size/MIME/magic. `file_name` không tham gia fingerprint; retry cùng bytes nhưng tên client khác vẫn được coi là cùng logical upload và trả metadata đã persist ban đầu.

**Job fingerprint:** SHA-256 của canonical UTF-8 JSON của **validated create payload sau khi server áp dụng defaults**. Canonicalization v1:
- keys cố định: `title`, `job_level`, `location`, `raw_content`, `w_skill`, `w_semantic`, `w_experience`;
- text Unicode NFC; `title/job_level/location` trim outer whitespace; `location` rỗng -> `null`;
- `raw_content` giữ nội dung nhưng chuẩn hóa CRLF/CR -> LF;
- weights dùng giá trị server-resolved và serialize fixed 3 decimal digits;
- JSON không whitespace thừa, UTF-8, key order cố định.

Quy tắc retry:
- cùng actor + route + key -> cùng deterministic id;
- resource đã tồn tại và fingerprint **giống** -> trả đúng resource hiện hữu, không create side-effect mới;
- resource đã tồn tại và fingerprint **khác** -> `409 IDEMPOTENCY_KEY_REUSED`;
- concurrent insert cùng deterministic PK: request thua unique race phải re-read row và so fingerprint; giống -> return existing, khác -> 409;
- muốn tạo logical resource mới phải dùng key mới.

### 9.3 Resume storage orphan/reconciliation
Storage key create là deterministic theo resource id và không phụ thuộc filename, canonical form: `resumes/{resume_id}/source`.

Flow khi DB chưa có Resume:
1. `put-if-absent`/no-overwrite vào deterministic storage key;
2. nếu object vừa được tạo: tiếp tục INSERT Resume với fingerprint;
3. nếu object đã tồn tại nhưng DB row chưa tồn tại: compute SHA-256 bytes của object hiện hữu;
4. fingerprint object == request fingerprint -> reuse object và retry INSERT DB;
5. fingerprint object != request fingerprint -> `409 IDEMPOTENCY_KEY_REUSED`, không overwrite, không nhận object đó là file của request mới.

Nếu storage write thành công nhưng DB INSERT thất bại, object được phép tồn tại như orphan tạm thời. Retry cùng logical request sẽ đi qua bước fingerprint reconciliation phía trên. Không xóa mù object trong error path vì có thể đang có request concurrent hợp lệ.

### 9.4 Dispatch sau commit
DB/resource creation commit trước, dispatch parse sau. Nếu immediate dispatcher lỗi:
- resource vẫn ở `PENDING`;
- server log lỗi dispatch;
- endpoint vẫn trả resource đã tạo (`202` Resume, `201` Job) vì persistence đã thành công;
- client **không cần POST tạo lại chỉ để cứu queue**.

Nếu response mạng bị mất và client retry POST, cùng Idempotency-Key + cùng fingerprint trả cùng resource.

### 9.5 Parse Recovery Sweeper
MVP có recovery/re-dispatch nội bộ cho Resume/JD `PENDING` chưa được claim:
- periodic/startup sweep chọn resource `PENDING`, `is_deleted=false`, `updated_at` cũ hơn grace window cấu hình;
- enqueue lại `(resource_id, current_revision)` mà **không tăng revision** và không đổi payload;
- duplicate re-dispatch an toàn vì exclusive CAS chỉ cho một worker claim `PENDING -> PROCESSING`.

Recovery này xử lý failure **trước khi worker claim**. Recovery stuck `PROCESSING` sau worker crash cần lease/attempt token riêng và thuộc hardening sau MVP; không được reset mù PROCESSING về PENDING.

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
- Nếu một item fail ownership/readiness/deleted-state -> cả request fail, không sửa Match nào.
- Chuẩn bị/upsert toàn batch trong **một transaction**; mỗi row generation++ và snapshot revision hiện tại.
- Sau commit mới dispatch task.
- Nếu dispatcher lỗi, endpoint trả `503 TASK_DISPATCH_FAILED`; rows đã chuẩn bị có thể còn PENDING và request có thể retry an toàn. Lần retry tăng generation nên task cũ không overwrite task mới.

## 12. Nguồn chuẩn khi code
1. `PTTK_MASTER.md` + requirements/business rules.
2. `05_database/schema.sql` + `skill_taxonomy_seed.sql`.
3. `07_api/api_contract.md` + `openapi.yaml`.
4. Use Case / Activity / Sequence / Architecture.
5. Code.

Muốn đổi API/database/enum/concurrency/idempotency rule phải sửa PTTK trước.

## 13. Gate trước code
- CI OpenAPI/PlantUML/schema/design PASS.
- PR review/merge.
- reset DB cũ.
- chạy `schema.sql`.
- chạy `skill_taxonomy_seed.sql`.
- verify 10 tables, constraints, HNSW, taxonomy seed.
- smoke-test duplicate delivery CAS, stale revision/generation, soft-delete race guard, parse recovery re-dispatch.
- smoke-test Idempotency-Key: same key+same fingerprint returns same resource; same key+different fingerprint -> 409; concurrent duplicate create; Resume storage orphan same/different fingerprint reconciliation.
- sau đó mới ORM/Auth/API/AI.
