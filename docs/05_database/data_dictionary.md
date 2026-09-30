# DATA DICTIONARY — CANONICAL

`schema.sql` là nguồn vật lý cuối cùng; `skill_taxonomy_seed.sql` là bootstrap data bắt buộc cho demo.

## users
`id` UUID; `email` case-insensitive unique; `password_hash`; `full_name`; `phone_number`; `role=CANDIDATE|HR|ADMIN`; `is_active`; audit timestamps.

Role change là service invariant: CANDIDATE<->HR chỉ khi không còn Resume/JD chưa soft-delete.

## skills
`id`, `name`, `normalized_name` unique, `skill_kind=HARD|SOFT`, `category`, `description`.
Pipeline không tự thêm skill lạ. Seed baseline chạy sau schema.

## resumes
- ownership: `owner_user_id`.
- file: `file_name`, `storage_key`, `file_size`, `mime_type`.
- `revision`: phiên bản input scoring/parse canonical, >=1.
- parse: `parsing_status`, `raw_text`, `error_message`, `parsed_at`.
- embedding: `resume_embedding vector(1024)`, `embedding_model`, `embedding_preprocessing_version`.
- manual/soft delete flags.

PARSED yêu cầu raw_text + embedding + model + preprocessing version + parsed_at.
FAILED yêu cầu error; state khác bắt buộc error NULL.

## candidate_profiles
Snapshot nghề nghiệp từ Resume, **0..1** row/Resume; `resume_id` UNIQUE. PENDING/FAILED có thể không có row.

## resume_skills
`resume_id`, `skill_id`, optional `years_of_experience`, `proficiency_level`; unique pair.

## resume_experiences
Company/title, nullable dates, `is_current`. Khi scoring:
- thiếu start_date -> interval không định lượng;
- current + end NULL -> dùng current date;
- non-current + end NULL -> không định lượng;
- merge overlap trước cộng tổng.

## resume_educations
Institution/degree/field/start_year/graduation_year/gpa/description.

## job_descriptions
- ownership `recruiter_id` luôn là HR tạo JD.
- `revision` >=1.
- raw metadata + criteria summary.
- embedding vector/model/**preprocessing version**.
- parse state + parsing error.
- verified flag + weights + business status.
- soft delete/audit timestamps.

Raw content/criteria/weights/model/preprocessing mutation ảnh hưởng score => revision++ + Match invalidation.

## job_skills
JD taxonomy requirements; `importance=MANDATORY|OPTIONAL`; min years; unique pair.

## match_results
Một current row/pair.
- concurrency: `generation`, `resume_revision`, `job_revision`.
- scores/evidence.
- provenance: `algorithm_version`, `embedding_model`, `embedding_preprocessing_version`.
- lifecycle + error/timestamps.

COMPLETED yêu cầu 4 score + embedding provenance + calculated_at.
Non-COMPLETED phải clear score/evidence/embedding provenance/calculated_at.
FAILED bắt buộc error_message; state khác error NULL.

### Stale-worker rule
Task phải mang expected generation/revisions. Terminal UPDATE chỉ được commit nếu row generation và linked Resume/JD revisions vẫn khớp; rowcount=0 => stale task discard.

### Privacy
Candidate: resume owner. HR: đồng thời JD recruiter + resume owner. Admin override.
