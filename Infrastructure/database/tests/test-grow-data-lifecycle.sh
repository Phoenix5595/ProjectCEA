#!/usr/bin/env bash
set -euo pipefail

# Grow data lifecycle proof harness.
#
# Proves on a disposable PostgreSQL database (never `cea_sensors`, never
# `sj_cea`):
#   * the idempotent grow_data_lifecycle.sql migration (applied twice with
#     ON_ERROR_STOP),
#   * plain-SQL schema invariants (inventory, six automation_state indexes,
#     no cold store or source triggers, grow_lifecycle closure rules),
#   * the checked-in source-schema preflight plus drift probes (unknown
#     column, missing column, column order, type, core-missing,
#     unavailable-present, unique-constraint and primary-key drift), via
#     grow-archive-service/grow_archive_service/schema.py,
#   * the real loopback Calendar/Archive API membership proof, including
#     concurrent grow overlap fencing, scope corrections, explicit close and reopen.
#
# Reuses the guarded disposable-database harness from
# test-monitoring-read-models.sh (monitoring_test_ namespace). It never forks
# a weaker harness, rejects inherited connection environment variables and
# rejects inherited archive/remote endpoint configuration, so no case can
# reach production or an external network destination.

readonly GROW_HARNESS_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly GROW_MIGRATION_SQL="$GROW_HARNESS_DIRECTORY/../grow_data_lifecycle.sql"
readonly GROW_EXPIRY_CURSOR_SQL="$GROW_HARNESS_DIRECTORY/../grow_data_lifecycle_expiry_cursor.sql"
readonly GROW_FIXTURE_SQL="$GROW_HARNESS_DIRECTORY/fixtures/grow_data_lifecycle_fixture.sql"
readonly GROW_SCHEMA_CASE_SQL="$GROW_HARNESS_DIRECTORY/cases/grow-data-lifecycle-schema.sql"
readonly GROW_SCHEMA_CASE_PY="$GROW_HARNESS_DIRECTORY/grow_data_lifecycle_schema_case.py"
readonly GROW_MONITORING_HARNESS="$GROW_HARNESS_DIRECTORY/test-monitoring-read-models.sh"
readonly GROW_ARCHIVE_VENV_PY="$GROW_HARNESS_DIRECTORY/../../grow-archive-service/.venv/bin/python"
readonly GROW_MEMBERSHIP_SMOKE="$GROW_HARNESS_DIRECTORY/../../grow-archive-service/tests/smoke_lifecycle.py"
readonly GROW_CALENDAR_SYNC_SMOKE="$GROW_HARNESS_DIRECTORY/../../grow-archive-service/tests/smoke_calendar_sync.py"
readonly GROW_HISTORY_AVAILABILITY_SMOKE="$GROW_HARNESS_DIRECTORY/../../grow-archive-service/tests/smoke_history_availability.py"
readonly GROW_LOSSLESS_SMOKE="$GROW_HARNESS_DIRECTORY/../../grow-archive-service/tests/smoke_lossless.py"
readonly GROW_COMPRESSED_SOURCE_SMOKE="$GROW_HARNESS_DIRECTORY/../../grow-archive-service/tests/smoke_compressed_source.py"
readonly GROW_DELETION_FENCE_SMOKE="$GROW_HARNESS_DIRECTORY/../../grow-archive-service/tests/smoke_deletion_fence.py"
readonly GROW_FILES_EXPORT_SMOKE="$GROW_HARNESS_DIRECTORY/../../grow-archive-service/tests/smoke_files_export.py"
readonly GROW_ANNUAL_CORRECTION_SMOKE="$GROW_HARNESS_DIRECTORY/../../grow-archive-service/tests/smoke_annual_correction.py"
readonly GROW_DERIVED_REFRESH_SMOKE="$GROW_HARNESS_DIRECTORY/../../grow-archive-service/tests/smoke_derived_refresh.py"
readonly GROW_ARCHIVE_API_SMOKE="$GROW_HARNESS_DIRECTORY/../../grow-archive-service/tests/smoke_archive_api.py"
readonly GROW_NATIVE_READERS_SMOKE="$GROW_HARNESS_DIRECTORY/../../grow-archive-service/tests/smoke_native_backend_readers.py"
readonly GROW_ENABLED_LIFECYCLE_SMOKE="$GROW_HARNESS_DIRECTORY/../../grow-archive-service/tests/smoke_enabled_lifecycle.py"
readonly GROW_RENAME_CASE_SH="$GROW_HARNESS_DIRECTORY/grow_data_lifecycle_rename_case.sh"
readonly GROW_RENAME_CASE_PY="$GROW_HARNESS_DIRECTORY/grow_data_lifecycle_rename_case.py"
readonly GROW_COMPRESSION_POLICY_SQL="$GROW_HARNESS_DIRECTORY/../grow_data_source_compression.sql"
readonly GROW_COMPRESSION_POLICY_CASE_PY="$GROW_HARNESS_DIRECTORY/grow_data_source_compression_case.py"
readonly GROW_LIFECYCLE_HARNESS_LOCK='/tmp/grow-lifecycle-harness.lock'

grow_fail() {
  printf 'grow data lifecycle harness: %s\n' "$1" >&2
  return 1
}

implemented_cases() {
  printf '%s\n' schema membership calendar-sync history-availability lossless compressed-source source-compression-policy deletion-fence rename files-export annual-correction archive-api derived-repair native-readers enabled-lifecycle
}

planned_cases() {
  printf '%s\n' grow-rollover veg-rollover reader-isolation
}

is_known_case() {
  local case_name="$1"
  local candidate
  while IFS= read -r candidate; do
    [[ "$candidate" == "$case_name" ]] && return 0
  done < <(implemented_cases; planned_cases)
  return 1
}

reject_inherited_environment() {
  local environment_line
  local variable_name
  local upper_name
  while IFS= read -r environment_line; do
    variable_name="${environment_line%%=*}"
    upper_name="${variable_name^^}"
    case "$upper_name" in
      PG* | POSTGRES_* | REDIS_* | DATABASE_URL | MONITORING_TEST_DATABASE_URL | BASE_URL)
        grow_fail "refusing inherited database environment variable: $variable_name"
        return 1
        ;;
      ARCHIVE_* | WEBDAV_* | NEXTCLOUD_* | CALDAV_* | CEA_API_KEY* | CEA_ENCRYPTION_KEY | CALENDAR_SYNC_ENCRYPTION_KEY)
        grow_fail "refusing inherited archive/remote endpoint environment variable: $variable_name"
        return 1
        ;;
      HTTP_PROXY | HTTPS_PROXY | ALL_PROXY | HTTP_PROXY_URL | HTTPS_PROXY_URL)
        grow_fail "refusing inherited outbound proxy environment variable: $variable_name"
        return 1
        ;;
    esac
  done < <(env)
}

resolve_grow_python() {
  if [[ -x "$GROW_ARCHIVE_VENV_PY" ]] && "$GROW_ARCHIVE_VENV_PY" -c 'import asyncpg' >/dev/null 2>&1; then
    printf '%s\n' "$GROW_ARCHIVE_VENV_PY"
    return 0
  fi
  if command -v python3 >/dev/null 2>&1 && python3 -c 'import asyncpg' >/dev/null 2>&1; then
    command -v python3
    return 0
  fi
  grow_fail "no interpreter with asyncpg: install it into $GROW_ARCHIVE_VENV_PY (owned worktree venv)"
}

assert_case_files_present() {
  local path
  for path in "$GROW_MIGRATION_SQL" "$GROW_FIXTURE_SQL" "$GROW_SCHEMA_CASE_SQL" \
    "$GROW_RENAME_CASE_SH" "$GROW_RENAME_CASE_PY" \
    "$GROW_EXPIRY_CURSOR_SQL" \
    "$GROW_COMPRESSION_POLICY_SQL" "$GROW_COMPRESSION_POLICY_CASE_PY" \
    "$GROW_SCHEMA_CASE_PY" "$GROW_MEMBERSHIP_SMOKE" "$GROW_CALENDAR_SYNC_SMOKE" \
    "$GROW_HISTORY_AVAILABILITY_SMOKE" "$GROW_LOSSLESS_SMOKE" \
    "$GROW_COMPRESSED_SOURCE_SMOKE" "$GROW_DELETION_FENCE_SMOKE" \
    "$GROW_FILES_EXPORT_SMOKE" "$GROW_DERIVED_REFRESH_SMOKE" \
    "$GROW_ANNUAL_CORRECTION_SMOKE" \
    "$GROW_ARCHIVE_API_SMOKE" "$GROW_NATIVE_READERS_SMOKE" \
    "$GROW_ENABLED_LIFECYCLE_SMOKE"; do
    [[ -f "$path" ]] || {
      grow_fail "required file is missing: $path"
      return 1
    }
  done
}

grow_run_internal_case() {
  local case_name="$1"
  local database_url="${MONITORING_TEST_DATABASE_URL:-}"

  [[ "${GROW_HARNESS_INTERNAL:-}" == '1' ]] || {
    grow_fail 'internal case runner may only be called by with_monitoring_test_db'
    return 1
  }
  [[ "${MONITORING_HARNESS_INTERNAL:-}" == '1' ]] || {
    grow_fail 'internal case runner is missing the disposable-harness marker'
    return 1
  }
  [[ -n "$database_url" && "$database_url" == "${MONITORING_TEST_DATABASE_URL:-}" ]] || {
    grow_fail 'internal case runner did not receive the exact disposable database URL'
    return 1
  }
  declare -F assert_safe_database_url >/dev/null || source "$GROW_MONITORING_HARNESS"
  assert_safe_database_url "$database_url" || return 1

  printf 'running grow data lifecycle case: %s\n' "$case_name"

  psql -X --set=ON_ERROR_STOP=1 --file="$GROW_MIGRATION_SQL" "$database_url" >/dev/null
  printf 'applied grow_data_lifecycle.sql (pass 1)\n'
  psql -X --set=ON_ERROR_STOP=1 --file="$GROW_MIGRATION_SQL" "$database_url" >/dev/null
  printf 're-applied grow_data_lifecycle.sql (idempotence)\n'
  psql -X --set=ON_ERROR_STOP=1 --file="$GROW_EXPIRY_CURSOR_SQL" "$database_url" >/dev/null
  psql -X --set=ON_ERROR_STOP=1 --file="$GROW_EXPIRY_CURSOR_SQL" "$database_url" >/dev/null

  case "$case_name" in
    schema)
      psql -X --set=ON_ERROR_STOP=1 --file="$GROW_SCHEMA_CASE_SQL" "$database_url" >/dev/null
      printf 'schema SQL proofs passed\n'
      (
        export GROW_LIFECYCLE_PYTHON
        "$GROW_LIFECYCLE_PYTHON" "$GROW_SCHEMA_CASE_PY" \
          --database-url "$database_url" --with-queue-smoke
      )
      printf 'schema case passed\n'
      ;;
    membership)
      "$GROW_LIFECYCLE_PYTHON" "$GROW_MEMBERSHIP_SMOKE" \
        --database-url "$database_url"
      printf 'membership case passed\n'
      ;;
    calendar-sync)
      "$GROW_LIFECYCLE_PYTHON" "$GROW_CALENDAR_SYNC_SMOKE" --database-url "$database_url"
      printf 'calendar sync case passed\n'
      ;;
    history-availability)
      "$GROW_LIFECYCLE_PYTHON" "$GROW_HISTORY_AVAILABILITY_SMOKE" --database-url "$database_url"
      printf 'history availability case passed\n'
      ;;
    lossless)
      "$GROW_LIFECYCLE_PYTHON" "$GROW_LOSSLESS_SMOKE" --database-url "$database_url"
      printf 'lossless case passed\n'
      ;;
    compressed-source)
      "$GROW_LIFECYCLE_PYTHON" "$GROW_COMPRESSED_SOURCE_SMOKE" --database-url "$database_url"
      printf 'compressed-source case passed\n'
      ;;
    source-compression-policy)
      [[ "${GROW_SOURCE_COMPRESSION_CASE_LOCKED:-0}" == '1' ]] || {
        grow_fail 'source-compression-policy case must be wrapped in /tmp/grow-lifecycle-harness.lock'
        return 1
      }
      command -v flock >/dev/null 2>&1 || {
        grow_fail 'required command not found: flock'
        return 1
      }
      if flock -n "$GROW_LIFECYCLE_HARNESS_LOCK" true; then
        grow_fail 'source-compression-policy case lock is not held'
        return 1
      fi
      "$GROW_LIFECYCLE_PYTHON" "$GROW_COMPRESSION_POLICY_CASE_PY" \
        --database-url "$database_url"
      printf 'source compression policy case passed\n'
      ;;
    deletion-fence)
      "$GROW_LIFECYCLE_PYTHON" "$GROW_DELETION_FENCE_SMOKE" --database-url "$database_url"
      printf 'deletion-fence case passed\n'
      ;;
    files-export)
      "$GROW_LIFECYCLE_PYTHON" "$GROW_FILES_EXPORT_SMOKE" --database-url "$database_url"
      printf 'files export case passed\n'
      ;;
    annual-correction)
      "$GROW_LIFECYCLE_PYTHON" "$GROW_ANNUAL_CORRECTION_SMOKE" --database-url "$database_url"
      printf 'annual correction case passed\n'
      ;;
    archive-api)
      "$GROW_LIFECYCLE_PYTHON" "$GROW_ARCHIVE_API_SMOKE" --database-url "$database_url"
      printf 'archive API case passed\n'
      ;;
    derived-repair)
      "$GROW_LIFECYCLE_PYTHON" "$GROW_DERIVED_REFRESH_SMOKE" --database-url "$database_url"
      printf 'derived repair case passed\n'
      ;;
    native-readers)
      "$GROW_LIFECYCLE_PYTHON" "$GROW_NATIVE_READERS_SMOKE" --database-url "$database_url"
      printf 'native readers case passed\n'
      ;;
    enabled-lifecycle)
      "$GROW_LIFECYCLE_PYTHON" "$GROW_ENABLED_LIFECYCLE_SMOKE" --database-url "$database_url"
      printf 'enabled lifecycle case passed\n'
      ;;
    rename)
      [[ "${GROW_RENAME_CASE_LOCKED:-0}" == '1' ]] || {
        grow_fail 'rename case must be wrapped in /tmp/grow-lifecycle-harness.lock'
        return 1
      }
      command -v flock >/dev/null 2>&1 || {
        grow_fail 'required command not found: flock'
        return 1
      }
      if flock -n "$GROW_LIFECYCLE_HARNESS_LOCK" true; then
        grow_fail 'rename case lock is not held'
        return 1
      fi
      source "$GROW_RENAME_CASE_SH"
      run_owned_database_rename_proof "$database_url" || return $?
      printf 'rename case passed\n'
      ;;
    *)
      grow_fail "internal runner has no implementation for case: $case_name"
      return 2
      ;;
  esac
}

grow_usage() {
  cat <<'USAGE'
Usage: test-grow-data-lifecycle.sh --case CASE

Cases:
  schema
  membership
  calendar-sync
  history-availability
  lossless
  compressed-source
  source-compression-policy
  deletion-fence
  rename
  files-export
  annual-correction
  archive-api
  derived-repair
  native-readers
  enabled-lifecycle
  all        runs every implemented case
planned (not implemented yet; --case NAME exits 2 with this message):
  grow-rollover, veg-rollover, reader-isolation
USAGE
}

ensure_case_runnable() {
  local case_name="$1"
  is_known_case "$case_name" || {
    grow_usage >&2
    return 2
  }
  local case_implemented=0
  local candidate
  while IFS= read -r candidate; do
    [[ "$candidate" == "$case_name" ]] && case_implemented=1
  done < <(implemented_cases)
  ((case_implemented)) || {
    grow_fail "case $case_name is planned but not implemented yet"
    return 2
  }

  assert_case_files_present
  GROW_LIFECYCLE_PYTHON="$(resolve_grow_python)"
  export GROW_LIFECYCLE_PYTHON

  declare -F with_monitoring_test_db >/dev/null || source "$GROW_MONITORING_HARNESS"
}

run_single_case() {
  local case_name="$1"
  local case_status
  ensure_case_runnable "$case_name" || return $?
  if [[ "$case_name" == 'rename' || "$case_name" == 'source-compression-policy' ]]; then
    # grow_main already holds the harness lock for the whole run.
    if [[ "$case_name" == 'rename' ]]; then
      local GROW_RENAME_CASE_LOCKED=1
      export GROW_RENAME_CASE_LOCKED
    else
      local GROW_SOURCE_COMPRESSION_CASE_LOCKED=1
      export GROW_SOURCE_COMPRESSION_CASE_LOCKED
    fi
    if MONITORING_TEST_IN_PROCESS=1 GROW_HARNESS_INTERNAL=1 \
        MONITORING_TEST_FIXTURE_SQL="$GROW_FIXTURE_SQL" \
        with_monitoring_test_db grow_run_internal_case "$case_name"; then
      case_status=0
    else
      case_status=$?
    fi
    unset GROW_RENAME_CASE_LOCKED GROW_SOURCE_COMPRESSION_CASE_LOCKED
    return "$case_status"
  fi
  GROW_HARNESS_INTERNAL=1 MONITORING_TEST_FIXTURE_SQL="$GROW_FIXTURE_SQL" \
    with_monitoring_test_db env GROW_HARNESS_INTERNAL=1 bash "$0" --internal-case "$case_name"
}

grow_main() {
  local case_name

  if [[ "${1:-}" == '--internal-case' ]]; then
    [[ "$#" -eq 2 ]] || {
      grow_fail '--internal-case requires exactly one case name'
      return 2
    }
    grow_run_internal_case "$2"
    return
  fi

  reject_inherited_environment

  case "${1:-}" in
    --case)
      [[ "$#" -eq 2 ]] || {
        grow_fail '--case requires exactly one case name'
        return 2
      }
      case_name="$2"
      # One lock for the whole run (every case shares the local Postgres).
      # Callers must not wrap this script in flock themselves.
      command -v flock >/dev/null 2>&1 || {
        grow_fail 'required command not found: flock'
        return 1
      }
      local lock_fd
      exec {lock_fd}> "$GROW_LIFECYCLE_HARNESS_LOCK" || {
        grow_fail 'could not open the grow lifecycle harness lock'
        return 1
      }
      flock -x "$lock_fd" || {
        grow_fail 'could not acquire the grow lifecycle harness lock'
        return 1
      }
      if [[ "$case_name" == 'all' ]]; then
        local candidate overall=0
        while IFS= read -r candidate; do
          run_single_case "$candidate" || overall=1
        done < <(implemented_cases)
        return "$overall"
      fi
      run_single_case "$case_name"
      ;;
    --help | -h)
      grow_usage
      ;;
    *)
      grow_usage >&2
      return 2
      ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  grow_main "$@"
fi
