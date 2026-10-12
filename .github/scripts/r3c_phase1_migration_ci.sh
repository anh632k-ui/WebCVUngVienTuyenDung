#!/usr/bin/env bash
set -euo pipefail

# Destructive mismatch cases below are restricted to disposable GitHub Actions databases.
readonly EXPECTED_DB='webcv_task_r3c_phase1_ci'
readonly EXPECTED_REPOSITORY='anh632k-ui/WebCVUngVienTuyenDung'
readonly PG_USER='webcv_ci'
readonly SCHEMA='docs/05_database/schema.sql'
readonly SEED='docs/05_database/skill_taxonomy_seed.sql'
readonly LEGACY_SEED='docs/05_database/migrations/tests/r3c_phase1_seed_legacy.sql'
readonly EXPAND='docs/05_database/migrations/r3c_phase1_expand.sql'
readonly INDEXES='docs/05_database/migrations/r3c_phase1_indexes.sql'
readonly VERIFY='docs/05_database/migrations/tests/r3c_phase1_verify.sql'

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

bootstrap_database() {
  local database="$1"
  psql_db "$database" < "$SCHEMA"
  psql_db "$database" < "$SEED"
  assert_server_identity "$database"
}

create_case_database() {
  local database="$1"
  docker exec "$POSTGRES_CONTAINER_ID" createdb -U "$PG_USER" "$database"
  bootstrap_database "$database"
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

# The service database is empty at this point. Verify container/database/version first,
# then bootstrap; the complete 10-table/42-skill identity gate runs before migration DDL.
[[ "$(scalar "$EXPECTED_DB" 'SELECT current_database()')" == "$EXPECTED_DB" ]] \
  || fail 'initial database identity mismatch'
[[ "$(scalar "$EXPECTED_DB" "SELECT current_setting('server_version_num')::integer / 10000")" == '18' ]] \
  || fail 'initial PostgreSQL major is not 18'

bootstrap_database "$EXPECTED_DB"
psql_db "$EXPECTED_DB" -c \
  "SELECT version() AS exact_postgresql_version, current_database() AS database_identity"
psql_db "$EXPECTED_DB" < "$LEGACY_SEED"

before_indexes="$(scalar "$EXPECTED_DB" "SELECT string_agg(indexname, ',' ORDER BY indexname) FROM pg_indexes WHERE schemaname='public'")"
psql_db "$EXPECTED_DB" < "$EXPAND"
psql_db "$EXPECTED_DB" < "$INDEXES"
psql_db "$EXPECTED_DB" < "$VERIFY"

# Explicit rerun: correct objects are accepted without duplicate objects or row mutation.
psql_db "$EXPECTED_DB" < "$EXPAND"
psql_db "$EXPECTED_DB" < "$INDEXES"
psql_db "$EXPECTED_DB" < "$VERIFY"

after_existing_indexes="$(scalar "$EXPECTED_DB" "SELECT string_agg(indexname, ',' ORDER BY indexname) FROM pg_indexes WHERE schemaname='public' AND indexname NOT IN ('idx_resumes_processing_lease','idx_jobs_processing_lease','idx_matches_processing_lease')")"
[[ "$before_indexes" == "$after_existing_indexes" ]] || fail 'an existing index was lost or mutated'

create_case_database 'r3c_p1_case_wrong_type'
psql_db 'r3c_p1_case_wrong_type' -c \
  'ALTER TABLE public.resumes ADD COLUMN processing_owner_token text'
expect_failure 'wrong existing column type' 'r3c_p1_case_wrong_type' "$EXPAND"

create_case_database 'r3c_p1_case_missing_table'
psql_db 'r3c_p1_case_missing_table' -c 'DROP TABLE public.match_results'
expect_failure 'missing target table' 'r3c_p1_case_missing_table' "$EXPAND"

create_case_database 'r3c_p1_case_index_conflict'
psql_db 'r3c_p1_case_index_conflict' < "$EXPAND"
psql_db 'r3c_p1_case_index_conflict' -c \
  'CREATE INDEX idx_resumes_processing_lease ON public.resumes (id)'
expect_failure 'occupied index name' 'r3c_p1_case_index_conflict' "$INDEXES"

create_case_database 'r3c_p1_case_invalid_index'
psql_db 'r3c_p1_case_invalid_index' < "$EXPAND"
psql_db 'r3c_p1_case_invalid_index' -c \
  "INSERT INTO users (id,email,password_hash,full_name,role) VALUES ('50000000-0000-0000-0000-000000000001','invalid-index@example.invalid','x','Invalid Index Fixture','CANDIDATE'); INSERT INTO resumes (id,owner_user_id,file_name,storage_key,file_size,mime_type,create_request_fingerprint) VALUES ('60000000-0000-0000-0000-000000000001','50000000-0000-0000-0000-000000000001','a.pdf','r3c/invalid/a',1,'application/pdf',repeat('e',64)), ('60000000-0000-0000-0000-000000000002','50000000-0000-0000-0000-000000000001','b.pdf','r3c/invalid/b',1,'application/pdf',repeat('f',64))"
if psql_db 'r3c_p1_case_invalid_index' -c \
  'CREATE UNIQUE INDEX CONCURRENTLY idx_resumes_processing_lease ON public.resumes (processing_attempt_epoch)'
then
  fail 'invalid-index fixture unexpectedly built a unique index over duplicate epochs'
fi
expect_failure 'invalid existing index' 'r3c_p1_case_invalid_index' "$INDEXES"

for case_database in \
  r3c_p1_case_wrong_type \
  r3c_p1_case_missing_table \
  r3c_p1_case_index_conflict \
  r3c_p1_case_invalid_index
do
  docker exec "$POSTGRES_CONTAINER_ID" dropdb -U "$PG_USER" --force "$case_database"
done

printf 'R3C Phase 1 PostgreSQL migration scenarios: PASS\n'
