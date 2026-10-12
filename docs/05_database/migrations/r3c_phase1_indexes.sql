-- R3C Phase 1 concurrent index migration.
-- NOT AUTHORIZED FOR LOCAL/PRODUCTION EXECUTION.
-- psql is required. This file must not run inside a transaction block.

\set ON_ERROR_STOP on

SET lock_timeout = '5s';
SET statement_timeout = '10min';

DO $preflight$
DECLARE
    expected record;
    index_metadata record;
    normalized_predicate text;
BEGIN
    FOR expected IN
        SELECT *
        FROM (VALUES
            ('idx_resumes_processing_lease', 'resumes', 'parsing_status'),
            ('idx_jobs_processing_lease', 'job_descriptions', 'parsing_status'),
            ('idx_matches_processing_lease', 'match_results', 'status')
        ) AS specification(index_name, table_name, status_column)
    LOOP
        IF NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_attribute a
            JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relname = expected.table_name
              AND a.attname = 'processing_lease_expires_at'
              AND pg_catalog.format_type(a.atttypid, a.atttypmod) =
                  'timestamp with time zone'
              AND a.attnum > 0
              AND NOT a.attisdropped
        ) THEN
            RAISE EXCEPTION 'R3C Phase 1 index preflight missing public.%.processing_lease_expires_at',
                expected.table_name;
        END IF;

        SELECT
            table_class.relname AS table_name,
            index_class.relkind AS object_kind,
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
        INTO index_metadata
        FROM pg_catalog.pg_class index_class
        JOIN pg_catalog.pg_namespace index_namespace
          ON index_namespace.oid = index_class.relnamespace
        LEFT JOIN pg_catalog.pg_index index_row ON index_row.indexrelid = index_class.oid
        LEFT JOIN pg_catalog.pg_class table_class ON table_class.oid = index_row.indrelid
        LEFT JOIN pg_catalog.pg_am access_method ON access_method.oid = index_class.relam
        WHERE index_namespace.nspname = 'public'
          AND index_class.relname = expected.index_name;

        IF FOUND THEN
            normalized_predicate := pg_catalog.regexp_replace(
                pg_catalog.replace(
                    pg_catalog.replace(
                        pg_catalog.replace(index_metadata.predicate, '::text', ''),
                        '(', ''
                    ),
                    ')', ''
                ),
                '[[:space:]]',
                '',
                'g'
            );

            IF index_metadata.object_kind <> 'i'
               OR index_metadata.table_name <> expected.table_name
               OR index_metadata.access_method <> 'btree'
               OR NOT index_metadata.indisvalid
               OR index_metadata.indisunique
               OR index_metadata.indnkeyatts <> 2
               OR index_metadata.indnatts <> 2
               OR index_metadata.columns <> ARRAY['processing_lease_expires_at', 'id']
               OR normalized_predicate IS DISTINCT FROM
                  expected.status_column || '=''PROCESSING''ANDprocessing_lease_expires_atISNOTNULL'
            THEN
                RAISE EXCEPTION
                    'R3C Phase 1 index conflict or invalid index: public.%',
                    expected.index_name;
            END IF;
        END IF;
    END LOOP;
END
$preflight$;

SELECT NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname = 'idx_resumes_processing_lease'
) AS create_resumes_index \gset
\if :create_resumes_index
CREATE INDEX CONCURRENTLY idx_resumes_processing_lease
    ON public.resumes (processing_lease_expires_at, id)
    WHERE parsing_status = 'PROCESSING' AND processing_lease_expires_at IS NOT NULL;
\endif

SELECT NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname = 'idx_jobs_processing_lease'
) AS create_jobs_index \gset
\if :create_jobs_index
CREATE INDEX CONCURRENTLY idx_jobs_processing_lease
    ON public.job_descriptions (processing_lease_expires_at, id)
    WHERE parsing_status = 'PROCESSING' AND processing_lease_expires_at IS NOT NULL;
\endif

SELECT NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname = 'idx_matches_processing_lease'
) AS create_matches_index \gset
\if :create_matches_index
CREATE INDEX CONCURRENTLY idx_matches_processing_lease
    ON public.match_results (processing_lease_expires_at, id)
    WHERE status = 'PROCESSING' AND processing_lease_expires_at IS NOT NULL;
\endif

DO $postflight$
DECLARE
    expected record;
    index_metadata record;
    normalized_predicate text;
BEGIN
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
            index_class.relkind AS object_kind,
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
        INTO index_metadata
        FROM pg_catalog.pg_class index_class
        JOIN pg_catalog.pg_namespace index_namespace
          ON index_namespace.oid = index_class.relnamespace
        LEFT JOIN pg_catalog.pg_index index_row ON index_row.indexrelid = index_class.oid
        LEFT JOIN pg_catalog.pg_class table_class ON table_class.oid = index_row.indrelid
        LEFT JOIN pg_catalog.pg_am access_method ON access_method.oid = index_class.relam
        WHERE index_namespace.nspname = 'public'
          AND index_class.relname = expected.index_name;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'R3C Phase 1 index postcondition missing public.%',
                expected.index_name;
        END IF;

        normalized_predicate := pg_catalog.regexp_replace(
            pg_catalog.replace(
                pg_catalog.replace(
                    pg_catalog.replace(index_metadata.predicate, '::text', ''),
                    '(', ''
                ),
                ')', ''
            ),
            '[[:space:]]',
            '',
            'g'
        );

        IF index_metadata.object_kind <> 'i'
           OR index_metadata.table_name <> expected.table_name
           OR index_metadata.access_method <> 'btree'
           OR NOT index_metadata.indisvalid
           OR index_metadata.indisunique
           OR index_metadata.indnkeyatts <> 2
           OR index_metadata.indnatts <> 2
           OR index_metadata.columns <> ARRAY['processing_lease_expires_at', 'id']
           OR normalized_predicate IS DISTINCT FROM
              expected.status_column || '=''PROCESSING''ANDprocessing_lease_expires_atISNOTNULL'
        THEN
            RAISE EXCEPTION 'R3C Phase 1 index postcondition failed for public.%',
                expected.index_name;
        END IF;
    END LOOP;
END
$postflight$;

RESET statement_timeout;
RESET lock_timeout;
