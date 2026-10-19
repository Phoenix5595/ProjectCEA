#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly ONCE_SCRIPT="$SCRIPT_DIRECTORY/../../../deploy-grow-lifecycle-once.sh"
readonly STATE_HELPER_SOURCE="$SCRIPT_DIRECTORY/../deploy_state.py"
readonly REAL_PYTHON="$(command -v python3)"
readonly SANDBOX="$(mktemp -d)"
trap 'rm -rf -- "$SANDBOX"' EXIT

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

pass() {
  printf 'PASS: %s\n' "$1"
}

expect_success() {
  "$@" >"$CASE_ROOT/command.out" 2>"$CASE_ROOT/command.err" || {
    cat "$CASE_ROOT/command.out" "$CASE_ROOT/command.err" >&2
    fail "expected command to succeed: $*"
  }
}

expect_failure() {
  if "$@" >"$CASE_ROOT/command.out" 2>"$CASE_ROOT/command.err"; then
    cat "$CASE_ROOT/command.out" "$CASE_ROOT/command.err" >&2
    fail "expected command to fail: $*"
  fi
}

record() {
  printf '%s\n' "$*" >> "$DEPLOY_ONCE_EVENT_LOG"
}

line_of() {
  local pattern="$1"
  local line
  line="$(grep -Fn "$pattern" "$DEPLOY_ONCE_EVENT_LOG" | cut -d: -f1 || true)"
  [[ -n "$line" ]] || fail "event not found: $pattern"
  printf '%s\n' "$line"
}

assert_before() {
  local first second
  first="$(line_of "$1")"
  second="$(line_of "$2")"
  (( first < second )) || fail "expected '$1' before '$2'"
}

current_target() {
  readlink -f "$CURRENT" 2>/dev/null || true
}

setup_case() {
  local name="$1" service
  CASE_ROOT="$SANDBOX/$name"
  BIN="$CASE_ROOT/bin"
  SOURCE="$CASE_ROOT/source"
  DEPLOY_ROOT="$CASE_ROOT/projectcea"
  RELEASES="$DEPLOY_ROOT/releases"
  CURRENT="$DEPLOY_ROOT/current"
  STATE_DIR="$CASE_ROOT/state"
  STATE_JSON="$STATE_DIR/deploy_state.json"
  SYSTEMD_DIR="$CASE_ROOT/systemd"
  API_KEY_ENV_FILE="$CASE_ROOT/api_key.env"
  POSTGRES_ENV_FILE="$CASE_ROOT/postgres.env"
  DEPLOY_ONCE_EVENT_LOG="$CASE_ROOT/events.log"
  DEPLOY_GROW_ONCE_STATE="$STATE_DIR/grow-lifecycle-deploy-once.json"
  TARGET="$RELEASES/candidate-grow-lifecycle"
  mkdir -p "$BIN" "$RELEASES/previous/Infrastructure/scripts" "$SOURCE/Infrastructure" "$SYSTEMD_DIR" "$STATE_DIR"
  mkdir -p \
    "$SOURCE/Infrastructure/backend/app" \
    "$SOURCE/Infrastructure/automation-service/app" \
    "$SOURCE/Infrastructure/can-processor-service" \
    "$SOURCE/Infrastructure/soil-sensor-service" \
    "$SOURCE/Infrastructure/onewire-worker-service" \
    "$SOURCE/Infrastructure/weather-service" \
    "$SOURCE/Infrastructure/monitoring-service" \
    "$SOURCE/Infrastructure/grow-archive-service" \
    "$SOURCE/Infrastructure/shared" \
    "$SOURCE/Infrastructure/scripts" \
    "$SOURCE/Infrastructure/frontend/dist" \
    "$SOURCE/Infrastructure/database"
  for service in backend automation-service can-processor-service soil-sensor-service onewire-worker-service weather-service monitoring-service grow-archive-service; do
    printf 'fixture requirements\n' > "$SOURCE/Infrastructure/$service/requirements.txt"
  done
  printf '#!/usr/bin/env bash\nexit 0\n' > "$SOURCE/Infrastructure/can-processor-service/setup_can.sh"
  printf 'services: []\n' > "$SOURCE/Infrastructure/services.yaml"
  printf '{"name":"fixture","version":"1"}\n' > "$SOURCE/Infrastructure/frontend/package.json"
  printf '<!doctype html>source\n' > "$SOURCE/Infrastructure/frontend/dist/index.html"
  printf '[Service]\nEnvironment=POSTGRES_DB=sj_cea\n' > "$SOURCE/Infrastructure/grow-archive-service.service"
  printf '%s\n' '-- schema' > "$SOURCE/Infrastructure/database/grow_data_lifecycle.sql"
  printf '%s\n' '-- cursor' > "$SOURCE/Infrastructure/database/grow_data_lifecycle_expiry_cursor.sql"
  printf '%s\n' '-- compression' > "$SOURCE/Infrastructure/database/grow_data_source_compression.sql"
  cp "$STATE_HELPER_SOURCE" "$SOURCE/Infrastructure/scripts/deploy_state.py"
  chmod 0755 "$SOURCE/Infrastructure/scripts/deploy_state.py"
  printf '#!/usr/bin/env bash\nexit 0\n' > "$SOURCE/Infrastructure/scripts/install-sudoers.sh"
  chmod 0755 "$SOURCE/Infrastructure/scripts/install-sudoers.sh"
  printf 'previous release\n' > "$RELEASES/previous/Infrastructure/marker"
  cp "$STATE_HELPER_SOURCE" "$RELEASES/previous/Infrastructure/scripts/deploy_state.py"
  chmod 0755 "$RELEASES/previous/Infrastructure/scripts/deploy_state.py"
  cat > "$BIN/service_list.py" <<'PY'
#!/usr/bin/env python3
import sys
if sys.argv[1:] == ["--list-deploy-managed-units"]:
    print("legacy.service")
    print("grow-archive-service.service")
elif sys.argv[1:] == ["--list-health"]:
    print("legacy.service\thttp://health.example.test/health")
PY
  cp "$BIN/service_list.py" "$RELEASES/previous/Infrastructure/scripts/service_list.py"
  chmod 0755 "$BIN/service_list.py" "$RELEASES/previous/Infrastructure/scripts/service_list.py"
  cp "$BIN/service_list.py" "$SOURCE/Infrastructure/scripts/service_list.py"
  chmod 0755 "$SOURCE/Infrastructure/scripts/service_list.py"

  cat > "$BIN/sudo" <<'SUDO'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "-u" && "${2:-}" == "cea_user" ]]; then
  shift 2
  if [[ "${1:-}" == "env" ]]; then
    shift
    while [[ "${1:-}" == *=* ]]; do
      export "$1"
      shift
    done
  fi
  exec "$@"
fi
case "${1:-}" in
  chown)
    printf 'sudo chown %s\n' "${*:2}" >> "$DEPLOY_ONCE_EVENT_LOG"
    exit 0
    ;;
  tee)
    shift
    exec tee "$@"
    ;;
esac
exec "$@"
SUDO

  cat > "$BIN/systemctl" <<'SYSTEMCTL'
#!/usr/bin/env bash
set -euo pipefail
printf 'systemctl %s\n' "$*" >> "$DEPLOY_ONCE_EVENT_LOG"
SYSTEMCTL

  cat > "$BIN/ruff" <<'RUFF'
#!/usr/bin/env bash
printf 'ruff %s\n' "$*" >> "$DEPLOY_ONCE_EVENT_LOG"
if [[ "${1:-}" != check ]]; then
  printf 'unexpected ruff invocation: %s\n' "$*" >&2
  exit 41
fi
for arg in "$@"; do
  [[ "$arg" != --fix && "$arg" != format ]] || exit 42
done
RUFF

  cat > "$BIN/rsync" <<'RSYNC'
#!/usr/bin/env bash
set -euo pipefail
source_path="${@: -2:1}"
destination_path="${@: -1}"
printf 'rsync stage\n' >> "$DEPLOY_ONCE_EVENT_LOG"
mkdir -p "$destination_path"
cp -a "$source_path/". "$destination_path/"
RSYNC

  cat > "$BIN/python3" <<PYTHON
#!/usr/bin/env bash
set -euo pipefail
if [[ "\${1:-}" == -m && "\${2:-}" == venv ]]; then
  mkdir -p "\$3/bin"
  exit 0
fi
exec "$REAL_PYTHON" "\$@"
PYTHON

  cat > "$BIN/pip" <<'PIP'
#!/usr/bin/env bash
printf 'pip %s\n' "$*" >> "$DEPLOY_ONCE_EVENT_LOG"
exit 0
PIP

  cat > "$BIN/npm" <<'NPM'
#!/usr/bin/env bash
set -euo pipefail
printf 'npm %s\n' "$*" >> "$DEPLOY_ONCE_EVENT_LOG"
if [[ "${1:-}" == run && "${2:-}" == build ]]; then
  mkdir -p dist
  printf '<!doctype html>built\n' > dist/index.html
fi
NPM

  cat > "$BIN/psql" <<'PSQL'
#!/usr/bin/env bash
set -euo pipefail
file=""
has_x=0
has_stop=0
has_user=0
has_db=0
for arg in "$@"; do
  case "$arg" in
    -X) has_x=1 ;;
    --set=ON_ERROR_STOP=1) has_stop=1 ;;
    --username=cea_user) has_user=1 ;;
    --dbname=sj_cea) has_db=1 ;;
    --file=*) file="${arg#--file=}" ;;
  esac
done
[[ "$has_x" == 1 && "$has_stop" == 1 && "$has_user" == 1 && "$has_db" == 1 ]] || exit 43
[[ "${PGPASSWORD:-}" == test-db-password ]] || exit 44
printf 'psql %s\n' "$(basename "$file")" >> "$DEPLOY_ONCE_EVENT_LOG"
if [[ "${PSQL_FAIL_ON:-}" == "$(basename "$file")" ]]; then
  exit 45
fi
PSQL

  cat > "$BIN/curl" <<'CURL'
#!/usr/bin/env bash
set -euo pipefail
printf 'curl health\n' >> "$DEPLOY_ONCE_EVENT_LOG"
outfile=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o) outfile="$2"; shift 2 ;;
    -w|-m) shift 2 ;;
    -s|-S|-sS) shift ;;
    *) shift ;;
  esac
done
[[ -z "$outfile" ]] || printf '{}\n' > "$outfile"
printf '200'
CURL

  cat > "$BIN/install" <<'INSTALL'
#!/usr/bin/env bash
set -euo pipefail
printf 'install %s\n' "$*" >> "$DEPLOY_ONCE_EVENT_LOG"
source_path="${@: -2:1}"
destination_path="${@: -1}"
cp "$source_path" "$destination_path"
chmod 0644 "$destination_path"
INSTALL

  cat > "$BIN/rename.sh" <<'RENAME'
#!/usr/bin/env bash
set -euo pipefail
printf 'rename %s\n' "$*" >> "$DEPLOY_ONCE_EVENT_LOG"
if [[ "${1:-}" == apply && "${RENAME_FAIL_APPLY:-0}" == 1 ]]; then
  exit 46
fi
RENAME

  cat > "$BIN/deploy.sh" <<'DEPLOY'
#!/usr/bin/env bash
set -euo pipefail
printf 'deploy called skip=%s target=%s source=%s\n' "${DEPLOY_SKIP_STAGING:-}" "${TARGET:-}" "${SOURCE:-}" >> "$DEPLOY_ONCE_EVENT_LOG"
if [[ "${DEPLOY_TEST_FAIL:-0}" == 1 ]]; then
  ln -sfn "$DEPLOY_ONCE_PREVIOUS_RELEASE" "$DEPLOY_CURRENT"
  sudo systemctl daemon-reload
  sudo systemctl restart legacy.service
  exit 47
fi
ln -sfn "$TARGET" "$DEPLOY_CURRENT"
python3 "$DEPLOY_STATE_HELPER" set-candidate "$DEPLOY_STATE_JSON" "$(basename "$TARGET")" "$TARGET" >/dev/null
DEPLOY

  cat > "$BIN/rollback.sh" <<'ROLLBACK'
#!/usr/bin/env bash
set -euo pipefail
printf 'rollback script called\n' >> "$DEPLOY_ONCE_EVENT_LOG"
ln -sfn "$DEPLOY_ONCE_PREVIOUS_RELEASE" "$DEPLOY_CURRENT"
python3 "$DEPLOY_STATE_HELPER" clear-candidate "$DEPLOY_STATE_JSON" >/dev/null
sudo systemctl daemon-reload
sudo systemctl restart legacy.service
sudo systemctl restart grow-archive-service.service
ROLLBACK

  chmod 0700 "$BIN/sudo" "$BIN/systemctl" "$BIN/ruff" "$BIN/rsync" "$BIN/python3" "$BIN/pip" "$BIN/npm" "$BIN/psql" "$BIN/curl" "$BIN/install" "$BIN/rename.sh" "$BIN/deploy.sh" "$BIN/rollback.sh"
  printf 'CEA_API_KEY=sandbox-key-not-real\n' > "$API_KEY_ENV_FILE"
  printf 'POSTGRES_PASSWORD=test-db-password\nPOSTGRES_HOST=localhost\nPOSTGRES_PORT=5432\n' > "$POSTGRES_ENV_FILE"
  cat > "$STATE_JSON" <<STATE
{"last_good_release_id":"previous","last_good_release_path":"$RELEASES/previous","rollback_to_path":null}
STATE
  ln -sfn "$RELEASES/previous" "$CURRENT"

  export SOURCE DEPLOY_ROOT RELEASES CURRENT STATE_DIR STATE_JSON SYSTEMD_DIR API_KEY_ENV_FILE POSTGRES_ENV_FILE
  export DEPLOY_ONCE_EVENT_LOG DEPLOY_GROW_ONCE_STATE TARGET
  export DEPLOY_LOCK_FILE="$CASE_ROOT/deploy.lock"
  export DEPLOY_ROOT RELEASES CURRENT DEPLOY_RELEASES="$RELEASES" DEPLOY_CURRENT="$CURRENT"
  export DEPLOY_STATE_DIR="$STATE_DIR" DEPLOY_STATE_JSON="$STATE_JSON" DEPLOY_STATE_HELPER="$SOURCE/Infrastructure/scripts/deploy_state.py"
  export DEPLOY_LOCK_FILE DEPLOY_LOG="$CASE_ROOT/deploy.log" DEPLOY_PACKAGE_LOG="$CASE_ROOT/packages.log"
  export DEPLOY_SUDO="$BIN/sudo" DEPLOY_SYSTEMCTL="$BIN/systemctl" DEPLOY_PYTHON="$BIN/python3"
  export DEPLOY_RUFF="$BIN/ruff" DEPLOY_RSYNC="$BIN/rsync" DEPLOY_NPM="$BIN/npm" DEPLOY_PIP="$BIN/pip"
  export DEPLOY_PSQL="$BIN/psql" DEPLOY_CURL="$BIN/curl" DEPLOY_INSTALL="$BIN/install"
  export RENAME_SCRIPT="$BIN/rename.sh" DEPLOY_SCRIPT="$BIN/deploy.sh" ROLLBACK_SCRIPT="$BIN/rollback.sh"
  export INSTALL_SUDOERS_SCRIPT="$SOURCE/Infrastructure/scripts/install-sudoers.sh"
  export DEPLOY_SERVICE_LIST="$BIN/service_list.py" ARCHIVE_DATA_DIR="$CASE_ROOT/archive-data"
  export PROJECT_STATE_DIR="$CASE_ROOT/project-state" NOTES_DATA_DIR="$CASE_ROOT/notes"
  export DEPLOY_HEALTH_ATTEMPTS=1
  export PATH="$BIN:$PATH"
  unset PSQL_FAIL_ON RENAME_FAIL_APPLY DEPLOY_TEST_FAIL
}

# Success path: preflight, safe staging, rename, schema and compression policies,
# unit install, then the regular deploy script's symlink/restart/health phase.
setup_case success
expect_success bash "$ONCE_SCRIPT" --confirm sj_cea
[[ "$(current_target)" == "$TARGET" ]] || fail 'success did not activate staged release'
[[ "$(python3 "$SOURCE/Infrastructure/scripts/deploy_state.py" read "$STATE_JSON" candidate_release_path)" == "$TARGET" ]] || fail 'success did not leave a candidate in deploy state'
[[ "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["phase"])' "$DEPLOY_GROW_ONCE_STATE")" == active ]] || fail 'success state is not active'
[[ "$(python3 -c 'import json,sys; print(int(json.load(open(sys.argv[1]))["compression_attempted"]))' "$DEPLOY_GROW_ONCE_STATE")" == 1 ]] || fail 'success did not record compression policy application'
assert_before 'rename preflight' 'ruff check'
assert_before 'ruff check' 'rsync stage'
assert_before 'rsync stage' 'rename apply --confirm cea_sensors'
assert_before 'rename apply --confirm cea_sensors' 'psql grow_data_lifecycle.sql'
assert_before 'psql grow_data_lifecycle.sql' 'psql grow_data_lifecycle_expiry_cursor.sql'
assert_before 'psql grow_data_lifecycle_expiry_cursor.sql' 'psql grow_data_source_compression.sql'
assert_before 'install -o root -g root -m 0644' 'deploy called skip=1'
assert_before 'psql grow_data_source_compression.sql' 'install -o root -g root -m 0644'
[[ -s "$SYSTEMD_DIR/grow-archive-service.service" ]] || fail 'new archive unit was not installed'
if grep -Eq 'ruff .*--fix|ruff .*format' "$DEPLOY_ONCE_EVENT_LOG"; then
  fail 'single-use script invoked ruff --fix or ruff format'
fi
pass 'success stages read-only lint, rename, schema and compression policies, unit, then deploy'

# A schema failure reverses the DB rename and installs/restarts no candidate.
setup_case schema-failure
export PSQL_FAIL_ON=grow_data_lifecycle_expiry_cursor.sql
expect_failure bash "$ONCE_SCRIPT" --confirm sj_cea
[[ "$(current_target)" == "$RELEASES/previous" ]] || fail 'schema failure changed current release'
[[ ! -e "$SYSTEMD_DIR/grow-archive-service.service" ]] || fail 'schema failure left archive unit installed'
if grep -Fq 'deploy called' "$DEPLOY_ONCE_EVENT_LOG"; then fail 'schema failure reached deploy.sh'; fi
assert_before 'psql grow_data_lifecycle_expiry_cursor.sql' 'rename rollback'
assert_before 'rename rollback' 'systemctl restart legacy.service'
grep -Fq 'schema changes are forward-only and remain applied' "$CASE_ROOT/command.err" || fail 'schema failure did not warn that schema remains applied'
pass 'schema failure rolls back rename and restarts previous release'

# A compression-policy failure uses the same safe recovery; policy changes
# are explicitly reported as non-reversible if the SQL step committed.
setup_case compression-failure
export PSQL_FAIL_ON=grow_data_source_compression.sql
expect_failure bash "$ONCE_SCRIPT" --confirm sj_cea
[[ "$(current_target)" == "$RELEASES/previous" ]] || fail 'compression failure changed current release'
[[ ! -e "$SYSTEMD_DIR/grow-archive-service.service" ]] || fail 'compression failure left archive unit installed'
if grep -Fq 'deploy called' "$DEPLOY_ONCE_EVENT_LOG"; then fail 'compression failure reached deploy.sh'; fi
assert_before 'psql grow_data_source_compression.sql' 'rename rollback'
assert_before 'rename rollback' 'systemctl restart legacy.service'
grep -Fq 'grow source retention/compression policy changes are not reversed' "$CASE_ROOT/command.err" || fail 'compression failure omitted policy rollback warning'
pass 'compression failure rolls back rename and restarts previous release with policy warning'


# A failed deploy's own rollback restart is suppressed until the DB name and
# archive unit are reverted; the wrapper then restarts the previous release.
setup_case deploy-failure
export DEPLOY_TEST_FAIL=1
expect_failure bash "$ONCE_SCRIPT" --confirm sj_cea
[[ "$(current_target)" == "$RELEASES/previous" ]] || fail 'deploy failure did not restore previous release'
assert_before 'deploy called skip=1' 'suppressed previous-release restart'
assert_before 'suppressed previous-release restart' 'rename rollback'
assert_before 'rename rollback' 'systemctl restart legacy.service'
[[ ! -e "$SYSTEMD_DIR/grow-archive-service.service" ]] || fail 'deploy failure left archive unit installed'
grep -Fq 'schema changes are forward-only and remain applied' "$CASE_ROOT/command.err" || fail 'deploy failure did not warn that schema remains applied'
grep -Fq 'grow source retention/compression policy changes are not reversed' "$CASE_ROOT/command.err" || fail 'deploy failure did not warn that compression policy changes remain'
pass 'deploy failure reverses rename before previous-release restart'

# Manual rollback stops the candidate, reverses the rename, removes the new
# unit, then invokes rollback-deploy.sh to switch and restart last-good.
setup_case manual-rollback
expect_success bash "$ONCE_SCRIPT" --confirm sj_cea
: > "$DEPLOY_ONCE_EVENT_LOG"
expect_success bash "$ONCE_SCRIPT" rollback
[[ "$(current_target)" == "$RELEASES/previous" ]] || fail 'manual rollback did not restore previous release'
assert_before 'systemctl stop legacy.service' 'rename rollback'
assert_before 'rename rollback' 'rollback script called'
assert_before 'rollback script called' 'systemctl restart legacy.service'
[[ ! -e "$SYSTEMD_DIR/grow-archive-service.service" ]] || fail 'manual rollback left archive unit installed'
grep -Fq 'schema changes are forward-only and remain applied' "$CASE_ROOT/command.err" || fail 'manual rollback did not warn that schema remains applied'
grep -Fq 'grow source retention/compression policy changes are not reversed' "$CASE_ROOT/command.err" || fail 'manual rollback did not warn that compression policy changes remain'
grep -Fq 'suppressed new archive unit restart: restart grow-archive-service.service' "$DEPLOY_ONCE_EVENT_LOG" || fail 'manual rollback attempted to restart the removed archive unit'
pass 'manual rollback reverses rename and removes unit before restarting previous release'

# Finalize-equivalent deploy-state promotion removes the active candidate;
# rollback then refuses without making any DB or service changes.
setup_case finalized
expect_success bash "$ONCE_SCRIPT" --confirm sj_cea
python3 "$SOURCE/Infrastructure/scripts/deploy_state.py" promote "$STATE_JSON" >/dev/null
: > "$DEPLOY_ONCE_EVENT_LOG"
expect_failure bash "$ONCE_SCRIPT" rollback
[[ "$(current_target)" == "$TARGET" ]] || fail 'post-finalize refusal changed current release'
[[ ! -s "$DEPLOY_ONCE_EVENT_LOG" ]] || fail 'post-finalize refusal ran rollback operations'
grep -Fq 'refusing rollback' "$CASE_ROOT/command.err" || fail 'post-finalize refusal was not explained'
pass 'post-finalize rollback is refused without side effects'

printf 'deploy-grow-lifecycle-once sandbox test passed\n'
