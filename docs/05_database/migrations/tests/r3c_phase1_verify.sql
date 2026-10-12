-- Assertions for the disposable PostgreSQL 18 migration target.
\set ON_ERROR_STOP on

DO $verify$
DECLARE
    column_count integer;
    table_count integer;
    skill_count integer;
    legacy_count integer;
    expected record;
    actual record;
    normalized_predicate text;
BEGIN
    SELECT count(*) INTO table_count
    FROM information_schema.tables
    WHERE table_schema = 'public' AND table_type = 'BASE TABLE';
    SELECT count(*) INTO skill_count FROM public.skills;
    IF table_count <> 10 OR skill_count <> 42 THEN
        RAISE EXCEPTION 'identity invariant changed: tables %, skills %', table_count, skill_count;
    END IF;

    SELECT count(*) INTO column_count
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name IN ('resumes', 'job_descriptions', 'match_results')
      AND column_name IN (
          'processing_attempt_epoch', 'processing_owner_token',
          'processing_activation_token', 'processing_heartbeat_at',
          'processing_lease_expires_at'
      );
    IF column_count <> 15 THEN
        RAISE EXCEPTION 'expected 15 Phase 1 columns, found %', column_count;
    END IF;

    FOR expected IN
        SELECT *
        FROM (VALUES
            ('resumes'), ('job_descriptions'), ('match_results')
        ) AS tables(table_name)
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = expected.table_name
              AND column_name = 'processing_attempt_epoch'
              AND data_type = 'bigint'
              AND is_nullable = 'NO'
              AND column_default IN ('0', '''0''::bigint')
        ) OR (
            SELECT count(*) FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = expected.table_name
              AND column_name IN ('processing_owner_token', 'processing_activation_token')
              AND data_type = 'uuid'
              AND is_nullable = 'YES'
              AND column_default IS NULL
        ) <> 2 OR (
            SELECT count(*) FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = expected.table_name
              AND column_name IN ('processing_heartbeat_at', 'processing_lease_expires_at')
              AND data_type = 'timestamp with time zone'
              AND is_nullable = 'YES'
              AND column_default IS NULL
        ) <> 2 THEN
            RAISE EXCEPTION 'Phase 1 column metadata mismatch on public.%', expected.table_name;
        END IF;
    END LOOP;

    SELECT count(*) INTO legacy_count
    FROM (
        SELECT 1 FROM resumes
        WHERE id = '20000000-0000-0000-0000-000000000001'
          AND revision = 7 AND parsing_status = 'PROCESSING'
          AND file_name = 'legacy.pdf'
          AND processing_attempt_epoch = 0
          AND processing_owner_token IS NULL
          AND processing_activation_token IS NULL
          AND processing_heartbeat_at IS NULL
          AND processing_lease_expires_at IS NULL
        UNION ALL
        SELECT 1 FROM job_descriptions
        WHERE id = '30000000-0000-0000-0000-000000000001'
          AND revision = 9 AND parsing_status = 'PROCESSING'
          AND title = 'Legacy Job'
          AND processing_attempt_epoch = 0
          AND processing_owner_token IS NULL
          AND processing_activation_token IS NULL
          AND processing_heartbeat_at IS NULL
          AND processing_lease_expires_at IS NULL
        UNION ALL
        SELECT 1 FROM match_results
        WHERE id = '40000000-0000-0000-0000-000000000001'
          AND generation = 11 AND resume_revision = 7 AND job_revision = 9
          AND status = 'PROCESSING'
          AND processing_attempt_epoch = 0
          AND processing_owner_token IS NULL
          AND processing_activation_token IS NULL
          AND processing_heartbeat_at IS NULL
          AND processing_lease_expires_at IS NULL
    ) preserved;
    IF legacy_count <> 3 THEN
        RAISE EXCEPTION 'pre-existing PROCESSING row preservation failed';
    END IF;

    FOR expected IN
        SELECT *
        FROM (VALUES
            ('idx_resumes_processing_lease', 'resumes', 'parsing_status'),
            ('idx_jobs_processing_lease', 'job_descriptions', 'parsing_status'),
            ('idx_matches_processing_lease', 'match_results', 'status')
        ) AS specification(index_name, table_name, status_column)
    LOOP
        SELECT
            table_class.relname AS table_name,
            index_row.indisvalid,
            index_row.indisunique,
            ARRAY(
                SELECT attribute.attname
                FROM unnest(index_row.indkey) WITH ORDINALITY AS key(attnum, position)
                JOIN pg_catalog.pg_attribute attribute
                  ON attribute.attrelid = index_row.indrelid
                 AND attribute.attnum = key.attnum
                ORDER BY key.position
            ) AS columns,
            pg_catalog.pg_get_expr(index_row.indpred, index_row.indrelid) AS predicate
        INTO actual
        FROM pg_catalog.pg_class index_class
        JOIN pg_catalog.pg_namespace n ON n.oid = index_class.relnamespace
        JOIN pg_catalog.pg_index index_row ON index_row.indexrelid = index_class.oid
        JOIN pg_catalog.pg_class table_class ON table_class.oid = index_row.indrelid
        WHERE n.nspname = 'public' AND index_class.relname = expected.index_name;

        normalized_predicate := pg_catalog.regexp_replace(
            pg_catalog.replace(
                pg_catalog.replace(pg_catalog.replace(actual.predicate, '::text', ''), '(', ''),
                ')', ''
            ),
            '[[:space:]]', '', 'g'
        );
        IF NOT FOUND
           OR actual.table_name <> expected.table_name
           OR NOT actual.indisvalid
           OR actual.indisunique
           OR actual.columns <> ARRAY['processing_lease_expires_at', 'id']
           OR normalized_predicate <>
              expected.status_column || '=''PROCESSING''ANDprocessing_lease_expires_atISNOTNULL'
        THEN
            RAISE EXCEPTION 'index verification failed for %', expected.index_name;
        END IF;
    END LOOP;
END
$verify$;
