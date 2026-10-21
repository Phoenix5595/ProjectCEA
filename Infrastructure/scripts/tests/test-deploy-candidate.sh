#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly DEPLOY_SCRIPT="$SCRIPT_DIRECTORY/../../../deploy.sh"
readonly ROLLBACK_SCRIPT="$SCRIPT_DIRECTORY/../../../rollback-deploy.sh"
readonly FINALIZE_SCRIPT="$SCRIPT_DIRECTORY/../../../finalize-deploy.sh"
readonly STATE_HELPER="$SCRIPT_DIRECTORY/../deploy_state.py"
readonly IDENTITY_VERIFIER="$SCRIPT_DIRECTORY/../verify_release_identity.py"
readonly ORIGINAL_PATH="$PATH"

readonly SANDBOX="$(mktemp -d)"
readonly BIN_DIRECTORY="$SANDBOX/bin"
readonly DEPLOY_ROOT="$SANDBOX/projectcea"
readonly RELEASES="$DEPLOY_ROOT/releases"
readonly CURRENT="$DEPLOY_ROOT/current"
readonly STATE_DIR="$SANDBOX/state"
readonly STATE_FILE="$STATE_DIR/deploy_state.json"
readonly EVENT_LOG="$SANDBOX/events.log"
readonly PACKAGE_LOG="$SANDBOX/packages.log"
readonly HEALTH_STATE_FILE="$SANDBOX/health-state"
readonly HEALTH_DELAYED_CALLS_FILE="$SANDBOX/health-delayed-calls"
readonly DEPLOY_LOCK_FILE="$SANDBOX/deploy.lock"
readonly API_KEY_ENV_FILE="$SANDBOX/api_key.env"
readonly FRONTEND_BUILD_PROOF="$SANDBOX/frontend-build-proof"
readonly PACKAGE_WRAPPER_PROBE="$SANDBOX/run-package-command.sh"

readonly REPO_ROOT="$(cd "$SCRIPT_DIRECTORY/../../.." && pwd)"
readonly ARCHIVE_ROOT="$SANDBOX/git-archive"
readonly SYSTEMD_DIR="$SANDBOX/systemd"
readonly SYSTEMD_BACKUP_ROOT="$SANDBOX/systemd-backups"
readonly CADDY_DIR="$SANDBOX/caddy"
readonly CADDY_FILE="$CADDY_DIR/Caddyfile"
readonly AUTOMATION_STOPPED_FILE="$SANDBOX/automation-stopped"
readonly REHEARSAL_DIR="$SANDBOX/rehearsals"
readonly MIGRATION_BACKUP_DIR="$SANDBOX/db-backups"
readonly POSTGRES_ENV_FILE="$SANDBOX/postgres.env"
readonly DB_MIGRATE_STUB="$SANDBOX/db_migrate_stub.py"
readonly PSQL_STUB="$SANDBOX/bin/psql"
readonly PG_DUMP_STUB="$SANDBOX/bin/pg_dump"
readonly DB_HEAD_FILE="$SANDBOX/db-head"
readonly ALEMBIC_HEADS_FILE="$SANDBOX/alembic-heads"
readonly PIP_CACHE_DIR="$SANDBOX/pip-cache"
readonly EXPECTED_GIT_SHA="$(printf 'a%.0s' {1..40})"

readonly REL_A="$RELEASES/rel-A"
readonly REL_B="$RELEASES/rel-B"
readonly REL_C="$RELEASES/rel-C"
readonly REL_D="$RELEASES/rel-D"

cleanup() {
  local pids
  pids="$(jobs -p -r 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true
  fi
  rm -rf "$SANDBOX"
}
trap cleanup EXIT

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

pass() {
  printf 'PASS: %s\n' "$1"
}



mkdir -p "$BIN_DIRECTORY" "$RELEASES" "$STATE_DIR" \
  "$ARCHIVE_ROOT/Infrastructure/scripts" "$ARCHIVE_ROOT/Infrastructure/frontend/dist" \
  "$ARCHIVE_ROOT/Infrastructure/can-processor-service" "$ARCHIVE_ROOT/Infrastructure/grow-archive-service" \
  "$ARCHIVE_ROOT/Infrastructure/caddy"
cp -a "$REPO_ROOT/pyproject.toml" "$ARCHIVE_ROOT/pyproject.toml"
cp -a "$REPO_ROOT/Infrastructure/services.yaml" "$ARCHIVE_ROOT/Infrastructure/services.yaml"
cp -a "$REPO_ROOT/Infrastructure/scripts/service_list.py" "$ARCHIVE_ROOT/Infrastructure/scripts/"
cp -a "$REPO_ROOT/Infrastructure/scripts/sync_systemd_units.sh" "$ARCHIVE_ROOT/Infrastructure/scripts/"
cp -a "$REPO_ROOT/Infrastructure/scripts/install-sudoers.sh" "$ARCHIVE_ROOT/Infrastructure/scripts/"
cp -a "$REPO_ROOT/Infrastructure/systemd-overrides" "$ARCHIVE_ROOT/Infrastructure/"
cp -a "$REPO_ROOT/Infrastructure/"*.service "$ARCHIVE_ROOT/Infrastructure/"
cp -a "$REPO_ROOT/Infrastructure/service_error_handler.sh" "$ARCHIVE_ROOT/Infrastructure/"
printf '#!/bin/bash\nexit 0\n' > "$ARCHIVE_ROOT/Infrastructure/can-processor-service/setup_can.sh"
chmod 0755 "$ARCHIVE_ROOT/Infrastructure/can-processor-service/setup_can.sh"
printf '# Required staged-release fixture file.\n' > "$ARCHIVE_ROOT/Infrastructure/grow-archive-service/requirements.txt"
printf '{"name":"cea-frontend","version":"fixture"}\n' > "$ARCHIVE_ROOT/Infrastructure/frontend/package.json"
printf '<!doctype html><title>Fixture</title>\n' > "$ARCHIVE_ROOT/Infrastructure/frontend/dist/index.html"
printf '# archive Caddy configuration\n' > "$ARCHIVE_ROOT/Infrastructure/caddy/Caddyfile"

for rel in "$REL_A" "$REL_B" "$REL_C" "$REL_D"; do
  mkdir -p "$rel"
  cp -a "$ARCHIVE_ROOT/Infrastructure" "$rel/"
  cp -a "$ARCHIVE_ROOT/pyproject.toml" "$rel/pyproject.toml"
  printf '# release Caddy configuration: %s\n' "$(basename "$rel")" > "$rel/Infrastructure/caddy/Caddyfile"
  printf '\n# fixture release: %s\n' "$(basename "$rel")" >> "$rel/Infrastructure/cea-backend.service"
  printf '{"release_id":"%s","git_sha":"%s","alembic_heads":[],"migration_head":null}\n' \
    "$(basename "$rel")" "$EXPECTED_GIT_SHA" > "$rel/deploy_manifest.json"
done

mkdir -p "$RELEASES/rel-old-1" "$RELEASES/rel-old-2" "$RELEASES/rel-old-3"

touch -m -d "2026-01-01 00:00:00" "$REL_A"
touch -m -d "2026-01-01 00:01:00" "$REL_B"
touch -m -d "2026-01-01 00:02:00" "$REL_C"
touch -m -d "2026-01-01 00:03:00" "$RELEASES/rel-old-1"
touch -m -d "2026-01-01 00:04:00" "$RELEASES/rel-old-2"
touch -m -d "2026-01-01 00:05:00" "$RELEASES/rel-old-3"

cat > "$BIN_DIRECTORY/sudo" <<'SUDO'
#!/usr/bin/env bash
case "${1:-}" in
  chown)
    exit 0
    ;;
  cp)
    if [[ "${2:-}" == "/home/antoine/ProjectCEA/Infrastructure/automation-service/install/sudoers-cea-restart" ]]; then
      exit 0
    fi
    ;;
  chmod)
    if [[ "${2:-}" == "0440" && "${3:-}" == "/etc/sudoers.d/sudoers-cea-restart" ]]; then
      exit 0
    fi
    ;;
  install)
    shift
    declare -a install_args=()
    while [[ $# -gt 0 ]]; do
      case "$1" in
        -o|-g) shift 2 ;;
        -o*|-g*) shift ;;
        *) install_args+=("$1"); shift ;;
      esac
    done
    last_index=$((${#install_args[@]} - 1))
    if [[ "${DEPLOY_TEST_FAIL_CADDY_INSTALL:-0}" == "1" && "${install_args[$last_index]:-}" == "${DEPLOY_CADDY_FILE:-}" && ! -e "${EVENT_LOG}.caddy-install-failed" ]]; then
      touch "${EVENT_LOG}.caddy-install-failed"
      exit 42
    fi
    exec install "${install_args[@]}"
    ;;
  python3)
    if [[ "${2:-}" == "-m" && "${3:-}" == "venv" ]]; then
      venv_dir="${4:-}"
      mkdir -p "$venv_dir/bin"
      cat > "$venv_dir/bin/pip" <<'PIP'
#!/usr/bin/env bash
exit 0
PIP
      chmod 0755 "$venv_dir/bin/pip"
      exit 0
    fi
    ;;
esac
exec "$@"
SUDO

cat > "$BIN_DIRECTORY/ruff" <<'RUFF'
#!/usr/bin/env bash
if [[ "${DEPLOY_TEST_LINT_FAILURE:-0}" == "1" ]]; then
  exit 1
fi
if [[ ! -s "$PWD/pyproject.toml" ]]; then
  printf 'release pyproject.toml is missing from Ruff working directory\n' >&2
  exit 2
fi
exit 0
RUFF

cat > "$BIN_DIRECTORY/rsync" <<'RSYNC'
#!/usr/bin/env bash
set -euo pipefail

source_path="${@: -2:1}"
destination_path="${@: -1}"
source_root="${source_path%/}"
delete_destination=0
declare -a excludes=()
for argument in "$@"; do
  case "$argument" in
    --delete) delete_destination=1 ;;
    --exclude=*) excludes+=("${argument#--exclude=}") ;;
  esac
done
mkdir -p "$destination_path"
if [[ "$delete_destination" -eq 1 ]]; then
  find "$destination_path" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
fi
while IFS= read -r -d '' source_file; do
  relative="${source_file#"$source_root"/}"
  basename="${relative##*/}"
  skip=0
  for excluded in "${excludes[@]}"; do
    [[ "$basename" == "$excluded" ]] && skip=1
  done
  [[ "$skip" -eq 0 ]] || continue
  destination_file="$destination_path/$relative"
  mkdir -p "$(dirname "$destination_file")"
  cp -a "$source_file" "$destination_file"
done < <(find "$source_root" -type f -print0)
RSYNC

cat > "$BIN_DIRECTORY/npm" <<'NPM'
#!/usr/bin/env bash
set -euo pipefail

case "${1:-}" in
  ci)
    if [[ "${DEPLOY_TEST_NPM_CI_FAILURE:-0}" == "1" ]]; then
      printf 'npm notice failed retry-package\n'
      printf 'npm ERR retry GET https://registry.example.test/retry-package exhausted\n' >&2
      exit 42
    fi
    printf 'npm notice fetched retry-package\n'
    printf 'npm WARN retry GET https://registry.example.test/retry-package attempt 2\n' >&2
    exit 0
    ;;
  run)
    if [[ "${2:-}" != "build" ]] || [[ "${VITE_CEA_API_KEY:-}" != "${DEPLOY_TEST_EXPECTED_API_KEY:-}" ]]; then
      exit 1
    fi
    printf 'npm notice built frontend\n'
    printf 'npm WARN build metadata retry skipped\n' >&2
    mkdir -p dist
    printf '<!doctype html><title>Built fixture</title>\n' > dist/index.html
    : > "$FRONTEND_BUILD_PROOF"
    exit 0
    ;;
  *)
    exit 1
    ;;
esac
NPM

cat > "$BIN_DIRECTORY/visudo" <<'VISUDO'
#!/usr/bin/env bash
exit 0
VISUDO

cat > "$BIN_DIRECTORY/systemctl" <<'SYSTEMCTL'
#!/usr/bin/env bash
set -euo pipefail

case "${1:-}" in
  show)
    if [[ "${DEPLOY_TEST_NOT_FOUND_UNIT:-}" == "${5:-}" ]]; then
      printf 'not-found\n'
    else
      printf 'loaded\n'
    fi
    ;;
  daemon-reload)
    printf 'systemctl daemon-reload\n' >> "$EVENT_LOG"
    ;;
  restart|start|stop)
    printf 'systemctl %s\n' "$*" >> "$EVENT_LOG"
    if [[ "${2:-}" == "automation-service.service" ]]; then
      if [[ "${1:-}" == "stop" ]]; then
        : > "$AUTOMATION_STOPPED_FILE"
      else
        rm -f "$AUTOMATION_STOPPED_FILE"
      fi
    fi
    if [[ "${DEPLOY_TEST_FAIL_UNIT:-}" == "${2:-}" && ! -e "$EVENT_LOG.restart-failed" ]]; then
      touch "$EVENT_LOG.restart-failed"
      exit 1
    fi
    ;;
  is-active)
    printf 'active\n'
    exit 0
    ;;
  *)
    printf 'unexpected systemctl invocation: %s\n' "$*" >&2
    exit 1
    ;;
esac
SYSTEMCTL

cat > "$BIN_DIRECTORY/curl" <<'CURL'
#!/usr/bin/env bash
set -euo pipefail

outfile="/dev/null"
url=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o) outfile="$2"; shift 2 ;;
    -w|-m|-H|--data|--user) shift 2 ;;
    -s|-S|-sS|-Ss) shift ;;
    -*) shift ;;
    *) url="$1"; shift ;;
  esac
done
printf 'curl %s\n' "$url" >> "$EVENT_LOG"

if [[ "$url" == "${DEPLOY_CADDY_HEALTH_URL:-http://127.0.0.1:8080/}" ]]; then
  if [[ "${DEPLOY_TEST_CADDY_CODE+x}" == "x" ]]; then
    code="$DEPLOY_TEST_CADDY_CODE"
  elif [[ -e "$AUTOMATION_STOPPED_FILE" ]]; then
    code="502"
  else
    code="200"
  fi
  body='{"status":"caddy"}'
else
  state="$(cat "$HEALTH_STATE_FILE" 2>/dev/null || echo not_ready)"
  if [[ "${HEALTH_DELAYED_URL:-}" == "$url" ]] && \
    [[ "$(wc -l < "$HEALTH_DELAYED_CALLS_FILE" 2>/dev/null || echo 0)" -lt "${HEALTH_DELAYED_FAILURES:-0}" ]]; then
    printf 'attempt\n' >> "$HEALTH_DELAYED_CALLS_FILE"
    body='{"status":"starting"}'
    code="503"
  elif [[ "$state" == "ready" ]]; then
    body='{"status":"ready"}'
    code="200"
  else
    body='{"status":"not_ready"}'
    code="503"
  fi
fi
printf '%s\n' "$body" > "$outfile"
printf '%s' "$code"
CURL

cat > "$BIN_DIRECTORY/git" <<'GIT'
#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "-C" ]]; then shift 2; fi
case "${1:-}" in
  rev-parse)
    printf '%s\n' "$EXPECTED_GIT_SHA"
    ;;
  status)
    if [[ "${DEPLOY_TEST_DIRTY_TRACKED:-0}" == "1" ]]; then
      printf ' M Infrastructure/services.yaml\n'
    elif [[ "${DEPLOY_TEST_UNTRACKED_ONLY:-0}" == "1" ]]; then
      printf '?? Infrastructure/new-file.py\n'
    fi
    ;;
  archive)
    tar -C "$DEPLOY_TEST_ARCHIVE_ROOT" -cf - Infrastructure pyproject.toml
    ;;
  *)
    echo "unexpected git invocation: $*" >&2
    exit 2
    ;;
esac
GIT

cat > "$BIN_DIRECTORY/caddy" <<'CADDY'
#!/usr/bin/env bash
printf 'caddy %s\n' "$*" >> "$EVENT_LOG"
exit 0
CADDY

cat > "$PSQL_STUB" <<'PSQL'
#!/usr/bin/env bash
set -euo pipefail
command_text=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --command=*) command_text="${1#--command=}" ;;
    --command|-c)
      command_text="${2:-}"
      shift
      ;;
  esac
  shift
done
if [[ "$command_text" == *"to_regclass"* ]]; then
  printf 't\n'
elif [[ "$command_text" == *"version_num"* ]]; then
  cat "$ALEMBIC_HEADS_FILE"
else
  echo "unexpected psql query: $command_text" >&2
  exit 2
fi
PSQL

cat > "$PG_DUMP_STUB" <<'PGDUMP'
#!/usr/bin/env bash
set -euo pipefail
file=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --file) file="$2"; shift 2 ;;
    *) shift ;;
  esac
done
[[ -n "$file" ]] || exit 2
mkdir -p "$(dirname "$file")"
printf 'fixture custom-format database dump\n' > "$file"
printf 'pg_dump -Fc %s\n' "$file" >> "$EVENT_LOG"
PGDUMP

cat > "$DB_MIGRATE_STUB" <<'PY'
#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
if "--release" not in args:
    raise SystemExit("missing --release")
release_index = args.index("--release")
command = args[release_index + 2]
command_args = args[release_index + 3 :]
event_log = Path(os.environ["EVENT_LOG"])
event_log.parent.mkdir(parents=True, exist_ok=True)
with event_log.open("a", encoding="utf-8") as stream:
    stream.write(f"db_migrate {command} {' '.join(command_args)}\n")

head_path = Path(os.environ["DB_HEAD_FILE"])
head = head_path.read_text(encoding="utf-8").strip() if head_path.exists() else "null"
alembic_path = Path(os.environ["ALEMBIC_HEADS_FILE"])
alembic_heads = [line.strip() for line in alembic_path.read_text(encoding="utf-8").splitlines() if line.strip()]

if command == "plan":
    downtime = os.environ.get("DEPLOY_TEST_DOWNTIME") == "1"
    pending = [{"version": "0001_test_migration", "sha256": "f" * 64, "downtime": True}] if downtime else []
    print(json.dumps({
        "pending": pending,
        "requires_downtime": downtime,
        "set_sha256": "test-set-sha" if downtime else "empty-set-sha",
        "head": None if head == "null" else head,
    }))
elif command == "apply":
    applied = []
    if "--allow-downtime" in command_args and head == "null":
        head = "0001_test_migration"
        head_path.write_text(head + "\n", encoding="utf-8")
        applied.append(head)
    print(json.dumps({"applied": applied, "head": None if head == "null" else head, "alembic_heads": alembic_heads}))
elif command == "head":
    print(head)
elif command == "down":
    target = command_args[command_args.index("--to") + 1]
    head = "null" if target == "none" else target
    head_path.write_text(head + "\n", encoding="utf-8")
elif command == "status":
    print(json.dumps({"head": None if head == "null" else head}))
else:
    raise SystemExit(f"unexpected db_migrate command: {command}")
PY
chmod 0700 "$BIN_DIRECTORY"/*

base_environment() {
  export PATH="$BIN_DIRECTORY:$PATH"
  export SOURCE="$REPO_ROOT"
  export DEPLOY_GIT_REPO="$REPO_ROOT"
  export DEPLOY_ROOT
  export DEPLOY_STATE_DIR="$STATE_DIR"
  export DEPLOY_LOG="$EVENT_LOG"
  export DEPLOY_PACKAGE_LOG="$PACKAGE_LOG"
  export DEPLOY_LOCK_FILE
  export DEPLOY_RELEASES="$RELEASES"
  export DEPLOY_CURRENT="$CURRENT"
  export DEPLOY_STATE_JSON="$STATE_FILE"
  export DEPLOY_MAX_RELEASES=10
  export DEPLOY_SKIP_STAGING=1
  export DEPLOY_HEALTH_ATTEMPTS=1
  export DEPLOY_CADDY_HEALTH_ATTEMPTS=1
  export DEPLOY_SYSTEMD_DIR="$SYSTEMD_DIR"
  export DEPLOY_SYSTEMD_BACKUP_ROOT="$SYSTEMD_BACKUP_ROOT"
  export DEPLOY_CADDY_FILE="$CADDY_FILE"
  export DEPLOY_CADDY_HEALTH_URL="http://127.0.0.1:8080/"
  export DEPLOY_REHEARSAL_DIR="$REHEARSAL_DIR"
  export DEPLOY_BACKUP_DIR="$MIGRATION_BACKUP_DIR"
  export DEPLOY_POSTGRES_ENV_FILE="$POSTGRES_ENV_FILE"
  export DEPLOY_DB_MIGRATE="$DB_MIGRATE_STUB"
  export DEPLOY_PSQL="$PSQL_STUB"
  export DEPLOY_PG_DUMP="$PG_DUMP_STUB"
  export PIP_CACHE_DIR
  export POSTGRES_DB="monitoring_test_deploy_candidates"
  export EVENT_LOG HEALTH_STATE_FILE HEALTH_DELAYED_CALLS_FILE AUTOMATION_STOPPED_FILE
  export DEPLOY_TEST_ARCHIVE_ROOT="$ARCHIVE_ROOT"
  export DEPLOY_TEST_DB_HEAD_FILE="$DB_HEAD_FILE"
  export DB_HEAD_FILE ALEMBIC_HEADS_FILE EXPECTED_GIT_SHA
}

run_frontend_build_key_sandbox() {
  local build_key

  build_key="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"
  printf 'CEA_API_KEY=%s\n' "$build_key" > "$API_KEY_ENV_FILE"
  chmod 0600 "$API_KEY_ENV_FILE"

  clear_state
  set_current "$REL_A"
  write_state_json "rel-A" "$REL_A" ""
  printf 'ready\n' > "$HEALTH_STATE_FILE"
  base_environment
  export API_KEY_ENV_FILE
  export DEPLOY_SKIP_STAGING=0
  export DEPLOY_TEST_EXPECTED_API_KEY="$build_key"
  export FRONTEND_BUILD_PROOF
  export TARGET="$REL_B"
  expect_success bash "$DEPLOY_SCRIPT"
  [[ -f "$FRONTEND_BUILD_PROOF" ]] || fail 'frontend build did not receive the API key'
  [[ "$(current_target)" == "$REL_B" ]] || fail 'frontend build scenario did not activate its candidate'
  [[ -s "$REL_B/pyproject.toml" ]] || fail 'exact-commit staging omitted the root Ruff configuration'
  assert_npm_package_record npm-ci 'sudo env CI=true npm ci --silent --no-audit --no-fund ' 'npm notice fetched retry-package' \
    'npm WARN retry GET https://registry.example.test/retry-package attempt 2' 0
  assert_npm_package_record npm-build 'sudo env CI=true VITE_CEA_API_KEY=\<redacted\> npm run build ' 'npm notice built frontend' \
    'npm WARN build metadata retry skipped' 0
  if grep -RFq --exclude="$(basename "$API_KEY_ENV_FILE")" "$build_key" "$SANDBOX"; then
    fail 'frontend build scenario recorded its API key'
  fi
  unset DEPLOY_TEST_EXPECTED_API_KEY
  pass "frontend build receives the API key without recording it"
}

extract_package_wrapper() {
  python3 - "$DEPLOY_SCRIPT" "$PACKAGE_WRAPPER_PROBE" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1]).read_text()
start = source.index("run_package_command() {")
end = source.index("\n}\n\nsystemd_load_state", start) + 3
Path(sys.argv[2]).write_text(source[start:end] + "\n")
PY
}

count_probe_temp_files() {
  python3 - "$1" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
print(sum(path.name.startswith("tmp.") for path in root.iterdir()))
PY
}

run_package_term_cleanup_sandbox() {
  local probe_root="$SANDBOX/package-term"
  local status temp_count

  mkdir -p "$probe_root/bin"
  cat > "$probe_root/bin/sudo" <<'SUDO'
#!/usr/bin/env bash
exec "$@"
SUDO
  chmod 0700 "$probe_root/bin/sudo"
  extract_package_wrapper
  set +e
  TMPDIR="$probe_root" DEPLOY_PACKAGE_LOG="$probe_root/package.log" \
    PATH="$probe_root/bin:$ORIGINAL_PATH" \
    timeout --signal=TERM --kill-after=1s 0.2s \
    bash -c 'set -euo pipefail; source "$1"; run_package_command term-probe bash -c '\''while :; do :; done'\''' \
    bash "$PACKAGE_WRAPPER_PROBE" >/dev/null 2>"$probe_root/stderr"
  status=$?
  set -e
  temp_count="$(count_probe_temp_files "$probe_root")"
  if [[ "$status" -ne 124 || "$temp_count" -ne 0 ]]; then
    printf 'FAIL: TERM cleanup probe status=%d temp_files=%d (expected 124/0)\n' "$status" "$temp_count"
    ADVERSARIAL_FAILURES=$((ADVERSARIAL_FAILURES + 1))
  else
    pass "TERM cleanup removes package temp files"
  fi
}

run_package_partial_allocation_sandbox() {
  local probe_root="$SANDBOX/package-partial"
  local status temp_count

  mkdir -p "$probe_root/bin"
  cat > "$probe_root/bin/sudo" <<'SUDO'
#!/usr/bin/env bash
exec "$@"
SUDO
  cat > "$probe_root/bin/mktemp" <<'MKTEMP'
#!/usr/bin/env bash
count_file="$TMPDIR/mktemp-count"
count=0
if [[ -f "$count_file" ]]; then count="$(cat "$count_file")"; fi
count=$((count + 1))
printf '%s\n' "$count" > "$count_file"
if [[ "$count" -eq 2 ]]; then
  printf 'forced second mktemp failure\n' >&2
  exit 42
fi
exec /usr/bin/mktemp "$@"
MKTEMP
  chmod 0700 "$probe_root/bin/sudo" "$probe_root/bin/mktemp"
  extract_package_wrapper
  set +e
  TMPDIR="$probe_root" DEPLOY_PACKAGE_LOG="$probe_root/package.log" \
    PATH="$probe_root/bin:$ORIGINAL_PATH" \
    bash -c 'set -euo pipefail; source "$1"; run_package_command partial-probe true' \
    bash "$PACKAGE_WRAPPER_PROBE" >/dev/null 2>"$probe_root/stderr"
  status=$?
  set -e
  temp_count="$(count_probe_temp_files "$probe_root")"
  if [[ "$status" -ne 42 || "$temp_count" -ne 0 ]]; then
    printf 'FAIL: partial allocation cleanup probe status=%d temp_files=%d (expected 42/0)\n' "$status" "$temp_count"
    ADVERSARIAL_FAILURES=$((ADVERSARIAL_FAILURES + 1))
  else
    pass "partial allocation cleanup removes first package temp file"
  fi
}

run_package_trap_scope_sandbox() {
  local probe_root="$SANDBOX/package-trap-scope"
  local status return_count

  mkdir -p "$probe_root/bin"
  cat > "$probe_root/bin/sudo" <<'SUDO'
#!/usr/bin/env bash
exec "$@"
SUDO
  chmod 0700 "$probe_root/bin/sudo"
  extract_package_wrapper
  : > "$probe_root/return-trap-marker"
  set +e
  TMPDIR="$probe_root" DEPLOY_PACKAGE_LOG="$probe_root/package.log" RETURN_TRAP_MARKER="$probe_root/return-trap-marker" \
    PATH="$probe_root/bin:$ORIGINAL_PATH" \
    bash -c 'set -euo pipefail; set -T; source "$1"; trap '\''printf outer-return\\n >> "$RETURN_TRAP_MARKER"'\'' RETURN; run_package_command trap-probe true; printf after\\n >> "$RETURN_TRAP_MARKER"' \
    bash "$PACKAGE_WRAPPER_PROBE"
  status=$?
  set -e
  return_count="$(grep -c '^outer-return$' "$probe_root/return-trap-marker" || true)"
  if [[ "$status" -ne 0 || "$return_count" -ne 1 ]]; then
    printf 'FAIL: RETURN trap scope probe status=%d prior_trap_runs=%d (expected 0/1)\n' "$status" "$return_count"
    ADVERSARIAL_FAILURES=$((ADVERSARIAL_FAILURES + 1))
  else
    pass "package cleanup restores the prior RETURN trap"
  fi
}

run_package_default_path_sandbox() {
  local build_key

  build_key="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"
  printf 'CEA_API_KEY=%s\n' "$build_key" > "$API_KEY_ENV_FILE"
  chmod 0600 "$API_KEY_ENV_FILE"

  clear_state
  set_current "$REL_A"
  write_state_json "rel-A" "$REL_A" ""
  printf 'ready\n' > "$HEALTH_STATE_FILE"
  base_environment
  unset DEPLOY_PACKAGE_LOG
  export API_KEY_ENV_FILE
  export DEPLOY_SKIP_STAGING=0
  export DEPLOY_TEST_EXPECTED_API_KEY="$build_key"
  export FRONTEND_BUILD_PROOF
  export TARGET="$REL_B"
  expect_success bash "$DEPLOY_SCRIPT"
  [[ -f "$FRONTEND_BUILD_PROOF" ]] || fail 'default package log scenario did not build the frontend'
  [[ "$(current_target)" == "$REL_B" ]] || fail 'default package log scenario did not activate its candidate'
  cp "$STATE_DIR/deploy-packages.log" "$PACKAGE_LOG"
  assert_npm_package_record npm-ci 'sudo env CI=true npm ci --silent --no-audit --no-fund ' 'npm notice fetched retry-package' \
    'npm WARN retry GET https://registry.example.test/retry-package attempt 2' 0
  unset DEPLOY_TEST_EXPECTED_API_KEY
  pass "default package log path records npm-ci output"
}

assert_npm_package_record() {
  local command_name="$1"
  local command_argv="$2"
  local stdout_text="$3"
  local stderr_text="$4"
  local exit_status="$5"
  local command_line stdout_begin_line stdout_line stdout_end_line stderr_begin_line stderr_line stderr_end_line exit_line

  command_line=$(grep -Fn "[package-command] command=$command_name argv=$command_argv" "$PACKAGE_LOG" | cut -d: -f1 || true)
  stdout_begin_line=$(grep -Fn "[package-command] command=$command_name stdout_begin" "$PACKAGE_LOG" | cut -d: -f1 || true)
  stdout_line=$(grep -Fn "[package-command] command=$command_name stdout=$stdout_text" "$PACKAGE_LOG" | cut -d: -f1 || true)
  stdout_end_line=$(grep -Fn "[package-command] command=$command_name stdout_end" "$PACKAGE_LOG" | cut -d: -f1 || true)
  stderr_begin_line=$(grep -Fn "[package-command] command=$command_name stderr_begin" "$PACKAGE_LOG" | cut -d: -f1 || true)
  stderr_line=$(grep -Fn "[package-command] command=$command_name stderr=$stderr_text" "$PACKAGE_LOG" | cut -d: -f1 || true)
  stderr_end_line=$(grep -Fn "[package-command] command=$command_name stderr_end" "$PACKAGE_LOG" | cut -d: -f1 || true)
  exit_line=$(grep -Fn "[package-command] command=$command_name exit_status=$exit_status" "$PACKAGE_LOG" | cut -d: -f1 || true)
  [[ -n "$command_line" && -n "$stdout_begin_line" && -n "$stdout_line" && -n "$stdout_end_line" && \
    -n "$stderr_begin_line" && -n "$stderr_line" && -n "$stderr_end_line" && -n "$exit_line" ]] || \
    fail 'npm package record does not preserve both streams and exit status'
  (( command_line < stdout_begin_line && stdout_begin_line < stdout_line && stdout_line < stdout_end_line && \
    stdout_end_line < stderr_begin_line && stderr_begin_line < stderr_line && stderr_line < stderr_end_line && \
    stderr_end_line < exit_line )) || fail 'npm package record is not deterministically ordered'
}

run_package_failure_sandbox() {
  clear_state
  set_current "$REL_A"
  write_state_json "rel-A" "$REL_A" "$REL_C"
  printf 'ready\n' > "$HEALTH_STATE_FILE"
  base_environment
  export DEPLOY_SKIP_STAGING=0
  export DEPLOY_TEST_NPM_CI_FAILURE=1
  export TARGET="$REL_B"
  expect_failure bash "$DEPLOY_SCRIPT"
  assert_npm_package_record npm-ci 'sudo env CI=true npm ci --silent --no-audit --no-fund ' 'npm notice failed retry-package' \
    'npm ERR retry GET https://registry.example.test/retry-package exhausted' 42
  [[ "$(current_target)" == "$REL_A" ]] || fail 'package failure changed the active release'
  [[ "$(state_field last_good_release_id)" == "rel-A" ]] || fail 'package failure changed last-good state'
  [[ "$(state_field rollback_to_path)" == "$REL_C" ]] || fail 'package failure changed rollback target'
  [[ "$(state_field candidate_release_id)" == "" ]] || fail 'package failure recorded a candidate'
  [[ "$(cat "$EVENT_LOG")" != *"systemctl restart"* ]] || fail 'package failure restarted services'
  unset DEPLOY_TEST_NPM_CI_FAILURE
  pass "package command failure records both streams and preserves deploy state"
}

expect_failure() {
  set +e
  "$@" >/dev/null 2>&1
  local status=$?
  set -e
  if [[ $status -eq 0 ]]; then
    printf 'expected command to fail: %s\n' "$*" >&2
    exit 1
  fi
}

expect_success() {
  "$@" >/dev/null
}

state_field() {
  python3 "$STATE_HELPER" read "$STATE_FILE" "$1"
}

current_target() {
  readlink -f "$CURRENT" 2>/dev/null || true
}

assert_event_before() {
  local earlier later
  earlier="$(grep -n -m 1 -F -- "$1" "$EVENT_LOG" | cut -d: -f1)"
  later="$(grep -n -m 1 -F -- "$2" "$EVENT_LOG" | cut -d: -f1)"
  [[ -n "$earlier" && -n "$later" && "$earlier" -lt "$later" ]] || \
    fail "event order was not preserved: $1 before $2"
}

clear_state() {
  rm -f "$STATE_FILE" "$EVENT_LOG" "$PACKAGE_LOG" "$HEALTH_STATE_FILE" "$HEALTH_DELAYED_CALLS_FILE"
  rm -f "$STATE_DIR/deploy-packages.log"
  rm -f "$CURRENT"
  rm -f "$EVENT_LOG.restart-failed" "$EVENT_LOG.caddy-install-failed" "$AUTOMATION_STOPPED_FILE"
  rm -rf "$SYSTEMD_DIR" "$SYSTEMD_BACKUP_ROOT" "$REHEARSAL_DIR" "$MIGRATION_BACKUP_DIR"
  mkdir -p "$SYSTEMD_DIR" "$CADDY_DIR" "$REHEARSAL_DIR" "$MIGRATION_BACKUP_DIR"
  printf '# pre-release Caddy configuration\n' > "$CADDY_FILE"
  printf 'null\n' > "$DB_HEAD_FILE"
  : > "$ALEMBIC_HEADS_FILE"
  printf 'POSTGRES_PASSWORD=test\n' > "$POSTGRES_ENV_FILE"
  unset HEALTH_DELAYED_URL HEALTH_DELAYED_FAILURES ROLLBACK_HEALTH_TIMEOUT_SECONDS
  unset DEPLOY_TEST_DIRTY_TRACKED DEPLOY_TEST_UNTRACKED_ONLY DEPLOY_TEST_LINT_FAILURE DEPLOY_TEST_DOWNTIME
  unset DEPLOY_TEST_NOT_FOUND_UNIT DEPLOY_TEST_CADDY_CODE DEPLOY_TEST_FAIL_UNIT DEPLOY_TEST_FAIL_CADDY_INSTALL
}

set_current() {
  ln -sfn "$1" "$CURRENT"
  [[ -d "$1/Infrastructure" ]] || return 0
  mkdir -p "$SYSTEMD_DIR" "$CADDY_DIR"
  cp "$1/Infrastructure/caddy/Caddyfile" "$CADDY_FILE"
  for unit_file in "$1"/Infrastructure/*.service; do
    [[ -f "$unit_file" ]] || continue
    cp -a "$unit_file" "$SYSTEMD_DIR/$(basename "$unit_file")"
  done
  for override_file in "$1"/Infrastructure/systemd-overrides/*/*.conf; do
    [[ -f "$override_file" ]] || continue
    relative="${override_file#"$1/Infrastructure/systemd-overrides/"}"
    mkdir -p "$SYSTEMD_DIR/$(dirname "$relative")"
    cp -a "$override_file" "$SYSTEMD_DIR/$relative"
  done
  local backup_dir="$SYSTEMD_BACKUP_ROOT/$(basename "$1")"
  mkdir -p "$backup_dir"
  [[ -f "$backup_dir/created.txt" ]] || : > "$backup_dir/created.txt"
}

write_state_json() {
  local last_good_id="${1:-}"
  local last_good_path="${2:-}"
  local rollback_path="${3:-}"
  local candidate_id="${4:-}"
  local candidate_path="${5:-}"
  python3 - "$STATE_FILE" "$last_good_id" "$last_good_path" "$rollback_path" "$candidate_id" "$candidate_path" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
state = {
  "last_good_release_id": sys.argv[2] or None,
  "last_good_release_path": sys.argv[3] or None,
  "rollback_to_path": sys.argv[4] or None,
  "candidate_release_id": sys.argv[5] or None,
  "candidate_release_path": sys.argv[6] or None,
  "candidate_started_at": "2026-07-30T00:00:00Z" if sys.argv[6] else None,
}

path.write_text(json.dumps(state, indent=2) + "\n")
PY
}

create_identity_fixture() {
  local fixture
  fixture="$(mktemp -d "$SANDBOX/reproducibility.XXXXXX")"

  IDENTITY_REPO="$fixture/repo"
  IDENTITY_EVIDENCE="$fixture/evidence"
  IDENTITY_RELEASES="$fixture/releases"
  IDENTITY_CURRENT="$fixture/current"
  IDENTITY_STATE_DIR="$fixture/state"
  IDENTITY_STATE_FILE="$IDENTITY_STATE_DIR/deploy_state.json"
  IDENTITY_EVENT_LOG="$fixture/events.log"
  IDENTITY_LOCK_FILE="$fixture/deploy.lock"
  IDENTITY_TARGET="$IDENTITY_RELEASES/candidate"
  IDENTITY_PREVIOUS="$IDENTITY_RELEASES/last-good"

  mkdir -p "$IDENTITY_REPO/Infrastructure/caddy" "$IDENTITY_EVIDENCE" "$IDENTITY_TARGET/Infrastructure" \
    "$IDENTITY_PREVIOUS/Infrastructure" "$IDENTITY_STATE_DIR"
  printf 'fixture release input\n' > "$IDENTITY_REPO/Infrastructure/caddy/Caddyfile"
  printf 'Infrastructure/caddy/Caddyfile\n' > "$IDENTITY_REPO/release-inputs.txt"
  PATH="$ORIGINAL_PATH" git init -q "$IDENTITY_REPO"
  PATH="$ORIGINAL_PATH" git -C "$IDENTITY_REPO" config user.email fixture@example.test
  PATH="$ORIGINAL_PATH" git -C "$IDENTITY_REPO" config user.name fixture
  PATH="$ORIGINAL_PATH" git -C "$IDENTITY_REPO" add Infrastructure/caddy/Caddyfile release-inputs.txt
  PATH="$ORIGINAL_PATH" git -C "$IDENTITY_REPO" commit -qm 'fixture release input'
  cp -R "$IDENTITY_REPO/Infrastructure/." "$IDENTITY_TARGET/Infrastructure/"
  cp -R "$REL_D/Infrastructure/." "$IDENTITY_TARGET/Infrastructure/"
  cp "$IDENTITY_REPO/Infrastructure/caddy/Caddyfile" "$IDENTITY_TARGET/Infrastructure/caddy/Caddyfile"
  cp -a "$ARCHIVE_ROOT/pyproject.toml" "$IDENTITY_TARGET/pyproject.toml"
  ln -sfn "$IDENTITY_PREVIOUS" "$IDENTITY_CURRENT"
}

identity_preflight() {
  PATH="$ORIGINAL_PATH" python3 "$IDENTITY_VERIFIER" preflight \
    --repo-root "$IDENTITY_REPO" \
    --allowlist "$IDENTITY_REPO/release-inputs.txt" \
    --evidence-dir "$IDENTITY_EVIDENCE" \
    --check-cmd true
}

identity_verify() {
  PATH="$ORIGINAL_PATH" python3 "$IDENTITY_VERIFIER" verify \
    --repo-root "$IDENTITY_REPO" \
    --record "$IDENTITY_EVIDENCE/release-identity-preflight.json" \
    --release-root "$IDENTITY_TARGET"
}

identity_run() {
  env \
    PATH="$BIN_DIRECTORY:$ORIGINAL_PATH" \
    DEPLOY_ROOT="$(dirname "$IDENTITY_RELEASES")" \
    DEPLOY_STATE_DIR="$IDENTITY_STATE_DIR" \
    DEPLOY_LOG="$IDENTITY_EVENT_LOG" \
    DEPLOY_LOCK_FILE="$IDENTITY_LOCK_FILE" \
    DEPLOY_RELEASES="$IDENTITY_RELEASES" \
    DEPLOY_CURRENT="$IDENTITY_CURRENT" \
    DEPLOY_STATE_JSON="$IDENTITY_STATE_FILE" \
    DEPLOY_MAX_RELEASES=10 \
    DEPLOY_SKIP_STAGING=1 \
    DEPLOY_HEALTH_ATTEMPTS=1 \
    DEPLOY_CADDY_HEALTH_ATTEMPTS=1 \
    HEALTH_STATE_FILE="$HEALTH_STATE_FILE" \
    EVENT_LOG="$IDENTITY_EVENT_LOG" \
    "$@"
}

identity_deploy() {
  printf 'ready\n' > "$HEALTH_STATE_FILE"
  identity_run TARGET="$IDENTITY_TARGET" bash "$DEPLOY_SCRIPT" >/dev/null
}

identity_finalize_if_verified() {
  identity_verify || return $?
  identity_run bash "$FINALIZE_SCRIPT" --confirm
}

EVIDENCE_STATES="$SANDBOX/evidence-states.json"
printf '[' > "$EVIDENCE_STATES"

append_state() {
  if [[ -s "$EVIDENCE_STATES" && "$(tail -c 1 "$EVIDENCE_STATES")" != "[" ]]; then
    printf ',' >> "$EVIDENCE_STATES"
  fi
  python3 - "$STATE_FILE" "$1" >> "$EVIDENCE_STATES" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
state = json.loads(path.read_text())
state["_note"] = sys.argv[2]
print(json.dumps(state))
PY
}

ADVERSARIAL_FAILURES=0
run_package_term_cleanup_sandbox
run_package_partial_allocation_sandbox
run_package_trap_scope_sandbox
if [[ "$ADVERSARIAL_FAILURES" -ne 0 ]]; then
  fail "package cleanup adversarial probes failed: $ADVERSARIAL_FAILURES"
fi

run_frontend_build_key_sandbox
run_package_default_path_sandbox
run_package_failure_sandbox

# A failed package build leaves an incomplete tree; skip-staging must not bypass
# the guard or change the active release, rollback target, or service state.
clear_state
set_current "$REL_A"
write_state_json "rel-A" "$REL_A" "$REL_C"
base_environment
export TARGET="$REL_B"
cp "$STATE_FILE" "$SANDBOX/state-before-guard.json"
expect_failure bash "$DEPLOY_SCRIPT"
cmp "$STATE_FILE" "$SANDBOX/state-before-guard.json" || fail 'incomplete staging changed state'
[[ "$(current_target)" == "$REL_A" ]] || fail 'incomplete staging switched current'
[[ "$(cat "$EVENT_LOG")" != *"systemctl restart"* ]] || fail 'incomplete staging restarted services'
pass 'incomplete frontend build is blocked even with skip-staging'

# Restore a complete pre-staged candidate for the following independent cases.
mkdir -p "$REL_B/Infrastructure/frontend/dist"
printf '<!doctype html><title>Fixture</title>\n' > "$REL_B/Infrastructure/frontend/dist/index.html"

clear_state
set_current "$REL_A"
write_state_json "rel-A" "$REL_A" "$REL_C"
base_environment
export TARGET="$RELEASES/never-staged"
cp "$STATE_FILE" "$SANDBOX/state-before-guard.json"
expect_failure bash "$DEPLOY_SCRIPT"
cmp "$STATE_FILE" "$SANDBOX/state-before-guard.json" || fail 'missing staging changed state'
[[ "$(current_target)" == "$REL_A" ]] || fail 'missing staging switched current'
[[ ! -e "$TARGET" ]] || fail 'guard created an empty release'
[[ "$(cat "$EVENT_LOG")" != *"systemctl restart"* ]] || fail 'missing staging restarted services'
pass 'nonexistent staged target cannot create a dangling current symlink'

clear_state
set_current "$REL_A"
write_state_json "rel-A" "$REL_A" "$REL_C"
base_environment
export TARGET="$REL_B"
mv "$REL_B/Infrastructure/can-processor-service/setup_can.sh" "$SANDBOX/setup-can.saved"
expect_failure bash "$DEPLOY_SCRIPT"
[[ "$(current_target)" == "$REL_A" ]] || fail 'missing CAN setup switched current'
[[ "$(cat "$EVENT_LOG")" != *"systemctl restart"* ]] || fail 'missing CAN setup restarted services'
mv "$SANDBOX/setup-can.saved" "$REL_B/Infrastructure/can-processor-service/setup_can.sh"
pass 'missing hardware setup entrypoint is blocked before service restart'

clear_state
set_current "$REL_A"
write_state_json "rel-A" "$REL_A" "$REL_C"
printf 'ready\n' > "$HEALTH_STATE_FILE"
base_environment
export TARGET="$REL_B"
export DEPLOY_TEST_FAIL_UNIT=can-setup.service
expect_failure bash "$DEPLOY_SCRIPT"
unset DEPLOY_TEST_FAIL_UNIT
[[ "$(current_target)" == "$REL_A" ]] || fail 'restart failure left the candidate active'
[[ "$(state_field last_good_release_id)" == "rel-A" ]] || fail 'restart failure changed last-good'
[[ "$(state_field rollback_to_path)" == "$REL_C" ]] || fail 'restart failure changed rollback target'
[[ "$(state_field candidate_release_id)" == "" ]] || fail 'restart failure recorded a candidate'
[[ "$(cat "$EVENT_LOG")" == *"service_restart_failed"* ]] || fail 'restart failure was not recorded'
[[ "$(cat "$EVENT_LOG")" == *"rollback_auto_done"* ]] || fail 'restart failure did not restore services'
assert_event_before 'systemctl restart grow-archive-service.service' 'rollback_auto_symlink'
pass 'service restart failure restores the previous release before exiting'

clear_state
set_current "$RELEASES/dangling"
write_state_json "rel-A" "$REL_A" "$REL_C"
cp "$STATE_FILE" "$SANDBOX/state-before-guard.json"
expect_failure python3 "$STATE_HELPER" reconcile "$STATE_FILE" "$CURRENT"
cmp "$STATE_FILE" "$SANDBOX/state-before-guard.json" || fail 'dangling reconciliation poisoned last-good'
base_environment
export TARGET="$REL_B"
expect_failure bash "$DEPLOY_SCRIPT"
cmp "$STATE_FILE" "$SANDBOX/state-before-guard.json" || fail 'deploy reconciled a missing current target'
[[ "$(cat "$EVENT_LOG")" != *"systemctl restart"* ]] || fail 'missing current restarted services'
pass 'dangling current cannot be reconciled into last-good'

clear_state
set_current "$REL_A"
write_state_json "rel-C" "$REL_C" "$REL_B"
python3 "$STATE_HELPER" cleanup "$RELEASES" "$STATE_FILE" 0 "$CURRENT" > "$SANDBOX/deletable-releases"
if grep -Fxq "$REL_A" "$SANDBOX/deletable-releases"; then
  fail 'cleanup selected the active release absent from stale state'
fi
grep -Fxq "$REL_D" "$SANDBOX/deletable-releases" || fail 'cleanup did not select an unprotected release'
pass 'cleanup protects the actual active symlink independently of state'

# --- Scenario 1: stale state reconciliation ---
clear_state
set_current "$REL_A"
write_state_json "rel-C" "$REL_C" "$REL_B"
printf 'ready\n' > "$HEALTH_STATE_FILE"
base_environment
export TARGET="$REL_A"
expect_success bash "$DEPLOY_SCRIPT"
[[ "$(current_target)" == "$REL_A" ]] || { printf 'scenario 1: current should be rel-A\n' >&2; exit 1; }
[[ "$(cat "$EVENT_LOG")" == *"systemctl restart monitoring-service.service"* ]] || { printf 'scenario 1: monitoring service was not restarted\n' >&2; exit 1; }
python3 - "$CURRENT/deploy_manifest.json" "$EXPECTED_GIT_SHA" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if manifest["git_sha"] != sys.argv[2]:
    raise SystemExit("scenario 1: manifest git_sha is not the resolved full commit SHA")
if manifest["alembic_heads"] != [] or manifest["migration_head"] is not None:
    raise SystemExit("scenario 1: migration heads are missing or incorrect")
if "monitoring-service" not in manifest["services"]:
    raise SystemExit("scenario 1: monitoring service missing from deploy manifest")
PY
[[ "$(state_field last_good_release_id)" == "rel-A" ]] || { printf 'scenario 1: stale last_good was not reconciled\n' >&2; exit 1; }
[[ "$(state_field candidate_release_id)" == "rel-A" ]] || { printf 'scenario 1: candidate should be rel-A\n' >&2; exit 1; }
[[ "$(state_field rollback_to_path)" == "" ]] || { printf 'scenario 1: rollback should be cleared after reconciliation\n' >&2; exit 1; }
[[ "$(cat "$EVENT_LOG")" == *"candidate-active"* ]] || { printf 'scenario 1: candidate-active event missing\n' >&2; exit 1; }
append_state "stale-reconciled-and-deployed"
pass "scenario 1: stale state reconciliation"

# --- Scenario 2: candidate health success leaves old last-good intact ---
clear_state
set_current "$REL_A"
write_state_json "rel-A" "$REL_A" "$REL_C"
printf 'ready\n' > "$HEALTH_STATE_FILE"
base_environment
export TARGET="$REL_B"
expect_success bash "$DEPLOY_SCRIPT"
[[ "$(current_target)" == "$REL_B" ]] || { printf 'scenario 2: current should be rel-B\n' >&2; exit 1; }
[[ "$(state_field last_good_release_id)" == "rel-A" ]] || { printf 'scenario 2: last_good should remain rel-A\n' >&2; exit 1; }
[[ "$(state_field candidate_release_id)" == "rel-B" ]] || { printf 'scenario 2: candidate should be rel-B\n' >&2; exit 1; }
[[ "$(state_field rollback_to_path)" == "$REL_C" ]] || { printf 'scenario 2: rollback should be unchanged\n' >&2; exit 1; }
[[ "$(cat "$EVENT_LOG")" == *"candidate-active"* ]] || { printf 'scenario 2: candidate-active event missing\n' >&2; exit 1; }
append_state "candidate-active-last-good-preserved"
pass "scenario 2: candidate health success leaves old last-good intact"

# --- Scenario 2b: a second deploy is blocked while a candidate is active ---
base_environment
export TARGET="$REL_C"
expect_failure bash "$DEPLOY_SCRIPT"
[[ "$(cat "$EVENT_LOG")" == *"deploy_blocked"* ]] || { printf 'scenario 2b: deploy_blocked event missing\n' >&2; exit 1; }
[[ "$(state_field candidate_release_id)" == "rel-B" ]] || { printf 'scenario 2b: candidate should still be rel-B\n' >&2; exit 1; }
[[ "$(current_target)" == "$REL_B" ]] || { printf 'scenario 2b: current should remain rel-B\n' >&2; exit 1; }
pass "scenario 2b: second deploy is blocked while a candidate is active"

# --- Scenario 3: health failure rolls back immediately and clears candidate ---
clear_state
set_current "$REL_A"
write_state_json "rel-A" "$REL_A" "$REL_C"
# health not ready
base_environment
export TARGET="$REL_B"
expect_failure bash "$DEPLOY_SCRIPT"
[[ "$(current_target)" == "$REL_A" ]] || { printf 'scenario 3: current should roll back to rel-A\n' >&2; exit 1; }
[[ "$(state_field last_good_release_id)" == "rel-A" ]] || { printf 'scenario 3: last_good should remain rel-A\n' >&2; exit 1; }
[[ "$(state_field candidate_release_id)" == "" ]] || { printf 'scenario 3: candidate should be cleared\n' >&2; exit 1; }
[[ "$(cat "$EVENT_LOG")" == *"candidate-rejected"* ]] || { printf 'scenario 3: candidate-rejected event missing\n' >&2; exit 1; }
append_state "health-failure-rolled-back"
pass "scenario 3: health failure rolls back and clears candidate"

# --- Scenario 4: finalize promotes candidate to last-good ---
clear_state
set_current "$REL_B"
write_state_json "rel-A" "$REL_A" "$REL_C" "rel-B" "$REL_B"
printf 'ready\n' > "$HEALTH_STATE_FILE"
base_environment
expect_success bash "$FINALIZE_SCRIPT" --confirm
[[ "$(current_target)" == "$REL_B" ]] || { printf 'scenario 4: current should remain rel-B\n' >&2; exit 1; }
[[ "$(state_field last_good_release_id)" == "rel-B" ]] || { printf 'scenario 4: last_good should be rel-B\n' >&2; exit 1; }
[[ "$(state_field rollback_to_path)" == "$REL_A" ]] || { printf 'scenario 4: rollback should be former last-good rel-A\n' >&2; exit 1; }
[[ "$(state_field candidate_release_id)" == "" ]] || { printf 'scenario 4: candidate should be cleared\n' >&2; exit 1; }
[[ "$(cat "$EVENT_LOG")" == *"finalized"* ]] || { printf 'scenario 4: finalized event missing\n' >&2; exit 1; }
[[ "$(cat "$EVENT_LOG")" == *"systemctl restart grow-archive-service.service"* ]] || \
  fail 'scenario 4: finalize did not restart the target release service list'
for port in 8000 8001 8002 8003 8004 8005 8006; do
  grep -Fq "curl http://127.0.0.1:$port/ready" "$EVENT_LOG" || \
    fail "scenario 4: finalize did not check ready port $port"
done
append_state "finalized"
pass "scenario 4: finalize promotes candidate to last-good"

# --- Scenario 5: no-arg rollback during active candidate returns to last-good and clears candidate ---
clear_state
set_current "$REL_B"
write_state_json "rel-A" "$REL_A" "$REL_C" "rel-B" "$REL_B"
printf 'ready\n' > "$HEALTH_STATE_FILE"
base_environment
expect_success bash "$ROLLBACK_SCRIPT"
[[ "$(current_target)" == "$REL_A" ]] || { printf 'scenario 5: current should be rel-A after reject rollback\n' >&2; exit 1; }
[[ "$(state_field last_good_release_id)" == "rel-A" ]] || { printf 'scenario 5: last_good should remain rel-A\n' >&2; exit 1; }
[[ "$(state_field candidate_release_id)" == "" ]] || { printf 'scenario 5: candidate should be cleared\n' >&2; exit 1; }
[[ "$(cat "$EVENT_LOG")" == *"rejected"* ]] || { printf 'scenario 5: rejected event missing\n' >&2; exit 1; }
append_state "candidate-rejected"
pass "scenario 5: rollback rejects active candidate"

# --- Scenario 5b: late automation health recovers within the bounded retry window ---
clear_state
set_current "$REL_B"
write_state_json "rel-A" "$REL_A" "$REL_C" "rel-B" "$REL_B"
printf 'ready\n' > "$HEALTH_STATE_FILE"
export HEALTH_DELAYED_URL="http://127.0.0.1:8001/health"
export HEALTH_DELAYED_FAILURES=2
export ROLLBACK_HEALTH_TIMEOUT_SECONDS=5
base_environment
expect_success bash "$ROLLBACK_SCRIPT"
[[ "$(wc -l < "$HEALTH_DELAYED_CALLS_FILE")" -eq 2 ]] || \
  fail 'scenario 5b: automation health was not retried until recovery'
[[ "$(state_field candidate_release_id)" == "" ]] || \
  fail 'scenario 5b: candidate should clear only after all health checks succeed'
pass "scenario 5b: late automation health recovers within the bounded retry window"

# --- Scenario 5c: timeout preserves the active candidate state ---
clear_state
set_current "$REL_B"
write_state_json "rel-A" "$REL_A" "$REL_C" "rel-B" "$REL_B"
export ROLLBACK_HEALTH_TIMEOUT_SECONDS=2
base_environment
SECONDS=0
expect_failure bash "$ROLLBACK_SCRIPT"
echo "scenario 5c: rollback took ${SECONDS}s with a 2s health timeout"
# Unbounded per-URL waits would take ~14 URLs x 2s; the shared deadline keeps the
# whole run near timeout + fixed rollback overhead (python/systemd/caddy stubs).
[[ "$SECONDS" -le 6 ]] || fail 'scenario 5c: health timeout exceeded its shared bound'
[[ "$(state_field candidate_release_id)" == "rel-B" ]] || \
  fail 'scenario 5c: timeout cleared the active candidate'
[[ "$(state_field candidate_release_path)" == "$REL_B" ]] || \
  fail 'scenario 5c: timeout changed the active candidate path'
[[ "$(cat "$EVENT_LOG")" == *"rollback_manual_health_fail"* ]] || \
  fail 'scenario 5c: timeout health failure event missing'
pass "scenario 5c: bounded timeout preserves active candidate state"

# --- Scenario 6: missing release argument fails cleanly ---
clear_state
set_current "$REL_A"
write_state_json "rel-A" "$REL_A" ""
base_environment
expect_failure bash "$ROLLBACK_SCRIPT" no-such-release
pass "scenario 6: missing release argument fails cleanly"

# --- Scenario 7: finalize aborts when current symlink does not match candidate ---
clear_state
set_current "$REL_A"
write_state_json "rel-A" "$REL_A" "" "rel-B" "$REL_B"
printf 'ready\n' > "$HEALTH_STATE_FILE"
base_environment
expect_failure bash "$FINALIZE_SCRIPT" --confirm
[[ "$(cat "$EVENT_LOG")" == *"finalize_symlink_mismatch"* ]] || { printf 'scenario 7: finalize_symlink_mismatch event missing\n' >&2; exit 1; }
[[ "$(state_field candidate_release_id)" == "rel-B" ]] || { printf 'scenario 7: candidate should remain rel-B\n' >&2; exit 1; }
pass "scenario 7: finalize aborts on candidate symlink mismatch"

# --- Scenario 8: lock contention blocks concurrent deploy/finalize/rollback ---
clear_state
set_current "$REL_A"
write_state_json "rel-A" "$REL_A" ""
(
  exec 9>"$DEPLOY_LOCK_FILE"
  flock -n 9 || exit 0
  sleep 30
) &
lock_holder=$!
sleep 0.2

base_environment
export TARGET="$REL_B"
expect_failure bash "$DEPLOY_SCRIPT"
base_environment
expect_failure bash "$ROLLBACK_SCRIPT"
base_environment
expect_failure bash "$FINALIZE_SCRIPT" --confirm

kill "$lock_holder" 2>/dev/null || true
wait "$lock_holder" 2>/dev/null || true
pass "scenario 8: lock contention blocks deploy finalize and rollback"

# --- Scenario 9: cleanup protects candidate, last-good, and rollback target ---
clear_state
set_current "$REL_C"
write_state_json "rel-C" "$REL_C" "$REL_B"
touch -m -d "2026-01-01 00:00:00" "$REL_A"
touch -m -d "2026-01-01 00:01:00" "$REL_B"
touch -m -d "2026-01-01 00:02:00" "$REL_C"
touch -m -d "2026-01-01 00:03:00" "$RELEASES/rel-old-1"
touch -m -d "2026-01-02 00:00:00" "$RELEASES/rel-old-2"
touch -m -d "2026-01-02 00:01:00" "$RELEASES/rel-old-3"
printf 'ready\n' > "$HEALTH_STATE_FILE"
base_environment
export DEPLOY_MAX_RELEASES=3
export TARGET="$REL_D"
expect_success bash "$DEPLOY_SCRIPT"
[[ "$(current_target)" == "$REL_D" ]] || { printf 'scenario 9: current should be rel-D\n' >&2; exit 1; }
[[ "$(state_field candidate_release_id)" == "rel-D" ]] || { printf 'scenario 9: candidate should be rel-D\n' >&2; exit 1; }
for protected in "$REL_B" "$REL_C" "$REL_D"; do
  [[ -d "$protected" ]] || { printf 'scenario 9: protected release %s was deleted\n' "$protected" >&2; exit 1; }
done
[[ -d "$RELEASES/rel-old-2" ]] || { printf 'scenario 9: rel-old-2 should survive (within max)\n' >&2; exit 1; }
[[ -d "$RELEASES/rel-old-3" ]] || { printf 'scenario 9: rel-old-3 should survive (within max)\n' >&2; exit 1; }
[[ ! -d "$REL_A" ]] || { printf 'scenario 9: rel-A should have been cleaned up (unprotected)\n' >&2; exit 1; }
[[ ! -d "$RELEASES/rel-old-1" ]] || { printf 'scenario 9: rel-old-1 should have been cleaned up\n' >&2; exit 1; }
append_state "cleanup-protected"
pass "scenario 9: cleanup protects candidate last-good and rollback target"

# --- Scenario 10: clean reproducibility preflight records release identity ---
create_identity_fixture
identity_preflight
identity_record="$IDENTITY_EVIDENCE/release-identity-preflight.json"
[[ -f "$identity_record" ]] || fail 'scenario 10: preflight record was not created'
python3 - "$identity_record" <<'PY'
import json
import sys
from pathlib import Path

record = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if record["release_inputs"] != [{"path": "Infrastructure/caddy/Caddyfile", "sha256": record["release_inputs"][0]["sha256"]}]:
    raise SystemExit("scenario 10: unexpected release input record")
if not record["head_sha"] or not record["release_inputs"][0]["sha256"]:
    raise SystemExit("scenario 10: record lacks committed identity")
PY
pass "scenario 10: clean reproducibility preflight records JSON identity"

# --- Scenario 11: repository drift blocks verify before finalize ---
create_identity_fixture
identity_preflight
identity_deploy
printf 'post-preflight drift\n' >> "$IDENTITY_REPO/Infrastructure/caddy/Caddyfile"
expect_failure identity_finalize_if_verified
[[ ! -f "$IDENTITY_EVENT_LOG" || "$(cat "$IDENTITY_EVENT_LOG")" != *'"event":"finalized"'* ]] || \
  fail 'scenario 11: finalize ran after verify rejected repository drift'
[[ "$(python3 "$STATE_HELPER" read "$IDENTITY_STATE_FILE" candidate_release_id)" == "candidate" ]] || \
  fail 'scenario 11: rejected verification cleared the active candidate'
pass "scenario 11: modified release input blocks verification before finalize"

# --- Scenario 12: untracked allowlisted input blocks preflight ---
create_identity_fixture
printf 'untracked release input\n' > "$IDENTITY_REPO/Infrastructure/untracked.txt"
printf 'Infrastructure/untracked.txt\n' > "$IDENTITY_REPO/untracked-inputs.txt"
set +e
PATH="$ORIGINAL_PATH" python3 "$IDENTITY_VERIFIER" preflight \
  --repo-root "$IDENTITY_REPO" \
  --allowlist "$IDENTITY_REPO/untracked-inputs.txt" \
  --evidence-dir "$IDENTITY_EVIDENCE" > "$IDENTITY_EVIDENCE/untracked-preflight.out" 2>&1
preflight_status=$?
set -e
[[ "$preflight_status" -ne 0 ]] || fail 'scenario 12: untracked release input passed preflight'
grep -Fq 'Infrastructure/untracked.txt' "$IDENTITY_EVIDENCE/untracked-preflight.out" || \
  fail 'scenario 12: untracked input was not identified'
pass "scenario 12: untracked allowlisted input blocks preflight"

# --- Scenario 13: altered release byte blocks verification before finalize ---
create_identity_fixture
identity_preflight
identity_deploy
python3 - "$IDENTITY_TARGET/Infrastructure/caddy/Caddyfile" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
contents = bytearray(path.read_bytes())
contents[0] ^= 1
path.write_bytes(contents)
PY
expect_failure identity_finalize_if_verified
[[ ! -f "$IDENTITY_EVENT_LOG" || "$(cat "$IDENTITY_EVENT_LOG")" != *'"event":"finalized"'* ]] || \
  fail 'scenario 13: finalize ran after release-byte mismatch'
pass "scenario 13: one altered release byte blocks verification before finalize"

# --- Scenario 14: unrelated dirty files remain outside the identity artifact set ---
create_identity_fixture
printf 'unrelated scratch data\n' > "$IDENTITY_REPO/unrelated-dirty.txt"
identity_preflight
identity_deploy
identity_finalize_if_verified
python3 - "$IDENTITY_EVIDENCE/release-identity-preflight.json" <<'PY'
import json
import sys
from pathlib import Path

record = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if record["porcelain"]:
    raise SystemExit("scenario 14: unrelated dirty file entered the release porcelain record")
if any("unrelated-dirty.txt" in str(item) for item in record["release_inputs"]):
    raise SystemExit("scenario 14: unrelated dirty file entered release inputs")
PY
pass "scenario 14: unrelated dirty file stays outside identity artifacts"

if [[ ! -d "$REL_A/Infrastructure" ]]; then
  mkdir -p "$REL_A"
  cp -a "$ARCHIVE_ROOT/Infrastructure" "$REL_A/"
  printf '# release Caddy configuration: rel-A\n' > "$REL_A/Infrastructure/caddy/Caddyfile"
  printf '\n# fixture release: rel-A\n' >> "$REL_A/Infrastructure/cea-backend.service"
  printf '{"release_id":"rel-A","git_sha":"%s","alembic_heads":[],"migration_head":null}\n' \
    "$EXPECTED_GIT_SHA" > "$REL_A/deploy_manifest.json"
fi


# --- Deploy hardening: dirty tracked inputs are rejected before release work ---
clear_state
set_current "$REL_A"
write_state_json "rel-A" "$REL_A" "$REL_C"
base_environment
export TARGET="$REL_B"
export DEPLOY_TEST_DIRTY_TRACKED=1
expect_failure bash "$DEPLOY_SCRIPT"
grep -Fq 'dirty_tracked_inputs' "$EVENT_LOG" || fail 'dirty tracked input was not reported'
[[ "$(current_target)" == "$REL_A" ]] || fail 'dirty tracked input changed current'
[[ "$(cat "$EVENT_LOG")" != *"db_migrate plan"* ]] || fail 'dirty tracked input reached migration planning'
pass 'dirty tracked inputs block deploy before migrations'

# Untracked inputs are deliberately ignored by the release-source guard.
clear_state
set_current "$REL_A"
write_state_json "rel-A" "$REL_A" "$REL_C"
printf 'ready\n' > "$HEALTH_STATE_FILE"
base_environment
export TARGET="$REL_B"
export DEPLOY_TEST_UNTRACKED_ONLY=1
expect_success bash "$DEPLOY_SCRIPT"
[[ "$(current_target)" == "$REL_B" ]] || fail 'untracked-only status blocked the release'
pass 'untracked-only release source status is allowed'

# --- Deploy hardening: lint is read-only and blocks before migrations ---
clear_state
set_current "$REL_A"
write_state_json "rel-A" "$REL_A" "$REL_C"
base_environment
export TARGET="$REL_B"
export DEPLOY_TEST_LINT_FAILURE=1
expect_failure bash "$DEPLOY_SCRIPT"
grep -Fq 'lint_failed' "$EVENT_LOG" || fail 'lint failure was not reported'
[[ "$(current_target)" == "$REL_A" ]] || fail 'lint failure changed current'
[[ "$(cat "$EVENT_LOG")" != *"db_migrate plan"* ]] || fail 'lint failure reached migration planning'
pass 'lint failure blocks candidate activation'

# --- Deploy hardening: downtime migration requires a fresh matching rehearsal ---
clear_state
set_current "$REL_A"
write_state_json "rel-A" "$REL_A" "$REL_C"
printf 'ready\n' > "$HEALTH_STATE_FILE"
base_environment
export TARGET="$REL_B"
export DEPLOY_TEST_DOWNTIME=1
expect_failure bash "$DEPLOY_SCRIPT"
grep -Fq 'rehearsal_missing' "$EVENT_LOG" || fail 'missing rehearsal was not reported'
[[ "$(cat "$EVENT_LOG")" != *"pg_dump -Fc"* ]] || fail 'missing rehearsal started a backup'
[[ "$(cat "$EVENT_LOG")" != *"systemctl stop "* ]] || fail 'missing rehearsal stopped services'
[[ "$(cat "$EVENT_LOG")" != *"db_migrate apply"* ]] || fail 'missing rehearsal applied migrations'
pass 'downtime migrations are blocked without a fresh matching rehearsal'

# --- Deploy hardening: downtime order, schema-aware rollback, and Caddy backup ---
clear_state
set_current "$REL_A"
write_state_json "rel-A" "$REL_A" "$REL_C"
printf 'ready\n' > "$HEALTH_STATE_FILE"
base_environment
export TARGET="$REL_B"
export DEPLOY_TEST_DOWNTIME=1
printf '05_relay_observation\n' > "$ALEMBIC_HEADS_FILE"
printf '{"release_id":"rel-A","git_sha":"%s"}\n' "$EXPECTED_GIT_SHA" > "$REL_A/deploy_manifest.json"
mkdir -p "$REHEARSAL_DIR"
printf '{"set_sha256":"test-set-sha","result":"pass"}\n' > "$REHEARSAL_DIR/test-set-sha.json"
expect_success bash "$DEPLOY_SCRIPT"
assert_event_before 'systemctl stop grow-archive-service.service' 'db_migrate apply --json --allow-downtime'
assert_event_before 'db_migrate apply --json --allow-downtime' 'deploy_symlink_switched'
assert_event_before 'systemctl restart automation-service.service' 'curl http://127.0.0.1:8080/'
[[ "$(current_target)" == "$REL_B" ]] || fail 'rehearsed downtime deploy did not activate the candidate'
[[ -s "$MIGRATION_BACKUP_DIR/pre-rel-B.dump" ]] || fail 'downtime deploy did not retain its pre-migration dump'
python3 - "$REL_B/deploy_manifest.json" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert manifest["migration_head"] == "0001_test_migration", manifest
assert manifest["alembic_heads"] == ["05_relay_observation"], manifest
assert manifest["previous_schema"] == {
    "migration_head": None,
    "alembic_heads": ["05_relay_observation"],
}, manifest
PY
[[ "$(cat "$CADDY_FILE")" == "$(cat "$REL_B/Infrastructure/caddy/Caddyfile")" ]] || \
  fail 'candidate Caddyfile was not installed'
[[ -s "$SYSTEMD_BACKUP_ROOT/rel-B/Caddyfile" ]] || fail 'pre-deploy Caddyfile was not backed up'
cmp -s "$SYSTEMD_BACKUP_ROOT/rel-B/Caddyfile" "$REL_A/Infrastructure/caddy/Caddyfile" || \
  fail 'Caddyfile backup does not match the previous release'
cmp -s "$SYSTEMD_BACKUP_ROOT/rel-B/cea-backend.service" "$REL_A/Infrastructure/cea-backend.service" || \
  fail 'replaced systemd unit was not backed up'
expect_failure bash "$ROLLBACK_SCRIPT"
grep -Fq 'schema_newer_than_target' "$EVENT_LOG" || fail 'manual rollback did not block a newer schema'
[[ "$(current_target)" == "$REL_B" ]] || fail 'schema-blocked rollback changed current'
expect_success bash "$ROLLBACK_SCRIPT" --with-down-migrations
assert_event_before 'db_migrate down --to none' 'rollback_manual_symlink'
[[ "$(current_target)" == "$REL_A" ]] || fail 'schema-aware manual rollback did not restore the target release'
[[ "$(cat "$DB_HEAD_FILE")" == "null" ]] || fail 'schema-aware manual rollback did not reverse the SQL migration'
cmp -s "$CADDY_FILE" "$REL_A/Infrastructure/caddy/Caddyfile" || fail 'rollback did not restore the previous Caddyfile'
cmp -s "$SYSTEMD_DIR/cea-backend.service" "$REL_A/Infrastructure/cea-backend.service" || \
  fail 'rollback did not restore the replaced systemd unit'
[[ "$(state_field candidate_release_id)" == "" ]] || fail 'candidate state was not cleared after rollback'
pass 'rehearsed downtime deploy orders stop/apply/switch and rollback restores schema, units, and Caddy'

# --- Deploy hardening: failed health after downtime migrates down before switching back ---
clear_state
set_current "$REL_A"
write_state_json "rel-A" "$REL_A" "$REL_C"
base_environment
export TARGET="$REL_B"
export DEPLOY_TEST_DOWNTIME=1
mkdir -p "$REHEARSAL_DIR"
printf '{"set_sha256":"test-set-sha","result":"pass"}\n' > "$REHEARSAL_DIR/test-set-sha.json"
expect_failure bash "$DEPLOY_SCRIPT"
assert_event_before 'db_migrate down --to none' 'rollback_auto_symlink'
[[ "$(current_target)" == "$REL_A" ]] || fail 'health failure after downtime did not restore the prior release'
[[ "$(cat "$DB_HEAD_FILE")" == "null" ]] || fail 'automatic rollback did not reverse the SQL migration'
pass 'automatic health rollback runs down migrations before switching releases'

# --- Deploy hardening: absent units are skipped while remaining services restart ---
clear_state
set_current "$REL_A"
write_state_json "rel-A" "$REL_A" "$REL_C"
printf 'ready\n' > "$HEALTH_STATE_FILE"
base_environment
export TARGET="$REL_B"
export DEPLOY_TEST_NOT_FOUND_UNIT=can-processor.service
expect_success bash "$DEPLOY_SCRIPT"
grep -Fq 'load_state_not_found' "$EVENT_LOG" || fail 'not-found unit was not reported as skipped'
[[ "$(cat "$EVENT_LOG")" != *"systemctl restart can-processor.service"* ]] || fail 'not-found unit was restarted'
[[ "$(cat "$EVENT_LOG")" == *"systemctl restart grow-archive-service.service"* ]] || \
  fail 'a not-found unit prevented later service restarts'
pass 'not-found units are skipped without stopping the restart loop'

# --- Deploy hardening: Caddy install failure rolls back stopped services and schema ---
clear_state
set_current "$REL_A"
write_state_json "rel-A" "$REL_A" "$REL_C"
printf 'ready\n' > "$HEALTH_STATE_FILE"
base_environment
export TARGET="$REL_B"
export DEPLOY_TEST_DOWNTIME=1
export DEPLOY_TEST_FAIL_CADDY_INSTALL=1
mkdir -p "$REHEARSAL_DIR"
printf '{"set_sha256":"test-set-sha","result":"pass"}\n' > "$REHEARSAL_DIR/test-set-sha.json"
expect_failure bash "$DEPLOY_SCRIPT"
grep -Fq 'caddy_install_failed' "$EVENT_LOG" || fail 'Caddy install failure was not reported'
assert_event_before 'db_migrate apply --json --allow-downtime' 'db_migrate down --to none'
assert_event_before 'db_migrate down --to none' 'rollback_auto_symlink'
[[ "$(current_target)" == "$REL_A" ]] || fail 'Caddy install failure left the candidate active'
[[ "$(cat "$DB_HEAD_FILE")" == "null" ]] || fail 'Caddy install failure did not reverse migrations'
cmp -s "$CADDY_FILE" "$REL_A/Infrastructure/caddy/Caddyfile" || fail 'Caddy install failure did not restore Caddy'
pass 'Caddy install failure triggers automatic schema and service rollback'

# --- Rollback hardening: an explicit non-adjacent target unwinds every newer backup ---
clear_state
set_current "$REL_C"
write_state_json "rel-C" "$REL_C" "$REL_B"
printf 'ready\n' > "$HEALTH_STATE_FILE"
base_environment
printf '{"release_id":"rel-A","git_sha":"%s","alembic_heads":[],"migration_head":null}\n' \
  "$EXPECTED_GIT_SHA" > "$REL_A/deploy_manifest.json"
mkdir -p "$SYSTEMD_BACKUP_ROOT/rel-B" "$SYSTEMD_BACKUP_ROOT/rel-C"
cp "$REL_A/Infrastructure/caddy/Caddyfile" "$SYSTEMD_BACKUP_ROOT/rel-B/Caddyfile"
cp "$REL_A/Infrastructure/cea-backend.service" "$SYSTEMD_BACKUP_ROOT/rel-B/cea-backend.service"
: > "$SYSTEMD_BACKUP_ROOT/rel-B/created.txt"
cp "$REL_B/Infrastructure/caddy/Caddyfile" "$SYSTEMD_BACKUP_ROOT/rel-C/Caddyfile"
cp "$REL_B/Infrastructure/cea-backend.service" "$SYSTEMD_BACKUP_ROOT/rel-C/cea-backend.service"
printf 'introduced-by-c.service\n' > "$SYSTEMD_BACKUP_ROOT/rel-C/created.txt"
printf 'C-only unit\n' > "$SYSTEMD_DIR/introduced-by-c.service"
expect_success bash "$ROLLBACK_SCRIPT" "$REL_A"
[[ "$(current_target)" == "$REL_A" ]] || fail 'non-adjacent rollback did not switch to its requested target'
cmp -s "$CADDY_FILE" "$REL_A/Infrastructure/caddy/Caddyfile" || fail 'non-adjacent rollback did not restore target Caddy'
cmp -s "$SYSTEMD_DIR/cea-backend.service" "$REL_A/Infrastructure/cea-backend.service" || \
  fail 'non-adjacent rollback did not unwind intervening unit files'
[[ ! -e "$SYSTEMD_DIR/introduced-by-c.service" ]] || fail 'non-adjacent rollback did not remove an intervening created unit'
[[ "$(grep -c 'systemctl restart caddy' "$EVENT_LOG")" -eq 2 ]] || \
  fail 'non-adjacent rollback did not restore each intervening Caddy backup'
pass 'explicit non-adjacent rollback unwinds configuration backups newest-first'

printf ']' >> "$EVIDENCE_STATES"

EVIDENCE_DIR="$SANDBOX/evidence"
mkdir -p "$EVIDENCE_DIR"
cp "$EVIDENCE_STATES" "$EVIDENCE_DIR/deploy-state.json"

{
  printf 'Failure scenarios exercised (all failed cleanly and preserved rollback state):\n'
  printf '  - deploy_blocked on second candidate\n'
  printf '  - candidate-rejected on health failure\n'
  printf '  - missing release argument\n'
  printf '  - finalize_symlink_mismatch\n'
  printf '  - lock contention on deploy, rollback, finalize\n'
  printf 'Cleanup scenario: rel-A and rel-old-1 deleted, protected releases (rel-B, rel-C, rel-D) plus rel-old-2/rel-old-3 retained.\n'
} > "$EVIDENCE_DIR/failure.txt"

printf 'deploy-candidate sandbox test passed\n'
