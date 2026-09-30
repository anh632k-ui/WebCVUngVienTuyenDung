# MATCHING ALGORITHM — hybrid-v1

## Preconditions
Resume/JD PARSED, chưa xóa, quyền hợp lệ; JD verified + >=1 job_skill; Candidate cần JD ACTIVE.
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

## Provenance
COMPLETED Match lưu:
- `algorithm_version`;
- `embedding_model`;
- `embedding_preprocessing_version`;
- snapshot `resume_revision`, `job_revision`;
- generation của current computation.

## Invalidation
Input scoring đổi => resource revision++ + Match generation++ + canonical clear payload.
Non-COMPLETED không giữ score/evidence/embedding provenance/calculated_at.

## Stale-worker protection
Mỗi enqueue tăng/ghi generation và snapshot revisions. Worker chỉ commit nếu:
`generation==expected_generation`
AND `resume.revision==expected_resume_revision`
AND `job.revision==expected_job_revision`.

Phải re-check ngay lúc terminal DB write. Nếu conditional UPDATE rowcount=0, task stale và kết quả bị bỏ.

## Batch semantics
1. validate toàn bộ resume_ids;
2. nếu một item fail => no mutation;
3. atomic transaction prepare/upsert all Match rows;
4. commit;
5. dispatch each task;
6. dispatch failure => 503. Client retry same request; retry increments generation, nên task cũ không overwrite.

## Privacy
Candidate đọc Match CV mình. HR cần JD+CV cùng thuộc HR. Admin override.

## Benchmark
MAE/Pearson/NDCG@K/Precision@K chỉ khi có ground truth; report algorithm/model/preprocessing version.
