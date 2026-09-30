# TRACEABILITY MATRIX — RELIABILITY LOCK

| UC | FR | Activity | Sequence | API | Data |
|---|---|---|---|---|---|
| UC01 Register | FR-01 | act_uc01_register | — | POST /auth/register | users |
| UC02 Login | FR-02 | act_uc02_03_login_session | seq_login | POST /auth/login | users |
| UC03 Refresh [Advanced] | FR-03 | act_uc02_03_login_session | seq_refresh_logout | Advanced only, not OpenAPI MVP | Redis optional |
| UC04 Profile | FR-04 | act_uc04_05_profile_password | seq_account_management | GET/PUT /users/me | users |
| UC05 Password | FR-05 | act_uc04_05_profile_password | seq_account_management | PUT /auth/change-password | users |
| UC06 Admin user | FR-06 | act_uc06_admin_users | — | GET/PATCH /admin/users | users + resource existence check |
| UC07 Upload CV | FR-07,08,43,44 | act_uc07_08_upload_parse_cv | seq_upload_parse_cv | POST /resumes/upload + Idempotency-Key | resumes.revision + deterministic id |
| UC08 Parse CV | FR-09,10,15,45 | act_uc07_08_upload_parse_cv | seq_upload_parse_cv | background + status | resume aggregate + expected_revision + PENDING recovery |
| UC09 Review CV | FR-11,12,38 | act_uc09_10_review_manage_cv | seq_review_cv | GET/PUT resume parsed-data + /skills | resume aggregate + revision + match generation |
| UC10 CV pool | FR-13,14 | act_uc09_10_review_manage_cv | — | list/detail/status/download/delete | resumes |
| UC11 JD management | FR-16,17,23,43,44 | act_uc11_12_manage_parse_jd | seq_create_parse_job | CRUD/status /jobs + Idempotency-Key on create | job_descriptions.revision + deterministic id |
| UC12 Parse JD | FR-19,20,24,45 | act_uc11_12_manage_parse_jd | seq_create_parse_job | background | job aggregate + expected_revision + PENDING recovery |
| UC13 Criteria | FR-21,38 | act_uc13_review_job_criteria | seq_job_criteria_weights | GET/PUT criteria + /skills | job_skills + revision + match generation |
| UC14 Browse JD | FR-18 | act_uc14_browse_jobs | — | GET /jobs | job_descriptions |
| UC15 Matching | FR-25..31,34..36 | act_uc15_matching | seq_matching | POST /matching/calculate | match_results generation/revisions/status CAS |
| UC16 Results/Gap | FR-32,37,40 | act_uc16_gap_analysis | seq_gap_leaderboard | GET /matching + detail + gap | match_results |
| UC17 Leaderboard | FR-33 | act_uc17_18_leaderboard_weights | seq_gap_leaderboard | GET /jobs/{id}/leaderboard | match_results + ownership joins |
| UC18 Weights | FR-22,34 | act_uc17_18_leaderboard_weights | seq_job_criteria_weights | PUT /jobs/{id}/weights | job revision + match generation |
| UC19 Export [Advanced] | FR-39 | act_uc19_export_report | — | not OpenAPI MVP | read-only analytics |

## Cross-cutting

| Requirement / invariant | Artefact chain |
|---|---|
| Skill taxonomy bootstrap FR-42 | seed SQL -> AI pipeline -> GET /skills -> CV/JD normalization |
| Candidate profile 0..1 | actors/ownership -> CDM/LDM/PDM/ERD -> schema UNIQUE(resume_id) -> OpenAPI nullable candidate_profile |
| Role transition safety | BR-AUTH-07 -> UC06/activity -> API 409 -> service resource existence query |
| Refresh is Advanced | PTTK_MASTER -> BFD/UC03 -> API Contract Advanced -> OpenAPI route absent |
| JD create only HR | ownership -> BR-JOB-02 -> UC11 -> API Contract/OpenAPI POST /jobs |
| JD status matrix | state_models -> BR-JOB-10 -> UC11 -> PATCH /jobs/{id}/status |
| Model + preprocessing identity | NFR AI -> schema -> AI pipeline -> matching precondition -> Match provenance |
| Revision meaning | PTTK_MASTER §8.1 -> BR-CV-06/BR-JOB-06/BR-AI-04 -> Activity/Sequence -> worker contract |
| Parse exclusive CAS | FR-15/24 -> BR-CV-07/BR-JOB-11 -> CV/JD Activity -> CV/JD Sequence -> version_guard.py |
| Match exclusive CAS | FR-35 -> BR-MATCH-13 -> Matching Activity -> Matching Sequence -> matching_algorithm -> version_guard.py |
| Idempotent create FR-43 | BR-CV-11/BR-JOB-12/BR-REL-01 -> API Idempotency-Key -> UUIDv5 guard -> create Activity/Sequence |
| Post-commit parse recovery FR-44/45 | BR-CV-12/BR-JOB-13/BR-REL-02/03 -> Architecture Recovery Sweeper -> CV/JD Sequence |
| Resume/JD revision | BR-CV-06/07, BR-JOB-06/09/11 -> schema -> Activity/Sequence -> workers |
| Match generation | BR-MATCH-11/13 -> schema -> matching Activity/Sequence -> conditional terminal write |
| Batch atomicity | BR-MATCH-14/15 -> UC15 -> Activity/Sequence -> API 503 |
| Experience missing dates | BR-MATCH-07 -> matching_algorithm -> UC15 |
| Match error state | BR-MATCH-16 -> schema `chk_match_error_state` -> worker terminal flow |
| Privacy | ownership -> BR-MATCH-09/10 -> UC15-17 -> API scope |
| Advanced Export | FR-39 -> UC19 -> API Contract Advanced -> absent OpenAPI |

## Audit rules
1. Route MVP trong diagram phải tồn tại trong OpenAPI; Advanced route phải vắng mặt cho tới khi bật.
2. Field/enum/provenance phải khớp Requirements -> Schema -> API.
3. Parse claim phải kiểm `expected_revision + PENDING`; terminal phải kiểm cùng revision + `PROCESSING`.
4. Match claim phải kiểm expected generation/revisions + `PENDING`; terminal phải kiểm same expected values + `PROCESSING`.
5. Duplicate delivery cùng revision/generation chỉ một worker được claim.
6. `revision` tăng trước computation mới; worker của chính expected revision không tăng khi terminal commit.
7. `POST /resumes/upload` và `POST /jobs` phải có required `Idempotency-Key`; retry cùng logical request không tạo duplicate resource.
8. Parse dispatcher failure sau persistence phải để PENDING và có internal recovery re-dispatch current revision.
9. Recovery không được tăng revision hoặc reset PROCESSING mù trong MVP.
10. Candidate profile luôn 0..1, không bắt response phải có object trước PARSED.
11. Môi trường mới không đạt ready nếu chưa chạy taxonomy seed.
12. Batch invalid item không được mutate partial Match rows.
13. Matching dispatch failure phải có retry semantics xác định.
