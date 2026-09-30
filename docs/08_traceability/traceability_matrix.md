# TRACEABILITY MATRIX — RELIABILITY + FINGERPRINT LOCK

| UC | FR | Activity | Sequence | API | Data |
|---|---|---|---|---|---|
| UC01 Register | FR-01 | act_uc01_register | — | POST /auth/register | users |
| UC02 Login | FR-02 | act_uc02_03_login_session | seq_login | POST /auth/login | users |
| UC03 Refresh [Advanced] | FR-03 | act_uc02_03_login_session | seq_refresh_logout | Advanced only, not OpenAPI MVP | Redis optional |
| UC04 Profile | FR-04 | act_uc04_05_profile_password | seq_account_management | GET/PUT /users/me | users |
| UC05 Password | FR-05 | act_uc04_05_profile_password | seq_account_management | PUT /auth/change-password | users |
| UC06 Admin user | FR-06 | act_uc06_admin_users | — | GET/PATCH /admin/users | users + resource existence check |
| UC07 Upload CV | FR-07,08,43,44,47,48 | act_uc07_08_upload_parse_cv | seq_upload_parse_cv | POST /resumes/upload + Idempotency-Key + 409 fingerprint conflict | resumes.create_request_fingerprint + deterministic id + deterministic storage key |
| UC08 Parse CV | FR-09,10,15,45,46 | act_uc07_08_upload_parse_cv | seq_upload_parse_cv | background + status | resume aggregate + expected_revision + delete-aware CAS + PENDING recovery |
| UC09 Review CV | FR-11,12,38 | act_uc09_10_review_manage_cv | seq_review_cv | GET/PUT resume parsed-data + /skills | resume aggregate + revision + match generation |
| UC10 CV pool | FR-13,14,46 | act_uc09_10_review_manage_cv | — | list/detail/status/download/delete | resumes.is_deleted |
| UC11 JD management | FR-16,17,23,43,44,47 | act_uc11_12_manage_parse_jd | seq_create_parse_job | CRUD/status /jobs + Idempotency-Key + 409 fingerprint conflict | job_descriptions.create_request_fingerprint + deterministic id |
| UC12 Parse JD | FR-19,20,24,45,46 | act_uc11_12_manage_parse_jd | seq_create_parse_job | background | job aggregate + expected_revision + delete-aware CAS + PENDING recovery |
| UC13 Criteria | FR-21,38 | act_uc13_review_job_criteria | seq_job_criteria_weights | GET/PUT criteria + /skills | job_skills + revision + match generation |
| UC14 Browse JD | FR-18 | act_uc14_browse_jobs | — | GET /jobs | job_descriptions |
| UC15 Matching | FR-25..31,34..36,46 | act_uc15_matching | seq_matching | POST /matching/calculate | match_results generation/revisions/status CAS + linked delete state |
| UC16 Results/Gap | FR-32,37,40,46 | act_uc16_gap_analysis | seq_gap_leaderboard | GET /matching + detail + gap | match_results + linked active resources |
| UC17 Leaderboard | FR-33,46 | act_uc17_18_leaderboard_weights | seq_gap_leaderboard | GET /jobs/{id}/leaderboard | match_results + ownership + is_deleted joins |
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
| Parse exclusive CAS | FR-15 / FR-24 -> BR-CV-07/BR-JOB-11 -> CV/JD Activity -> CV/JD Sequence -> version_guard.py |
| Match exclusive CAS | FR-35 -> BR-MATCH-13 -> Matching Activity -> Matching Sequence -> matching_algorithm -> version_guard.py |
| Soft-delete async safety FR-46 | BR-REL-07 + BR-CV-07/08 + BR-JOB-11 + BR-MATCH-01/09/10/13 -> Activity/Sequence -> API Contract -> CAS guard/query filters |
| Stable UUIDv5 namespace FR-43 | PTTK_MASTER §9.1 -> BR-REL-01 -> idempotency.py/config -> CV/JD create Activity/Sequence |
| Request fingerprint FR-47 | schema/PDM/Data Dictionary -> BR-CV-12 / BR-JOB-12/13 / BR-REL-02/03 -> UC07/11 -> Activity/Sequence -> API Contract/OpenAPI 409 |
| Resume storage orphan FR-48 | BR-CV-13 / BR-REL-08 -> UC07 -> CV Activity/Sequence -> deterministic storage key + SHA-256 reconciliation |
| Post-commit parse recovery FR-44 / FR-45 | BR-CV-14/BR-JOB-14/BR-REL-04/05 -> Architecture Recovery Sweeper -> CV/JD Sequence |
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
3. Parse claim phải kiểm `expected_revision + PENDING + is_deleted=false`; terminal phải kiểm cùng revision + `PROCESSING + is_deleted=false`.
4. Match claim phải kiểm expected generation/revisions + `PENDING` + linked resources chưa xóa; terminal phải kiểm same expected values + `PROCESSING` + linked resources chưa xóa.
5. Duplicate delivery cùng revision/generation chỉ một worker được claim.
6. `revision` tăng trước computation mới; worker của chính expected revision không tăng khi terminal commit.
7. Hai create endpoint phải có required `Idempotency-Key`; namespace UUIDv5 cố định đúng literal canonical.
8. Resume/JD phải persist `create_request_fingerprint`; same key+same fingerprint => same resource, different fingerprint => `409 IDEMPOTENCY_KEY_REUSED`.
9. Resume storage orphan chỉ được reuse sau SHA-256 equality; mismatch không overwrite; deterministic PK race phải re-read persisted row và compare fingerprint.
10. Parse dispatcher failure sau persistence phải để PENDING và có internal recovery re-dispatch current revision.
11. Recovery không được tăng revision hoặc reset PROCESSING mù trong MVP; recovery chỉ chọn resource chưa xóa.
12. Concurrent soft-delete phải chặn terminal worker write và current analytics phải filter resource đã xóa.
13. Candidate profile luôn 0..1, không bắt response phải có object trước PARSED.
14. Môi trường mới không đạt ready nếu chưa chạy taxonomy seed.
15. Batch invalid item không được mutate partial Match rows.
16. Matching dispatch failure phải có retry semantics xác định.
