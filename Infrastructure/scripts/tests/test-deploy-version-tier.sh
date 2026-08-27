#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPOSITORY_ROOT="$(cd "$SCRIPT_DIRECTORY/../../.." && pwd)"
readonly DEPLOY_SCRIPT="$REPOSITORY_ROOT/deploy.sh"
readonly SANDBOX="$(mktemp -d)"
readonly BIN_DIRECTORY="$SANDBOX/bin"
readonly FIXTURE_SOURCE="$SANDBOX/source"
readonly DEPLOY_ROOT="$SANDBOX/projectcea"
readonly RELEASES="$DEPLOY_ROOT/releases"
readonly STATE_DIRECTORY="$SANDBOX/state"
readonly EVENT_LOG="$SANDBOX/events.log"
readonly NPM_LOG="$SANDBOX/npm.log"

cleanup() {
  rm -rf "$SANDBOX"
}
trap cleanup EXIT

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

assert_contains() {
  local needle="$1"
  local path="$2"
  grep -Fq -- "$needle" "$path" || fail "expected '$needle' in $path"
}

assert_not_exists() {
  [[ ! -e "$1" ]] || fail "unexpected deployment side effect: $1"
}

mkdir -p "$BIN_DIRECTORY" "$RELEASES" "$STATE_DIRECTORY"
cp -R "$REPOSITORY_ROOT/Infrastructure" "$FIXTURE_SOURCE"

cat > "$FIXTURE_SOURCE/scripts/install-sudoers.sh" <<'INSTALL_SUDOERS'
#!/usr/bin/env bash
exit 0
INSTALL_SUDOERS

cat > "$BIN_DIRECTORY/sudo" <<'SUDO'
#!/usr/bin/env bash
exec "$@"
SUDO

cat > "$BIN_DIRECTORY/chown" <<'CHOWN'
#!/usr/bin/env bash
exit 0
CHOWN

cat > "$BIN_DIRECTORY/git" <<'GIT'
#!/usr/bin/env bash
printf 'abc123\n'
GIT

cat > "$BIN_DIRECTORY/ruff" <<'RUFF'
#!/usr/bin/env bash
exit 0
RUFF

cat > "$BIN_DIRECTORY/rsync" <<'RSYNC'
#!/usr/bin/env bash
set -euo pipefail

source_path="${@: -2:1}"
target_path="${@: -1}"
python3 - "$source_path/frontend/package.json" "$EXPECTED_VERSION" <<'PY'
import json
import sys
from pathlib import Path

if json.loads(Path(sys.argv[1]).read_text())['version'] != sys.argv[2]:
    raise SystemExit('release metadata was not updated before rsync')
PY
printf 'rsync %s\n' "$EXPECTED_VERSION" >> "$EVENT_LOG"
mkdir -p "$target_path"
cp -R "$source_path/." "$target_path/"
RSYNC

cat > "$BIN_DIRECTORY/npm" <<'NPM'
#!/usr/bin/env bash
set -euo pipefail

printf '%s %s\n' "$PWD" "$*" >> "$NPM_LOG"
case "$1" in
  run)
    tier="${2#release:}"
    [[ "$2" == release:* ]] || exit 1
    [[ "${NPM_RELEASE_FAILURE:-0}" != 1 ]] || exit 42
    python3 - "$PWD/package.json" "$PWD/package-lock.json" "$tier" <<'PY'
import json
import sys
from pathlib import Path

version = f'9.9.9-{sys.argv[3]}'
for raw_path in sys.argv[1:3]:
    path = Path(raw_path)
    payload = json.loads(path.read_text())
    payload['version'] = version
    if 'packages' in payload and '' in payload['packages']:
        payload['packages']['']['version'] = version
    path.write_text(json.dumps(payload) + '\n')
PY
    ;;
  ci)
    ;;
  *)
    [[ "$1" == build ]] || exit 1
    python3 - "$PWD/package.json" "$EXPECTED_VERSION" <<'PY'
import json
import sys
from pathlib import Path

if json.loads(Path(sys.argv[1]).read_text())['version'] != sys.argv[2]:
    raise SystemExit('candidate frontend was built before the bumped metadata copied')
PY
    printf 'build %s\n' "$EXPECTED_VERSION" >> "$EVENT_LOG"
    ;;
esac
NPM

cat > "$BIN_DIRECTORY/systemctl" <<'SYSTEMCTL'
#!/usr/bin/env bash
exit 0
SYSTEMCTL

cat > "$BIN_DIRECTORY/curl" <<'CURL'
#!/usr/bin/env bash
set -euo pipefail

output=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o) output="$2"; shift 2 ;;
    -w|-m) shift 2 ;;
    *) shift ;;
  esac
done
printf '{"status":"ready"}\n' > "$output"
printf '200'
CURL

chmod 0700 "$BIN_DIRECTORY"/* "$FIXTURE_SOURCE/scripts/install-sudoers.sh"

deploy() {
  env \
    PATH="$BIN_DIRECTORY:$PATH" \
    SOURCE="$FIXTURE_SOURCE" \
    DEPLOY_ROOT="$DEPLOY_ROOT" \
    DEPLOY_STATE_DIR="$STATE_DIRECTORY" \
    DEPLOY_LOCK_FILE="$SANDBOX/deploy.lock" \
    DEPLOY_LOG="$EVENT_LOG" \
    DEPLOY_RELEASES="$RELEASES" \
    DEPLOY_CURRENT="$DEPLOY_ROOT/current" \
    DEPLOY_STATE_JSON="$STATE_DIRECTORY/deploy_state.json" \
    DEPLOY_HEALTH_ATTEMPTS=1 \
    EXPECTED_VERSION="${EXPECTED_VERSION:-}" \
    bash "$DEPLOY_SCRIPT" "$@"
}

expect_invalid_arguments() {
  local name="$1"
  shift
  local output="$SANDBOX/$name.out"

  set +e
  deploy "$@" >"$output" 2>&1
  local status=$?
  set -e
  [[ $status -ne 0 ]] || fail "$name unexpectedly succeeded"
  assert_contains 'Usage:' "$output"
  assert_not_exists "$SANDBOX/deploy.lock"
  assert_not_exists "$EVENT_LOG"
  assert_not_exists "$NPM_LOG"
}

expect_invalid_arguments invalid-tier wrong
expect_invalid_arguments multiple-tiers patch minor

run_no_argument_deploy() {
  EXPECTED_VERSION="1.0.0"
  rm -f "$STATE_DIRECTORY/deploy_state.json" "$EVENT_LOG" "$NPM_LOG"
  rm -f "$DEPLOY_ROOT/current"

  deploy
  assert_not_exists "$NPM_LOG"
  assert_contains 'build 1.0.0' "$EVENT_LOG"
}

run_tiered_deploy() {
  local tier="$1"
  local target="$RELEASES/$tier"
  EXPECTED_VERSION="9.9.9-$tier"
  rm -f "$STATE_DIRECTORY/deploy_state.json" "$EVENT_LOG" "$NPM_LOG"
  rm -f "$DEPLOY_ROOT/current"
  rm -rf "$target"

  deploy "$tier"
  assert_contains "$FIXTURE_SOURCE/frontend npm run release:$tier" "$NPM_LOG"
  assert_contains "rsync $EXPECTED_VERSION" "$EVENT_LOG"
  assert_contains "build $EXPECTED_VERSION" "$EVENT_LOG"
  assert_contains "$EXPECTED_VERSION" "$target/Infrastructure/frontend/package.json"
  assert_contains "$EXPECTED_VERSION" "$target/Infrastructure/frontend/package-lock.json"
}

run_no_argument_deploy
run_tiered_deploy patch
run_tiered_deploy minor
run_tiered_deploy major

printf 'deploy version tier sandbox test passed\n'
