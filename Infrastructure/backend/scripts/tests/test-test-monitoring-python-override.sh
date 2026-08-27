#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIRECTORY
RUNNER="$SCRIPT_DIRECTORY/../test-monitoring.sh"
readonly RUNNER

temporary_directory="$(mktemp -d)"
trap 'rm -rf -- "$temporary_directory"' EXIT
override_log="$temporary_directory/override.log"
override_python="$temporary_directory/python"

cat >"$override_python" <<'PYTHON'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$MONITORING_OVERRIDE_LOG"
PYTHON
chmod +x "$override_python"

MONITORING_OVERRIDE_LOG="$override_log" MONITORING_PYTHON="$override_python" \
  bash "$RUNNER" --pure


if MONITORING_PYTHON="$temporary_directory/missing-python" bash "$RUNNER" --pure; then
  printf 'expected an invalid MONITORING_PYTHON to fail\n' >&2
  exit 1
fi
