#!/usr/bin/env bash
set -euo pipefail

readonly PURGE_SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly DATABASE_DIRECTORY="$(cd -- "$PURGE_SCRIPT_DIRECTORY/.." && pwd)"
readonly INFRASTRUCTURE_DIRECTORY="$(cd -- "$DATABASE_DIRECTORY/.." && pwd)"
readonly MONITORING_HARNESS="$PURGE_SCRIPT_DIRECTORY/test-monitoring-read-models.sh"
readonly GROW_FIXTURE_SQL="$PURGE_SCRIPT_DIRECTORY/fixtures/grow_data_lifecycle_fixture.sql"
readonly PURGE_SEED_SQL="$PURGE_SCRIPT_DIRECTORY/fixtures/purge_history_before_2026_08.sql"
readonly PURGE_CASE_SQL="$PURGE_SCRIPT_DIRECTORY/cases/purge-history-before-2026-08.sql"
readonly PURGE_OPERATOR_SQL="$DATABASE_DIRECTORY/operator_purge_history_before_2026_08.sql"
readonly GROW_ARCHIVE_DIRECTORY="$INFRASTRUCTURE_DIRECTORY/grow-archive-service"
readonly HARNESS_LOCK='/tmp/grow-lifecycle-harness.lock'

purge_fail() {
  printf 'purge history proof: %s\n' "$1" >&2
  return 1
}

run_expected_refusal() {
  local label="$1"
  shift
  local output

  if output="$(psql -X --set=ON_ERROR_STOP=1 "$@" \
    --file="$PURGE_OPERATOR_SQL" "$MONITORING_TEST_DATABASE_URL" 2>&1)"; then
    purge_fail "apply unexpectedly succeeded for $label"
    return 1
  fi
  [[ "$output" == *'apply refused: -v confirm_database must equal current_database()'* ]] || {
    printf '%s\n' "$output" >&2
    purge_fail "apply refusal did not identify the database confirmation guard for $label"
    return 1
  }
  printf 'apply refusal passed: %s\n' "$label"
}

run_internal_case() {
  source "$MONITORING_HARNESS"

  local database_url="${MONITORING_TEST_DATABASE_URL:-}"
  local python

  [[ "${MONITORING_HARNESS_INTERNAL:-}" == '1' ]] || {
    purge_fail 'internal proof may only run inside with_monitoring_test_db'
    return 1
  }
  [[ -n "$database_url" ]] || {
    purge_fail 'internal proof received no disposable database URL'
    return 1
  }
  assert_safe_database_url "$database_url"

  python="$GROW_ARCHIVE_DIRECTORY/.venv/bin/python"
  if [[ ! -x "$python" ]] || ! "$python" -c 'import asyncpg' >/dev/null 2>&1; then
    command -v python3 >/dev/null 2>&1 && python="$(command -v python3)" || {
      purge_fail 'no Python interpreter with asyncpg is available'
      return 1
    }
    "$python" -c 'import asyncpg' >/dev/null 2>&1 || {
      purge_fail 'no Python interpreter with asyncpg is available'
      return 1
    }
  fi

  export PYTHONPATH="$GROW_ARCHIVE_DIRECTORY:$INFRASTRUCTURE_DIRECTORY:$GROW_ARCHIVE_DIRECTORY/tests${PYTHONPATH:+:$PYTHONPATH}"
  "$python" -c \
    'import asyncio, os; from smoke_derived_refresh import install_real_derived_schema; asyncio.run(install_real_derived_schema(os.environ["MONITORING_TEST_DATABASE_URL"]))'
  printf 'installed the grow lifecycle fixture and canonical monitoring derived schema\n'

  psql -X --set=ON_ERROR_STOP=1 --file="$PURGE_SEED_SQL" "$database_url"
  printf 'seeded ten purgeable streams, protected history/configuration probes, canonical CAGGs, compressed measurement/effective_setpoints chunks, and daily automation_state chunks\n'

  run_expected_refusal 'missing confirm_database' --set=apply=1
  run_expected_refusal 'wrong confirm_database' --set=apply=1 --set=confirm_database=not_the_current_database

  psql -X --set=ON_ERROR_STOP=1 --file="$PURGE_CASE_SQL" "$database_url"
  printf 'purge history proof passed on disposable database %s\n' "${database_url##*/}"
}

main() {
  if [[ "${1:-}" == '--internal-case' ]]; then
    [[ "$#" -eq 1 ]] || {
      purge_fail '--internal-case takes no additional arguments'
      return 2
    }
    run_internal_case
    return
  fi

  if [[ "${PURGE_HISTORY_HARNESS_LOCKED:-0}" != '1' ]]; then
    command -v flock >/dev/null 2>&1 || {
      purge_fail 'required command not found: flock'
      return 1
    }
    exec flock -x "$HARNESS_LOCK" env PURGE_HISTORY_HARNESS_LOCKED=1 bash "$0" "$@"
  fi

  [[ "$#" -eq 0 ]] || {
    purge_fail 'usage: test-purge-history-before-2026-08.sh'
    return 2
  }

  source "$MONITORING_HARNESS"
  export MONITORING_TEST_FIXTURE_SQL="$GROW_FIXTURE_SQL"
  with_monitoring_test_db bash "$PURGE_SCRIPT_DIRECTORY/test-purge-history-before-2026-08.sh" --internal-case
}

main "$@"
