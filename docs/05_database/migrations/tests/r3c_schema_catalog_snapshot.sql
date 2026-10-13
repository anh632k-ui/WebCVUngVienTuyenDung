-- Stable catalog snapshot used to compare migrated legacy and fresh canonical databases.
-- Output is intentionally one JSONB object per line so diff reports the exact drift.
\set ON_ERROR_STOP on
\pset tuples_only on
\pset format unaligned

WITH metadata AS (
    SELECT
        'table'::text AS object_type,
        table_class.relname::text AS object_name,
        jsonb_build_object('kind', table_class.relkind) AS definition
    FROM pg_catalog.pg_class table_class
    JOIN pg_catalog.pg_namespace namespace_row
      ON namespace_row.oid = table_class.relnamespace
    WHERE namespace_row.nspname = 'public' AND table_class.relkind = 'r'

    UNION ALL

    SELECT
        'column',
        table_class.relname || '.' || attribute.attname,
        jsonb_build_object(
            'type', pg_catalog.format_type(attribute.atttypid, attribute.atttypmod),
            'not_null', attribute.attnotnull,
            'default', pg_catalog.regexp_replace(
                COALESCE(pg_catalog.pg_get_expr(default_row.adbin, default_row.adrelid), ''),
                '[[:space:]]+', ' ', 'g'
            )
        )
    FROM pg_catalog.pg_attribute attribute
    JOIN pg_catalog.pg_class table_class ON table_class.oid = attribute.attrelid
    JOIN pg_catalog.pg_namespace namespace_row
      ON namespace_row.oid = table_class.relnamespace
    LEFT JOIN pg_catalog.pg_attrdef default_row
      ON default_row.adrelid = attribute.attrelid
     AND default_row.adnum = attribute.attnum
    WHERE namespace_row.nspname = 'public'
      AND table_class.relkind = 'r'
      AND attribute.attnum > 0
      AND NOT attribute.attisdropped

    UNION ALL

    SELECT
        CASE constraint_row.contype
            WHEN 'p' THEN 'primary_key'
            WHEN 'f' THEN 'foreign_key'
            WHEN 'u' THEN 'unique_constraint'
            WHEN 'c' THEN 'check_constraint'
        END,
        table_class.relname || '.' || constraint_row.conname,
        jsonb_build_object(
            'definition', pg_catalog.regexp_replace(
                pg_catalog.pg_get_constraintdef(constraint_row.oid, true),
                '[[:space:]]+', ' ', 'g'
            ),
            'deferrable', constraint_row.condeferrable,
            'deferred', constraint_row.condeferred,
            'validated', constraint_row.convalidated
        )
    FROM pg_catalog.pg_constraint constraint_row
    JOIN pg_catalog.pg_class table_class ON table_class.oid = constraint_row.conrelid
    JOIN pg_catalog.pg_namespace namespace_row
      ON namespace_row.oid = table_class.relnamespace
    WHERE namespace_row.nspname = 'public'
      AND constraint_row.contype IN ('p', 'f', 'u', 'c')

    UNION ALL

    SELECT
        'index',
        index_class.relname,
        jsonb_build_object(
            'table', table_class.relname,
            'method', access_method.amname,
            'keys', ARRAY(
                SELECT pg_catalog.pg_get_indexdef(index_row.indexrelid, key_position, true)
                FROM generate_series(1, index_row.indnkeyatts) AS key_position
                ORDER BY key_position
            ),
            'all_attributes', ARRAY(
                SELECT pg_catalog.pg_get_indexdef(index_row.indexrelid, attribute_position, true)
                FROM generate_series(1, index_row.indnatts) AS attribute_position
                ORDER BY attribute_position
            ),
            'key_count', index_row.indnkeyatts,
            'attribute_count', index_row.indnatts,
            'predicate', pg_catalog.regexp_replace(
                COALESCE(pg_catalog.pg_get_expr(index_row.indpred, index_row.indrelid), ''),
                '[[:space:]]+', ' ', 'g'
            ),
            'unique', index_row.indisunique,
            'primary', index_row.indisprimary,
            'valid', index_row.indisvalid,
            'ready', index_row.indisready
        )
    FROM pg_catalog.pg_index index_row
    JOIN pg_catalog.pg_class index_class ON index_class.oid = index_row.indexrelid
    JOIN pg_catalog.pg_class table_class ON table_class.oid = index_row.indrelid
    JOIN pg_catalog.pg_namespace namespace_row
      ON namespace_row.oid = index_class.relnamespace
    JOIN pg_catalog.pg_am access_method ON access_method.oid = index_class.relam
    WHERE namespace_row.nspname = 'public'
)
SELECT jsonb_build_object(
    'object_type', object_type,
    'object_name', object_name,
    'definition', definition
)::text
FROM metadata
ORDER BY object_type, object_name, definition::text;
