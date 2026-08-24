#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
VERIFIER="$REPO_ROOT/Infrastructure/scripts/verify_release_identity.py"
PRODUCTION_ALLOWLIST="$REPO_ROOT/.omo/evidence/monitoring-pipeline-radical-optimization/task-1-monitoring-pipeline-radical-optimization-allowlist.txt"

fixture_root="$(mktemp -d)"
trap 'rm -rf "$fixture_root"' EXIT

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

expect_success() {
  local name="$1"
  local output="$2"
  shift 2

  set +e
  "$@" >"$output" 2>&1
  local rc=$?
  set -e
  [[ "$rc" -eq 0 ]] || fail "$name expected success, got exit=$rc"
  printf 'PASS: %s (exit=%s)\n' "$name" "$rc"
}

expect_failure() {
  local name="$1"
  local output="$2"
  shift 2

  set +e
  "$@" >"$output" 2>&1
  local rc=$?
  set -e
  [[ "$rc" -ne 0 ]] || fail "$name expected failure, got exit=$rc"
  printf 'PASS: %s (exit=%s)\n' "$name" "$rc"
}

assert_contains() {
  local needle="$1"
  local file="$2"
  grep -Fq -- "$needle" "$file" || fail "expected '$needle' in $file"
}

assert_not_contains() {
  local needle="$1"
  local file="$2"
  ! grep -Fq -- "$needle" "$file" || fail "did not expect '$needle' in $file"
}

repo="$fixture_root/repo"
mkdir -p "$repo/Infrastructure/caddy"
printf 'example release input\n' >"$repo/Infrastructure/caddy/Caddyfile"
printf 'Infrastructure/caddy/Caddyfile\n' >"$repo/release-inputs.txt"

git init -q "$repo"
git -C "$repo" config user.email fixture@example.test
git -C "$repo" config user.name fixture
git -C "$repo" add Infrastructure/caddy/Caddyfile release-inputs.txt
git -C "$repo" commit -qm 'fixture release input'

cp "$PRODUCTION_ALLOWLIST" "$fixture_root/production-allowlist.txt"
assert_contains 'Infrastructure/caddy/Caddyfile' "$fixture_root/production-allowlist.txt"

clean_evidence="$fixture_root/evidence-clean"
mkdir "$clean_evidence"
clean_output="$fixture_root/clean-preflight.out"
expect_success "clean preflight" "$clean_output" \
  python3 "$VERIFIER" preflight --repo-root "$repo" --allowlist "$repo/release-inputs.txt" --evidence-dir "$clean_evidence" --check-cmd true
record="$clean_evidence/release-identity-preflight.json"
[[ -f "$record" ]] || fail 'preflight record was not created'

release_root="$fixture_root/release"
mkdir "$release_root"
cp -R "$repo/Infrastructure" "$release_root/Infrastructure"
expect_success "matching release tree verify" "$fixture_root/verify-happy.out" \
  python3 "$VERIFIER" verify --repo-root "$repo" --record "$record" --release-root "$release_root"

printf 'modified\n' >>"$repo/Infrastructure/caddy/Caddyfile"
modified_evidence="$fixture_root/evidence-modified"
mkdir "$modified_evidence"
modified_output="$fixture_root/modified.out"
expect_failure "modified tracked input" "$modified_output" \
  python3 "$VERIFIER" preflight --repo-root "$repo" --allowlist "$repo/release-inputs.txt" --evidence-dir "$modified_evidence"
assert_contains 'Infrastructure/caddy/Caddyfile' "$modified_output"
git -C "$repo" restore Infrastructure/caddy/Caddyfile

printf 'untracked input\n' >"$repo/Infrastructure/untracked.txt"
printf 'Infrastructure/untracked.txt\n' >"$repo/untracked-inputs.txt"
untracked_evidence="$fixture_root/evidence-untracked"
mkdir "$untracked_evidence"
untracked_output="$fixture_root/untracked.out"
expect_failure "untracked input" "$untracked_output" \
  python3 "$VERIFIER" preflight --repo-root "$repo" --allowlist "$repo/untracked-inputs.txt" --evidence-dir "$untracked_evidence"
assert_contains 'Infrastructure/untracked.txt' "$untracked_output"
rm "$repo/Infrastructure/untracked.txt"

printf 'tampered release byte\n' >>"$release_root/Infrastructure/caddy/Caddyfile"
tampered_output="$fixture_root/tampered.out"
expect_failure "tampered release-root byte" "$tampered_output" \
  python3 "$VERIFIER" verify --repo-root "$repo" --record "$record" --release-root "$release_root"
assert_contains 'Infrastructure/caddy/Caddyfile' "$tampered_output"

outside="$fixture_root/outside"
mkdir "$outside"
ln -s "$outside" "$repo/escape"
printf 'escape/value.txt\n' >"$repo/symlink-inputs.txt"
symlink_evidence="$fixture_root/evidence-symlink"
mkdir "$symlink_evidence"
symlink_output="$fixture_root/symlink.out"
expect_failure "symlink escape" "$symlink_output" \
  python3 "$VERIFIER" preflight --repo-root "$repo" --allowlist "$repo/symlink-inputs.txt" --evidence-dir "$symlink_evidence"
assert_contains 'escape/value.txt' "$symlink_output"

secret_value='fixture-password-must-not-leak'
printf 'password=%s\n' "$secret_value" >"$repo/Infrastructure/secret.env"
printf 'Infrastructure/secret.env\n' >"$repo/secret-inputs.txt"
git -C "$repo" add Infrastructure/secret.env
git -C "$repo" commit -qm 'fixture secret input'
secret_evidence="$fixture_root/evidence-secret"
mkdir "$secret_evidence"
secret_output="$fixture_root/secret.out"
expect_success "checked-file secret redaction" "$secret_output" \
  python3 "$VERIFIER" preflight --repo-root "$repo" --allowlist "$repo/secret-inputs.txt" --evidence-dir "$secret_evidence" --check-cmd "sh -c 'cat Infrastructure/secret.env'"
assert_not_contains "$secret_value" "$secret_output"
assert_contains '[REDACTED]' "$secret_output"

marker="$fixture_root/check-ran"
fix_evidence="$fixture_root/evidence-fix"
mkdir "$fix_evidence"
fix_output="$fixture_root/fix.out"
expect_failure "--fix command rejected before execution" "$fix_output" \
  python3 "$VERIFIER" preflight --repo-root "$repo" --allowlist "$repo/secret-inputs.txt" --evidence-dir "$fix_evidence" --check-cmd="sh -c 'touch $marker' --fix"
[[ ! -e "$marker" ]] || fail '--fix command executed despite rejection'

check_failure_evidence="$fixture_root/evidence-check-failure"
mkdir "$check_failure_evidence"
check_failure_output="$fixture_root/check-failure.out"
expect_failure "failing check command" "$check_failure_output" \
  python3 "$VERIFIER" preflight --repo-root "$repo" --allowlist "$repo/secret-inputs.txt" --evidence-dir "$check_failure_evidence" --check-cmd false

printf 'PASS: all 9 release identity scenarios\n'
