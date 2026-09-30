# THIẾT KẾ PIPELINE AI/NLP

## CV
1. Validate PDF/DOCX <=5MB, MIME/magic bytes, safe storage key.
2. Extract text; OCR fallback Advanced.
3. Preprocess Unicode/whitespace/sections.
4. Regex/NER/rules trích contact/experience/education.
5. Map về Skill Taxonomy seed; không tự INSERT skill lạ.
6. Persist profile/skills/experience/education.
7. Embed canonical CV text.
8. Lưu `embedding_model` + `embedding_preprocessing_version`.
9. Human-in-the-loop.

Resume có `revision`. Parse/reparse task nhận `expected_revision`; trước commit worker phải `UPDATE ... WHERE id=? AND revision=expected_revision`. Rowcount=0 => stale task, discard.

Khi manual parsed data đổi:
- revision++;
- regenerate embedding;
- invalidate Match + generation++;
- cùng transaction cho canonical structured data/revision/invalidation.

## JD
1. Normalize/section raw JD.
2. Extract experience/education/skill phrase.
3. Map taxonomy seed.
4. MANDATORY/OPTIONAL.
5. Embed bằng cùng model + preprocessing baseline.
6. Persist parsed data.
7. HR verify criteria.

Raw JD đổi:
- revision++;
- DRAFT/PENDING;
- clear old embedding/model/preprocessing/verification;
- invalidate Match;
- enqueue parse(job_id, expected_revision).
Old task không được overwrite revision mới.

Criteria/weights/model/preprocessing đổi cũng revision++ + invalidate.

## Task Dispatcher
Celery+Redis là target; local fallback được phép nhưng interface/state không đổi.
Dispatch Match xảy ra **sau DB transaction chuẩn bị batch**. Nếu dispatcher failure, API trả 503; PENDING rows retry được.

## Matching task concurrency
Task payload:
`match_id, expected_generation, expected_resume_revision, expected_job_revision, algorithm_version`.

Worker:
1. conditional claim PROCESSING theo generation;
2. load Resume/JD và verify revisions/model/preprocessing;
3. compute;
4. terminal UPDATE chỉ nếu generation + revisions vẫn khớp.
Không khớp => stale task discard, không ghi FAILED lên generation mới.

## Versioning
`embedding_model` và `embedding_preprocessing_version` đều là phần identity của vector.
Model giống nhưng preprocessing khác => không được match.
Benchmark phải ghi algorithm/model/preprocessing version.

## LLM/XAI
Advanced; chỉ explanation/recommendation, không sửa deterministic score/evidence.
