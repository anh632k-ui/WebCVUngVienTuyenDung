-- R3C Phase 3 aggregate inventory template.
-- Operator execution requires a separately approved target and read-only account.
-- Output contains counts only: no resource IDs, CV/JD data, errors, embeddings,
-- personal data, or ownership capability values.

BEGIN TRANSACTION READ ONLY;

WITH canonical_tables(table_name) AS (
    VALUES
        ('resumes'::text),
        ('job_descriptions'::text),
        ('match_results'::text)
), processing_rows AS (
    SELECT
        'resumes'::text AS table_name,
        processing_attempt_epoch,
        processing_owner_token IS NULL AS owner_is_null,
        processing_activation_token IS NULL AS activation_is_null,
        processing_heartbeat_at IS NULL AS heartbeat_is_null,
        processing_lease_expires_at IS NULL AS lease_is_null,
        processing_lease_expires_at,
        is_deleted AS snapshot_invalidated
    FROM resumes
    WHERE parsing_status = 'PROCESSING'

    UNION ALL

    SELECT
        'job_descriptions'::text,
        processing_attempt_epoch,
        processing_owner_token IS NULL,
        processing_activation_token IS NULL,
        processing_heartbeat_at IS NULL,
        processing_lease_expires_at IS NULL,
        processing_lease_expires_at,
        is_deleted
    FROM job_descriptions
    WHERE parsing_status = 'PROCESSING'

    UNION ALL

    SELECT
        'match_results'::text,
        processing_attempt_epoch,
        processing_owner_token IS NULL,
        processing_activation_token IS NULL,
        processing_heartbeat_at IS NULL,
        processing_lease_expires_at IS NULL,
        processing_lease_expires_at,
        NOT EXISTS (
            SELECT 1
            FROM resumes
            WHERE resumes.id = match_results.resume_id
              AND resumes.revision = match_results.resume_revision
              AND resumes.is_deleted = false
        ) OR NOT EXISTS (
            SELECT 1
            FROM job_descriptions
            WHERE job_descriptions.id = match_results.job_id
              AND job_descriptions.revision = match_results.job_revision
              AND job_descriptions.is_deleted = false
        )
    FROM match_results
    WHERE match_results.status = 'PROCESSING'
), classified AS (
    SELECT
        table_name,
        processing_attempt_epoch,
        owner_is_null AND activation_is_null AND heartbeat_is_null AND lease_is_null
            AS legacy_unowned,
        NOT owner_is_null AND NOT activation_is_null
            AND NOT heartbeat_is_null AND NOT lease_is_null AS fenced_owned,
        processing_lease_expires_at,
        snapshot_invalidated
    FROM processing_rows
), aggregated AS (
SELECT
    table_name,
    count(*) AS processing_total,
    count(*) FILTER (WHERE legacy_unowned) AS legacy_unowned,
    count(*) FILTER (WHERE fenced_owned) AS fenced_owned,
    count(*) FILTER (
        WHERE NOT legacy_unowned AND NOT fenced_owned
    ) AS inconsistent_metadata,
    count(*) FILTER (WHERE snapshot_invalidated) AS snapshot_invalidated,
    count(*) FILTER (
        WHERE fenced_owned AND processing_lease_expires_at <= clock_timestamp()
    ) AS expired_lease,
    count(*) FILTER (
        WHERE fenced_owned AND processing_lease_expires_at > clock_timestamp()
    ) AS current_lease,
    min(processing_attempt_epoch) AS minimum_attempt_epoch,
    max(processing_attempt_epoch) AS maximum_attempt_epoch
FROM classified
GROUP BY table_name
)
SELECT
    canonical_tables.table_name,
    coalesce(aggregated.processing_total, 0) AS processing_total,
    coalesce(aggregated.legacy_unowned, 0) AS legacy_unowned,
    coalesce(aggregated.fenced_owned, 0) AS fenced_owned,
    coalesce(aggregated.inconsistent_metadata, 0) AS inconsistent_metadata,
    coalesce(aggregated.snapshot_invalidated, 0) AS snapshot_invalidated,
    coalesce(aggregated.expired_lease, 0) AS expired_lease,
    coalesce(aggregated.current_lease, 0) AS current_lease,
    aggregated.minimum_attempt_epoch,
    aggregated.maximum_attempt_epoch
FROM canonical_tables
LEFT JOIN aggregated USING (table_name)
ORDER BY canonical_tables.table_name;

COMMIT;
