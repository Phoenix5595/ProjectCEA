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

assert_script_hash() {
  local name="$1"
  local path="$2"
  local expected="$3"
  local actual

  actual="$(sha256sum "$path" | cut -d ' ' -f 1)"
  [[ "$actual" == "$expected" ]] || fail "$name script hash changed: expected $expected, got $actual"
}

assert_script_hash "deploy" "$DEPLOY_SCRIPT" "8d10bb20f985df5145a103068ab83158599d499d2b1acf243817c3b4e5fb2709"
assert_script_hash "finalize" "$FINALIZE_SCRIPT" "6503e11745e1f7c658d8fbc3bc08c630aae7b33d3d492671c0d8eab757bdd32e"
assert_script_hash "rollback" "$ROLLBACK_SCRIPT" "16203c049ebcc6d3da83222d4c65ca1f376a92c2123736a8cad24b6d7c6098de"
pass "deploy script hashes match expected sha256 values"

mkdir -p "$BIN_DIRECTORY" "$RELEASES" "$STATE_DIR"

for rel in "$REL_A" "$REL_B" "$REL_C" "$REL_D"; do
  mkdir -p "$rel/Infrastructure"
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
esac
exec "$@"
SUDO

cat > "$BIN_DIRECTORY/ruff" <<'RUFF'
#!/usr/bin/env bash
exit 0
RUFF

cat > "$BIN_DIRECTORY/rsync" <<'RSYNC'
#!/usr/bin/env bash
set -euo pipefail

source_path="${@: -2:1}"
destination_path="${@: -1}"
mkdir -p "$destination_path/frontend"
cp -a "$source_path/frontend/." "$destination_path/frontend/"
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
  daemon-reload)
    ;;
  restart|start|stop)
    printf 'systemctl %s\n' "$*" >> "$EVENT_LOG"
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

outfile=""
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
printf '%s\n' "$body" > "$outfile"
printf '%s' "$code"
CURL

cat > "$BIN_DIRECTORY/git" <<'GIT'
#!/usr/bin/env bash
printf 'abc123\n'
GIT

chmod 0700 "$BIN_DIRECTORY"/*

base_environment() {
  export PATH="$BIN_DIRECTORY:$PATH"
  export SOURCE="/home/antoine/ProjectCEA"
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
  export HEALTH_STATE_FILE
  export HEALTH_DELAYED_CALLS_FILE
  export EVENT_LOG
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
end = source.index("\n}\n\nrestart_services", start) + 3
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

clear_state() {
  rm -f "$STATE_FILE" "$EVENT_LOG" "$PACKAGE_LOG" "$HEALTH_STATE_FILE" "$HEALTH_DELAYED_CALLS_FILE"
  rm -f "$STATE_DIR/deploy-packages.log"
  rm -f "$CURRENT"
  unset HEALTH_DELAYED_URL HEALTH_DELAYED_FAILURES ROLLBACK_HEALTH_TIMEOUT_SECONDS
}

set_current() {
  ln -sfn "$1" "$CURRENT"
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
python3 - "$CURRENT/deploy_manifest.json" <<'PY'
import json
import sys
from pathlib import Path

services = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["services"]
if "monitoring-service" not in services:
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
[[ "$SECONDS" -le 3 ]] || fail 'scenario 5c: health timeout exceeded its shared bound'
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
