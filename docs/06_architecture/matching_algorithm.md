# MATCHING ALGORITHM — hybrid-v1

## Preconditions
Resume/JD PARSED, chưa soft-delete, quyền hợp lệ; JD verified + >=1 job_skill; Candidate cần JD ACTIVE.
Embedding phải tồn tại và cùng:
- dimension;
- `embedding_model`;
- `embedding_preprocessing_version`.

## Skill Score
MANDATORY=2, OPTIONAL=1.
`SkillScore = 100 * sum(weight_i*matched_i)/sum(weight_i)`.

## Semantic Score
`SemanticScore = 100 * clamp(cosine(resume_vec,job_vec),0,1)`.
BM25 chỉ diagnostic/retrieval/experiment trong v1.

## Experience Score
Tạo các employment interval định lượng:
- cần `start_date`;
- `is_current=true` và `end_date=NULL` => end=current date;
- `is_current=false` mà end_date=NULL => interval không định lượng;
- thiếu start => không định lượng;
- merge overlap trước khi cộng.

Nếu `required<=0`: 100.
Nếu `required>0` và không có interval định lượng: 0.
Nếu candidate>=required: 100.
Ngược lại `100*candidate/required`.
Không đoán số năm từ title/description khi không có date đủ tin cậy.

## Overall
`Overall=w_skill*Skill+w_semantic*Semantic+w_experience*Experience`, defaults 0.5/0.3/0.2.

## Gap
Missing MANDATORY=CRITICAL; OPTIONAL=MINOR. Skill có nhưng thiếu years có thể PARTIAL evidence.

## Revision semantics
Resume/JD `revision` là version của input/computation request. Service tăng revision **trước** computation mới khi input đổi. Worker của chính `expected_revision` không tăng revision khi commit output.

## Provenance
COMPLETED Match lưu:
- `algorithm_version`;
- `embedding_model`;
- `embedding_preprocessing_version`;
- snapshot `resume_revision`, `job_revision`;
- generation của current computation.

## Invalidation
Input scoring đổi => resource revision++ khi phù hợp + Match generation++ + canonical clear payload.
Trong transaction invalidation phải **refresh cả hai snapshot** của Match:
- `resume_revision = resumes.revision` hiện tại;
- `job_revision = job_descriptions.revision` hiện tại.
Non-COMPLETED không giữ score/evidence/embedding provenance/calculated_at.

## Exclusive stale/duplicate/deleted-worker protection
Queue được phép delivery cùng task nhiều lần. Safety không dựa vào giả định exactly-once.

### Claim CAS
Chỉ một worker được claim current generation:
- `status = PENDING`;
- `generation = expected_generation`;
- stored `resume_revision/job_revision` = expected snapshots;
- linked Resume/JD current revisions = expected snapshots;
- linked Resume/JD `is_deleted = FALSE`.

Worker phải atomic conditional update `PENDING -> PROCESSING`. Chỉ `rowcount=1` được tính score. Duplicate/stale/deleted-resource delivery `rowcount=0` phải discard.

### Terminal CAS
COMPLETED hoặc FAILED chỉ được ghi khi:
- `status = PROCESSING`;
- generation + stored snapshots + linked resource revisions vẫn khớp expected;
- linked Resume/JD vẫn `is_deleted=FALSE`.

Phải re-check ngay tại terminal DB write. Nếu `rowcount=0`, result bị discard. Vì chỉ một worker claim PENDING và cả FAILED/COMPLETED đều yêu cầu PROCESSING của generation đó, hai duplicate worker không được race terminal state. Nếu resource bị soft-delete sau claim nhưng trước terminal write, terminal CAS phải fail và kết quả không được ghi.

## Current result visibility
`GET /matching`, Gap Analysis và Leaderboard chỉ coi Match là current/visible khi linked Resume và JD đều chưa soft-delete. Soft-deleted linked resource không được xuất hiện trong current analytics dù row Match còn tồn tại để giữ referential history nội bộ.

## Batch semantics
1. validate toàn bộ resume_ids, gồm `is_deleted=false` cho Resume/JD;
2. nếu một item fail => no mutation;
3. atomic transaction prepare/upsert all Match rows, generation++ và refresh revision snapshots;
4. commit;
5. dispatch each task theo values vừa commit;
6. dispatch failure => 503. Client retry same request; retry increments generation, nên task cũ không overwrite.

## Privacy
Candidate đọc Match CV mình. HR cần JD+CV cùng thuộc HR. Admin override. Mọi scope vẫn phải filter linked resource chưa xóa.

## Benchmark
MAE/Pearson/NDCG@K/Precision@K chỉ khi có ground truth; report algorithm/model/preprocessing version.
