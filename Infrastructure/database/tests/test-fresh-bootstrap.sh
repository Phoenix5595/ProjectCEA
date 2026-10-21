#!/usr/bin/env bash
# Guarded proof that an empty database plus baseline.sql equals the cleared
# production copy. Disposable databases on CEA_TEST_PGPORT (15/test) only.
# Side effect: regenerates Infrastructure/database/baseline/baseline.sql.
set -euo pipefail

fresh_fail() {
  printf 'fresh bootstrap test: %s\n' "$1" >&2
}

WORKTREE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
SCRIPTS="$WORKTREE/Infrastructure/scripts"
CEA_TEST_PGPORT="${CEA_TEST_PGPORT:-5433}"
export CEA_TEST_PGPORT
[[ "$CEA_TEST_PGPORT" =~ ^[0-9]+$ && "$CEA_TEST_PGPORT" != '5432' ]] || {
  fresh_fail "CEA_TEST_PGPORT must be a non-production port: $CEA_TEST_PGPORT"
  exit 2
}

postgres_local() {
  sudo -u postgres env -i "PATH=$PATH" HOME=/var/lib/postgresql \
    PGHOST=/var/run/postgresql "PGPORT=$CEA_TEST_PGPORT" PGUSER=postgres "$@"
}

DATA_DIRECTORY="$(postgres_local psql -p "$CEA_TEST_PGPORT" -X --no-psqlrc --tuples-only --no-align \
  --dbname=postgres --command='SHOW data_directory')"
[[ "$DATA_DIRECTORY" == */15/test ]] || {
  fresh_fail "refusing cluster outside 15/test: $DATA_DIRECTORY"
  exit 2
}

if [[ -z "${PGPASSWORD:-}" ]]; then
  PGPASSWORD="$(sed -n 's/^POSTGRES_PASSWORD=//p' /opt/projectcea/shared/env/postgres.env | head -n1)"
  PGPASSWORD="${PGPASSWORD%\"}"; PGPASSWORD="${PGPASSWORD#\"}"
  PGPASSWORD="${PGPASSWORD%\'}"; PGPASSWORD="${PGPASSWORD#\'}"
fi
export PGPASSWORD

STAMP="$(date +%s%N)"
REFERENCE_DB=''
FRESH_DB="monitoring_test_fresh_${STAMP}"
EMPTY_DB="monitoring_test_empty_${STAMP}"

drop_disposable() {
  local name="$1"
  [[ -n "$name" && "$name" =~ ^monitoring_test_[a-z0-9_]+$ ]] || return 0
  postgres_local dropdb -p "$CEA_TEST_PGPORT" --if-exists --force "$name"
}

on_exit() {
  local status=$?
  trap - EXIT
  set +e
  drop_disposable "$REFERENCE_DB"
  drop_disposable "$FRESH_DB"
  drop_disposable "$EMPTY_DB"
  exit "$status"
}
trap on_exit EXIT

# 9d: apply on an empty database refuses with exit 2.
postgres_local createdb -p "$CEA_TEST_PGPORT" --owner=cea_user "$EMPTY_DB"
set +e
EMPTY_OUTPUT="$(POSTGRES_HOST=127.0.0.1 POSTGRES_PORT="$CEA_TEST_PGPORT" POSTGRES_DB="$EMPTY_DB" \
  POSTGRES_PASSWORD="$PGPASSWORD" python3 "$SCRIPTS/db_migrate.py" --release "$WORKTREE" apply 2>&1)"
EMPTY_STATUS=$?
set -e
[[ "$EMPTY_STATUS" -eq 2 && "$EMPTY_OUTPUT" == 'empty database:'* ]] || {
  fresh_fail "empty-database apply returned $EMPTY_STATUS: $EMPTY_OUTPUT"
  exit 1
}
printf 'empty-database guard: ok\n'

GENERATE_OUTPUT="$(bash "$SCRIPTS/generate_schema_baseline.sh" "$WORKTREE" --keep-reference)"
printf '%s\n' "$GENERATE_OUTPUT"
REFERENCE_DB="$(sed -n 's/^reference_database=//p' <<< "$GENERATE_OUTPUT")"
[[ "$REFERENCE_DB" =~ ^monitoring_test_baseline_[0-9]+$ ]] || {
  fresh_fail "generator did not report a reference database"
  exit 1
}

bash "$SCRIPTS/bootstrap_fresh_db.sh" "$FRESH_DB" --port "$CEA_TEST_PGPORT"

APPLY_OUTPUT="$(POSTGRES_HOST=127.0.0.1 POSTGRES_PORT="$CEA_TEST_PGPORT" POSTGRES_DB="$FRESH_DB" \
  POSTGRES_PASSWORD="$PGPASSWORD" python3 "$SCRIPTS/db_migrate.py" --release "$WORKTREE" apply)"
if grep -q '^applied ' <<< "$APPLY_OUTPUT"; then
  fresh_fail "apply on the fresh database ran migrations: $APPLY_OUTPUT"
  exit 1
fi
printf 'fresh apply: no pending migrations (head %s)\n' "$APPLY_OUTPUT"

# Let each overview checkpoint job run once on the fresh database, so a job
# that fails (and pauses itself) on baseline state is caught by the case.
OVERVIEW_RUNS_SQL="SELECT count(*) FROM timescaledb_information.job_stats s
  JOIN timescaledb_information.jobs j USING (job_id)
  WHERE j.proc_name = 'monitoring_refresh_overview_incremental' AND s.total_runs > 0"
ran=0
for _ in $(seq 1 80); do
  ran="$(postgres_local psql -p "$CEA_TEST_PGPORT" -X --no-psqlrc --tuples-only --no-align \
    --dbname="$FRESH_DB" --command="$OVERVIEW_RUNS_SQL")"
  [[ "$ran" == '3' ]] && break
  sleep 5
done
[[ "$ran" == '3' ]] || {
  fresh_fail "overview checkpoint jobs did not run on the fresh database within 400 s ($ran of 3)"
  exit 1
}

python3 "$WORKTREE/Infrastructure/database/tests/cases/fresh_bootstrap_case.py" "$REFERENCE_DB" "$FRESH_DB"
