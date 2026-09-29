# TRACEABILITY MATRIX — USE CASE -> DESIGN -> API -> DATABASE

Mục tiêu của bảng này là bảo đảm mỗi chức năng có đường truy vết từ yêu cầu đến API/data. Khi thay đổi một dòng, phải kiểm tra các artefact liên quan trong cùng dòng.

| UC | Functional Requirement | Activity | Sequence | API chính | Bảng chính |
|---|---|---|---|---|---|
| UC01 Đăng ký | FR-01 | `03_activity/act_uc01_register.puml` | — | `POST /auth/register` | `users` |
| UC02 Đăng nhập | FR-02 | `act_uc02_03_login_session.puml` | `seq_login.puml` | `POST /auth/login` | `users` + Redis nếu refresh bật |
| UC03 Quản lý phiên | FR-03 | `act_uc02_03_login_session.puml` | `seq_refresh_logout.puml` | `POST /auth/refresh`, `/auth/logout` | Redis session; `users` reference |
| UC04 Profile | FR-04 | `act_uc04_05_profile_password.puml` | `seq_account_management.puml` | `GET/PUT /users/me` | `users` |
| UC05 Đổi mật khẩu | FR-05 | `act_uc04_05_profile_password.puml` | `seq_account_management.puml` | `PUT /auth/change-password` | `users` + Redis revoke nếu bật |
| UC06 Admin user | FR-06 | `act_uc06_admin_users.puml` | — | `GET /admin/users`, `PATCH /admin/users/{id}` | `users` |
| UC07 Upload CV | FR-07, FR-08 | `act_uc07_08_upload_parse_cv.puml` | `seq_upload_parse_cv.puml` | `POST /resumes/upload` | `resumes` |
| UC08 Phân tích CV | FR-09, FR-10 | `act_uc07_08_upload_parse_cv.puml` | `seq_upload_parse_cv.puml` | background task + status API | `resumes`, `candidate_profiles`, `skills`, `resume_skills`, `resume_experiences`, `resume_educations` |
| UC09 Review CV | FR-11, FR-12, FR-35 | `act_uc09_10_review_manage_cv.puml` | `seq_review_cv.puml` | `GET /resumes/{id}`, `PUT /resumes/{id}/parsed-data`, `GET /skills` | Resume aggregate + `skills` |
| UC10 Kho CV | FR-13, FR-14 | `act_uc09_10_review_manage_cv.puml` | — | `GET /resumes`, status/download/delete | `resumes` + child tables |
| UC11 Quản lý JD | FR-15, FR-16 | `act_uc11_12_manage_parse_jd.puml` | `seq_create_parse_job.puml` | CRUD `/jobs` | `job_descriptions` |
| UC12 Phân tích JD | FR-18, FR-19 | `act_uc11_12_manage_parse_jd.puml` | `seq_create_parse_job.puml` | background task | `job_descriptions`, `skills`, `job_skills` |
| UC13 Review criteria | FR-20, FR-35 | `act_uc13_review_job_criteria.puml` | `seq_job_criteria_weights.puml` | `GET/PUT /jobs/{id}/criteria`, `GET /skills` | `job_descriptions`, `job_skills`, `skills` |
| UC14 Xem JD Active | FR-17 | `act_uc14_browse_jobs.puml` | — | `GET /jobs`, `GET /jobs/{id}` | `job_descriptions`, `job_skills` |
| UC15 Matching | FR-22..FR-28 | `act_uc15_matching.puml` | `seq_matching.puml` | `POST /matching/calculate` | `match_results` + CV/JD aggregate tables |
| UC16 Kết quả & Skill Gap | FR-29, FR-33, FR-36 | `act_uc16_gap_analysis.puml` | `seq_gap_leaderboard.puml` | `GET /matching`, `GET /matching/{id}`, `/gap-analysis` | `match_results`, `skills` |
| UC17 Leaderboard | FR-30 | `act_uc17_18_leaderboard_weights.puml` | `seq_gap_leaderboard.puml` | `GET /jobs/{id}/leaderboard` | `match_results`, `resumes`, `candidate_profiles` |
| UC18 Trọng số | FR-21, FR-31 | `act_uc17_18_leaderboard_weights.puml` | `seq_job_criteria_weights.puml` | `PUT /jobs/{id}/weights` | `job_descriptions`, `match_results` |
| UC19 Export | FR-32 | `act_uc19_export_report.puml` | — | `GET /jobs/{id}/export` | read-only từ match/JD/CV data |

## Cross-cutting requirements

| Requirement | Design artefact |
|---|---|
| FR-33 LLM/XAI | `06_architecture/ai_nlp_pipeline.md`, `matching_algorithm.md`, UC16 |
| FR-34 Benchmark/metrics | `06_architecture/matching_algorithm.md`; báo cáo thực nghiệm giai đoạn Chapter 3 |
| FR-35 Skill Taxonomy lookup | API Contract/OpenAPI `/skills`; UC09/UC13; bảng `skills` |
| FR-36 Match history | API Contract/OpenAPI `GET /matching`; UC16; `match_results` |
| Ownership/BOLA | `00_requirements/actors_roles_ownership.md`, API Contract, mọi service có `{id}` |
| State consistency | `00_requirements/state_models.puml`, `05_database/schema.sql` |
| Vector 1024 | `05_database/schema.sql`, PDM, AI pipeline, matching algorithm |
| PostgreSQL 18 + pgvector | `06_architecture/system_architecture.puml`, PDM, schema.sql |

## Quy tắc audit traceability

Một thay đổi chỉ được coi là PTTK đồng bộ khi:

1. Tên field/enum giống nhau giữa Requirements -> API -> Schema.
2. Route trong Activity/Sequence tồn tại trong API Contract và OpenAPI.
3. Bảng/cột được Activity/Sequence tham chiếu tồn tại trong PDM/schema.sql.
4. Quyền của Use Case giống API authorization/ownership rule.
5. State transition không tạo trạng thái ngoài CHECK constraint.
6. Tính năng Advanced được đánh dấu rõ, không trình bày như đã cài đặt khi chưa code.
