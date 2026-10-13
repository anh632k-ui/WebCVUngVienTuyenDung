-- Assertions for a fresh canonical PostgreSQL 18 bootstrap.
\set ON_ERROR_STOP on

DO $verify$
DECLARE
    table_count integer;
    skill_count integer;
    column_count integer;
    expected record;
    actual record;
    normalized_predicate text;
BEGIN
    SELECT count(*) INTO table_count
    FROM information_schema.tables
    WHERE table_schema = 'public' AND table_type = 'BASE TABLE';
    SELECT count(*) INTO skill_count FROM public.skills;
    IF table_count <> 10 OR skill_count <> 42 THEN
        RAISE EXCEPTION 'fresh identity invariant failed: tables %, skills %',
            table_count, skill_count;
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
        RAISE EXCEPTION 'fresh canonical expected 15 fencing columns, found %', column_count;
    END IF;

    FOR expected IN
        SELECT * FROM (VALUES
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
            RAISE EXCEPTION 'fresh fencing column mismatch on public.%', expected.table_name;
        END IF;
    END LOOP;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_constraint constraint_row
        JOIN pg_catalog.pg_namespace namespace_row
          ON namespace_row.oid = constraint_row.connamespace
        WHERE namespace_row.nspname = 'public'
          AND constraint_row.contype = 'c'
          AND pg_catalog.pg_get_constraintdef(constraint_row.oid) ~
              'processing_(owner_token|activation_token|heartbeat_at|lease_expires_at)'
    ) THEN
        RAISE EXCEPTION 'fresh canonical unexpectedly contains an ownership CHECK';
    END IF;

    FOR expected IN
        SELECT * FROM (VALUES
            ('idx_resumes_processing_lease', 'resumes', 'parsing_status'),
            ('idx_jobs_processing_lease', 'job_descriptions', 'parsing_status'),
            ('idx_matches_processing_lease', 'match_results', 'status')
        ) AS specification(index_name, table_name, status_column)
    LOOP
        SELECT
            table_class.relname AS table_name,
            access_method.amname AS access_method,
            index_row.indisvalid,
            index_row.indisunique,
            index_row.indnkeyatts,
            index_row.indnatts,
            ARRAY(
                SELECT attribute.attname::text
                FROM unnest(index_row.indkey) WITH ORDINALITY AS key(attnum, position)
                JOIN pg_catalog.pg_attribute attribute
                  ON attribute.attrelid = index_row.indrelid
                 AND attribute.attnum = key.attnum
                ORDER BY key.position
            ) AS columns,
            pg_catalog.pg_get_expr(index_row.indpred, index_row.indrelid) AS predicate
        INTO actual
        FROM pg_catalog.pg_class index_class
        JOIN pg_catalog.pg_namespace namespace_row
          ON namespace_row.oid = index_class.relnamespace
        JOIN pg_catalog.pg_index index_row ON index_row.indexrelid = index_class.oid
        JOIN pg_catalog.pg_class table_class ON table_class.oid = index_row.indrelid
        JOIN pg_catalog.pg_am access_method ON access_method.oid = index_class.relam
        WHERE namespace_row.nspname = 'public'
          AND index_class.relname = expected.index_name;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'fresh canonical index missing: %', expected.index_name;
        END IF;

        normalized_predicate := pg_catalog.regexp_replace(
            pg_catalog.replace(
                pg_catalog.replace(pg_catalog.replace(actual.predicate, '::text', ''), '(', ''),
                ')', ''
            ),
            '[[:space:]]', '', 'g'
        );
        IF actual.table_name <> expected.table_name
           OR actual.access_method <> 'btree'
           OR NOT actual.indisvalid
           OR actual.indisunique
           OR actual.indnkeyatts <> 2
           OR actual.indnatts <> 2
           OR actual.columns <> ARRAY['processing_lease_expires_at', 'id']
           OR normalized_predicate IS DISTINCT FROM
              expected.status_column || '=''PROCESSING''ANDprocessing_lease_expires_atISNOTNULL'
        THEN
            RAISE EXCEPTION 'fresh canonical index mismatch: %', expected.index_name;
        END IF;
    END LOOP;
END
$verify$;
