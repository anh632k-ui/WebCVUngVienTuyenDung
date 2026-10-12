-- R3C Phase 1 expand migration.
-- NOT AUTHORIZED FOR LOCAL/PRODUCTION EXECUTION.
-- Run only after separate deployment approval and the documented identity preflight.

\set ON_ERROR_STOP on

BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

DO $migration$
DECLARE
    target record;
    column_spec record;
    actual record;
BEGIN
    FOR target IN
        SELECT *
        FROM (VALUES
            ('resumes', 'revision', 'bigint', 'parsing_status'),
            ('job_descriptions', 'revision', 'bigint', 'parsing_status'),
            ('match_results', 'generation', 'bigint', 'status')
        ) AS expected(table_name, version_column, version_type, status_column)
    LOOP
        IF NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relname = target.table_name
              AND c.relkind = 'r'
        ) THEN
            RAISE EXCEPTION 'R3C Phase 1 requires public.% as an ordinary table',
                target.table_name;
        END IF;

        IF NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_attribute a
            JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relname = target.table_name
              AND a.attname = 'id'
              AND a.atttypid = 'uuid'::pg_catalog.regtype
              AND a.attnotnull
              AND a.attnum > 0
              AND NOT a.attisdropped
        ) OR NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_attribute a
            JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relname = target.table_name
              AND a.attname = target.version_column
              AND pg_catalog.format_type(a.atttypid, a.atttypmod) = target.version_type
              AND a.attnotnull
              AND a.attnum > 0
              AND NOT a.attisdropped
        ) OR NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_attribute a
            JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relname = target.table_name
              AND a.attname = target.status_column
              AND pg_catalog.format_type(a.atttypid, a.atttypmod) = 'character varying(20)'
              AND a.attnotnull
              AND a.attnum > 0
              AND NOT a.attisdropped
        ) THEN
            RAISE EXCEPTION 'R3C Phase 1 baseline metadata mismatch on public.%',
                target.table_name;
        END IF;
    END LOOP;

    FOR column_spec IN
        SELECT *
        FROM (VALUES
            ('resumes', 'processing_attempt_epoch', 'bigint', true, '0'),
            ('resumes', 'processing_owner_token', 'uuid', false, NULL),
            ('resumes', 'processing_activation_token', 'uuid', false, NULL),
            ('resumes', 'processing_heartbeat_at', 'timestamp with time zone', false, NULL),
            ('resumes', 'processing_lease_expires_at', 'timestamp with time zone', false, NULL),
            ('job_descriptions', 'processing_attempt_epoch', 'bigint', true, '0'),
            ('job_descriptions', 'processing_owner_token', 'uuid', false, NULL),
            ('job_descriptions', 'processing_activation_token', 'uuid', false, NULL),
            ('job_descriptions', 'processing_heartbeat_at', 'timestamp with time zone', false, NULL),
            ('job_descriptions', 'processing_lease_expires_at', 'timestamp with time zone', false, NULL),
            ('match_results', 'processing_attempt_epoch', 'bigint', true, '0'),
            ('match_results', 'processing_owner_token', 'uuid', false, NULL),
            ('match_results', 'processing_activation_token', 'uuid', false, NULL),
            ('match_results', 'processing_heartbeat_at', 'timestamp with time zone', false, NULL),
            ('match_results', 'processing_lease_expires_at', 'timestamp with time zone', false, NULL)
        ) AS expected(table_name, column_name, type_name, is_not_null, default_value)
    LOOP
        SELECT
            pg_catalog.format_type(a.atttypid, a.atttypmod) AS type_name,
            a.attnotnull AS is_not_null,
            pg_catalog.pg_get_expr(d.adbin, d.adrelid) AS default_value
        INTO actual
        FROM pg_catalog.pg_attribute a
        JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_catalog.pg_attrdef d
            ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE n.nspname = 'public'
          AND c.relname = column_spec.table_name
          AND a.attname = column_spec.column_name
          AND a.attnum > 0
          AND NOT a.attisdropped;

        IF FOUND THEN
            IF actual.type_name <> column_spec.type_name
               OR actual.is_not_null <> column_spec.is_not_null
               OR (
                    column_spec.default_value IS NULL
                    AND actual.default_value IS NOT NULL
               )
               OR (
                    column_spec.default_value IS NOT NULL
                    AND COALESCE(actual.default_value, '') NOT IN ('0', '''0''::bigint')
               ) THEN
                RAISE EXCEPTION
                    'R3C Phase 1 column mismatch: public.%.% has type %, not-null %, default %',
                    column_spec.table_name,
                    column_spec.column_name,
                    actual.type_name,
                    actual.is_not_null,
                    COALESCE(actual.default_value, '<none>');
            END IF;
        ELSE
            IF column_spec.column_name = 'processing_attempt_epoch' THEN
                EXECUTE pg_catalog.format(
                    'ALTER TABLE public.%I ADD COLUMN %I BIGINT NOT NULL DEFAULT 0',
                    column_spec.table_name,
                    column_spec.column_name
                );
            ELSIF column_spec.column_name IN (
                'processing_owner_token', 'processing_activation_token'
            ) THEN
                EXECUTE pg_catalog.format(
                    'ALTER TABLE public.%I ADD COLUMN %I UUID NULL',
                    column_spec.table_name,
                    column_spec.column_name
                );
            ELSE
                EXECUTE pg_catalog.format(
                    'ALTER TABLE public.%I ADD COLUMN %I TIMESTAMPTZ NULL',
                    column_spec.table_name,
                    column_spec.column_name
                );
            END IF;
        END IF;
    END LOOP;

    IF (
        SELECT count(*)
        FROM pg_catalog.pg_attribute a
        JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relname IN ('resumes', 'job_descriptions', 'match_results')
          AND a.attname IN (
              'processing_attempt_epoch',
              'processing_owner_token',
              'processing_activation_token',
              'processing_heartbeat_at',
              'processing_lease_expires_at'
          )
          AND a.attnum > 0
          AND NOT a.attisdropped
    ) <> 15 THEN
        RAISE EXCEPTION 'R3C Phase 1 postcondition failed: expected exactly 15 fencing columns';
    END IF;
END
$migration$;

COMMIT;
