-- Disposable CI fixture: valid PROCESSING rows created before the Phase 1 migration.
\set ON_ERROR_STOP on

INSERT INTO users (id, email, password_hash, full_name, role)
VALUES
    ('10000000-0000-0000-0000-000000000001', 'r3c-candidate@example.invalid', 'not-a-secret', 'R3C Candidate', 'CANDIDATE'),
    ('10000000-0000-0000-0000-000000000002', 'r3c-hr@example.invalid', 'not-a-secret', 'R3C HR', 'HR');

INSERT INTO resumes (
    id, owner_user_id, file_name, storage_key, file_size, mime_type,
    create_request_fingerprint, revision, parsing_status
)
VALUES (
    '20000000-0000-0000-0000-000000000001',
    '10000000-0000-0000-0000-000000000001',
    'legacy.pdf', 'r3c/legacy/resume', 128, 'application/pdf',
    repeat('a', 64), 7, 'PROCESSING'
);

INSERT INTO job_descriptions (
    id, recruiter_id, title, job_level, raw_content,
    create_request_fingerprint, revision, parsing_status, status
)
VALUES (
    '30000000-0000-0000-0000-000000000001',
    '10000000-0000-0000-0000-000000000002',
    'Legacy Job', 'MID', 'Legacy processing fixture',
    repeat('b', 64), 9, 'PROCESSING', 'DRAFT'
);

INSERT INTO match_results (
    id, job_id, resume_id, generation, resume_revision, job_revision,
    algorithm_version, status
)
VALUES (
    '40000000-0000-0000-0000-000000000001',
    '30000000-0000-0000-0000-000000000001',
    '20000000-0000-0000-0000-000000000001',
    11, 7, 9, 'hybrid-v1', 'PROCESSING'
);
