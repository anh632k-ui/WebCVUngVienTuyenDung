#!/usr/bin/env bash
set -euo pipefail

# Destructive mismatch cases below are restricted to disposable GitHub Actions databases.
readonly EXPECTED_DB='webcv_task_r3c_phase1_ci'
readonly EXPECTED_REPOSITORY='anh632k-ui/WebCVUngVienTuyenDung'
readonly PG_USER='webcv_ci'
readonly SCHEMA='docs/05_database/schema.sql'
readonly LEGACY_SCHEMA='docs/05_database/migrations/tests/r3c_pre_fencing_schema.sql'
readonly SEED='docs/05_database/skill_taxonomy_seed.sql'
readonly LEGACY_SEED='docs/05_database/migrations/tests/r3c_phase1_seed_legacy.sql'
readonly EXPAND='docs/05_database/migrations/r3c_phase1_expand.sql'
readonly INDEXES='docs/05_database/migrations/r3c_phase1_indexes.sql'
readonly VERIFY='docs/05_database/migrations/tests/r3c_phase1_verify.sql'
readonly FRESH_VERIFY='docs/05_database/migrations/tests/r3c_fresh_canonical_verify.sql'
readonly CATALOG_SNAPSHOT='docs/05_database/migrations/tests/r3c_schema_catalog_snapshot.sql'
readonly LEGACY_DB='r3c_p1_case_legacy'

fail() {
  printf 'R3C Phase 1 CI refusal: %s\n' "$1" >&2
  exit 1
}

[[ "${GITHUB_ACTIONS:-}" == 'true' ]] || fail 'not running in GitHub Actions'
[[ "${RUNNER_ENVIRONMENT:-}" == 'github-hosted' ]] || fail 'runner is not GitHub-hosted'
[[ "${CI:-}" == 'true' ]] || fail 'CI marker is missing'
[[ "${GITHUB_REPOSITORY:-}" == "$EXPECTED_REPOSITORY" ]] || fail 'unexpected repository identity'
[[ "${R3C_DATABASE_NAME:-}" == "$EXPECTED_DB" ]] || fail 'unexpected database name'
[[ "${R3C_DATABASE_HOST:-}" == '127.0.0.1' ]] || fail 'database host is not loopback'
[[ "${R3C_DATABASE_PORT:-}" == '55432' ]] || fail 'database port is not the isolated CI port'
[[ -n "${POSTGRES_CONTAINER_ID:-}" ]] || fail 'PostgreSQL service container is missing'
[[ "$(docker exec "$POSTGRES_CONTAINER_ID" printenv POSTGRES_DB)" == "$EXPECTED_DB" ]] \
  || fail 'service container database identity mismatch'

psql_db() {
  local database="$1"
  shift
  docker exec -i "$POSTGRES_CONTAINER_ID" psql \
    -X -U "$PG_USER" -d "$database" -v ON_ERROR_STOP=1 "$@"
}

scalar() {
  local database="$1"
  local query="$2"
  docker exec "$POSTGRES_CONTAINER_ID" psql \
    -X -U "$PG_USER" -d "$database" -Atqc "$query"
}

assert_server_identity() {
  local database="$1"
  [[ "$database" == "$EXPECTED_DB" || "$database" == r3c_p1_case_* ]] \
    || fail "database $database is outside the disposable allowlist"
  [[ "$(scalar "$database" 'SELECT current_database()')" == "$database" ]] \
    || fail "current_database mismatch for $database"
  [[ "$(scalar "$database" "SELECT current_setting('server_version_num')::integer / 10000")" == '18' ]] \
    || fail "PostgreSQL major is not 18 for $database"
  [[ "$(scalar "$database" "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'")" == '10' ]] \
    || fail "canonical table count mismatch for $database"
  [[ "$(scalar "$database" 'SELECT count(*) FROM public.skills')" == '42' ]] \
    || fail "taxonomy count mismatch for $database"
}

bootstrap_fresh_database() {
  local database="$1"
  psql_db "$database" < "$SCHEMA"
  psql_db "$database" < "$SEED"
  assert_server_identity "$database"
}

bootstrap_legacy_database() {
  local database="$1"
  psql_db "$database" < "$LEGACY_SCHEMA"
  psql_db "$database" < "$SEED"
  assert_server_identity "$database"
}

create_legacy_case_database() {
  local database="$1"
  docker exec "$POSTGRES_CONTAINER_ID" createdb -U "$PG_USER" "$database"
  bootstrap_legacy_database "$database"
}

expect_failure() {
  local label="$1"
  local database="$2"
  local file="$3"
  if psql_db "$database" < "$file"; then
    fail "$label unexpectedly succeeded"
  fi
  printf 'Expected refusal observed: %s\n' "$label"
}

# Refuse a fixture that has silently drifted into the post-fencing shape. Its source is the
# canonical schema at a38ec82d81e915581766d0ae6c38c717696a8724, copied into the repository
# so shallow CI checkouts do not depend on historical objects.
if grep -Eq 'processing_(attempt_epoch|owner_token|activation_token|heartbeat_at|lease_expires_at)|idx_(resumes|jobs|matches)_processing_lease' "$LEGACY_SCHEMA"; then
  fail 'legacy fixture contains fencing columns or lease indexes'
fi

# Path B: the service database is a fresh canonical bootstrap. Backend regression later uses it.
[[ "$(scalar "$EXPECTED_DB" 'SELECT current_database()')" == "$EXPECTED_DB" ]] \
  || fail 'initial database identity mismatch'
[[ "$(scalar "$EXPECTED_DB" "SELECT current_setting('server_version_num')::integer / 10000")" == '18' ]] \
  || fail 'initial PostgreSQL major is not 18'

bootstrap_fresh_database "$EXPECTED_DB"
psql_db "$EXPECTED_DB" -c \
  "SELECT version() AS exact_postgresql_version, current_database() AS database_identity"
psql_db "$EXPECTED_DB" < "$FRESH_VERIFY"

# Path A: bootstrap the immutable pre-fencing fixture, preserve legacy PROCESSING rows,
# then execute and rerun the reviewed Phase 1 migrations.
create_legacy_case_database "$LEGACY_DB"
psql_db "$LEGACY_DB" -c \
  "SELECT version() AS exact_postgresql_version, current_database() AS database_identity"
psql_db "$LEGACY_DB" < "$LEGACY_SEED"

before_indexes="$(scalar "$LEGACY_DB" "SELECT string_agg(indexname, ',' ORDER BY indexname) FROM pg_indexes WHERE schemaname='public'")"
psql_db "$LEGACY_DB" < "$EXPAND"
psql_db "$LEGACY_DB" < "$INDEXES"
psql_db "$LEGACY_DB" < "$VERIFY"

# Explicit rerun: correct objects are accepted without duplicate objects or row mutation.
psql_db "$LEGACY_DB" < "$EXPAND"
psql_db "$LEGACY_DB" < "$INDEXES"
psql_db "$LEGACY_DB" < "$VERIFY"

after_existing_indexes="$(scalar "$LEGACY_DB" "SELECT string_agg(indexname, ',' ORDER BY indexname) FROM pg_indexes WHERE schemaname='public' AND indexname NOT IN ('idx_resumes_processing_lease','idx_jobs_processing_lease','idx_matches_processing_lease')")"
[[ "$before_indexes" == "$after_existing_indexes" ]] || fail 'an existing index was lost or mutated'

create_legacy_case_database 'r3c_p1_case_wrong_type'
psql_db 'r3c_p1_case_wrong_type' -c \
  'ALTER TABLE public.resumes ADD COLUMN processing_owner_token text'
expect_failure 'wrong existing column type' 'r3c_p1_case_wrong_type' "$EXPAND"

create_legacy_case_database 'r3c_p1_case_missing_table'
psql_db 'r3c_p1_case_missing_table' -c 'DROP TABLE public.match_results'
expect_failure 'missing target table' 'r3c_p1_case_missing_table' "$EXPAND"

create_legacy_case_database 'r3c_p1_case_index_conflict'
psql_db 'r3c_p1_case_index_conflict' < "$EXPAND"
psql_db 'r3c_p1_case_index_conflict' -c \
  'CREATE INDEX idx_resumes_processing_lease ON public.resumes (id)'
expect_failure 'occupied index name' 'r3c_p1_case_index_conflict' "$INDEXES"

create_legacy_case_database 'r3c_p1_case_missing_predicate'
psql_db 'r3c_p1_case_missing_predicate' < "$EXPAND"
psql_db 'r3c_p1_case_missing_predicate' < "$LEGACY_SEED"
psql_db 'r3c_p1_case_missing_predicate' -c \
  'CREATE INDEX idx_resumes_processing_lease ON public.resumes (processing_lease_expires_at, id)'
missing_predicate_before="$(scalar 'r3c_p1_case_missing_predicate' "SELECT pg_get_indexdef('public.idx_resumes_processing_lease'::regclass)")"
expect_failure 'same-shape index without partial predicate' \
  'r3c_p1_case_missing_predicate' "$INDEXES"
expect_failure 'final verification of index without partial predicate' \
  'r3c_p1_case_missing_predicate' "$VERIFY"
missing_predicate_after="$(scalar 'r3c_p1_case_missing_predicate' "SELECT pg_get_indexdef('public.idx_resumes_processing_lease'::regclass)")"
[[ "$missing_predicate_before" == "$missing_predicate_after" ]] \
  || fail 'rejected index without partial predicate was mutated or replaced'
[[ "$(scalar 'r3c_p1_case_missing_predicate' "SELECT count(*) FROM pg_index WHERE indexrelid='public.idx_resumes_processing_lease'::regclass AND indisvalid")" == '1' ]] \
  || fail 'rejected index without partial predicate was deleted or invalidated'

create_legacy_case_database 'r3c_p1_case_invalid_index'
psql_db 'r3c_p1_case_invalid_index' < "$EXPAND"
psql_db 'r3c_p1_case_invalid_index' -c \
  "INSERT INTO users (id,email,password_hash,full_name,role) VALUES ('50000000-0000-0000-0000-000000000001','invalid-index@example.invalid','x','Invalid Index Fixture','CANDIDATE'); INSERT INTO resumes (id,owner_user_id,file_name,storage_key,file_size,mime_type,create_request_fingerprint) VALUES ('60000000-0000-0000-0000-000000000001','50000000-0000-0000-0000-000000000001','a.pdf','r3c/invalid/a',1,'application/pdf',repeat('e',64)), ('60000000-0000-0000-0000-000000000002','50000000-0000-0000-0000-000000000001','b.pdf','r3c/invalid/b',1,'application/pdf',repeat('f',64))"
if psql_db 'r3c_p1_case_invalid_index' -c \
  'CREATE UNIQUE INDEX CONCURRENTLY idx_resumes_processing_lease ON public.resumes (processing_attempt_epoch)'
then
  fail 'invalid-index fixture unexpectedly built a unique index over duplicate epochs'
fi
expect_failure 'invalid existing index' 'r3c_p1_case_invalid_index' "$INDEXES"

# Acceptance gate: catalog-equivalent public tables, columns, types, nullability,
# defaults, PK/FK/UQ/CHECK constraints, and complete index metadata.
readonly SNAPSHOT_DIR="$(mktemp -d)"
psql_db "$LEGACY_DB" < "$CATALOG_SNAPSHOT" > "$SNAPSHOT_DIR/legacy-migrated.catalog"
psql_db "$EXPECTED_DB" < "$CATALOG_SNAPSHOT" > "$SNAPSHOT_DIR/fresh-canonical.catalog"
if ! diff -u \
  "$SNAPSHOT_DIR/legacy-migrated.catalog" \
  "$SNAPSHOT_DIR/fresh-canonical.catalog"
then
  fail 'migrated legacy and fresh canonical schemas are not catalog-equivalent'
fi
printf 'R3C schema equivalence (%s <-> %s): PASS\n' "$LEGACY_DB" "$EXPECTED_DB"

for case_database in \
  r3c_p1_case_wrong_type \
  r3c_p1_case_missing_table \
  r3c_p1_case_index_conflict \
  r3c_p1_case_missing_predicate \
  r3c_p1_case_invalid_index
do
  docker exec "$POSTGRES_CONTAINER_ID" dropdb -U "$PG_USER" --force "$case_database"
done

printf 'R3C Phase 1 PostgreSQL migration scenarios: PASS\n'
