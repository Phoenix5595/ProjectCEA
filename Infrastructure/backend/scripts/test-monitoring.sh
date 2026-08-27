#!/usr/bin/env bash
set -euo pipefail

MONITORING_RUNNER_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly MONITORING_RUNNER_DIRECTORY
BACKEND_DIRECTORY="$(cd -- "$MONITORING_RUNNER_DIRECTORY/.." && pwd)"
readonly BACKEND_DIRECTORY
HARNESS_PATH="$(cd -- "$BACKEND_DIRECTORY/../database/tests" && pwd)/test-monitoring-read-models.sh"
readonly HARNESS_PATH
if [[ -n "${MONITORING_PYTHON:-}" ]]; then
  [[ -x "$MONITORING_PYTHON" ]] || {
    printf 'backend monitoring tests: MONITORING_PYTHON is not executable: %s\n' "$MONITORING_PYTHON" >&2
    exit 69
  }
  PYTHON_COMMAND="$MONITORING_PYTHON"
elif [[ -x "$BACKEND_DIRECTORY/.venv/bin/python" ]]; then
  PYTHON_COMMAND="$BACKEND_DIRECTORY/.venv/bin/python"
else
  PYTHON_COMMAND="python3"
fi
readonly PYTHON_COMMAND

monitoring_runner_fail() {
  printf 'backend monitoring tests: %s\n' "$1" >&2
  return 1
}

monitoring_runner_usage() {
  cat <<'USAGE'
Usage: test-monitoring.sh (--pure | --integration) [--case NAME] [PYTEST_ARGS...]

  --pure         Run monitoring unit tests without disposable services.
  --integration  Run integration tests through with_monitoring_test_db.
USAGE
}

run_pure_tests() {
  local -a pytest_arguments=("$@")

  cd "$BACKEND_DIRECTORY"
  "$PYTHON_COMMAND" -m pytest -q app/tests/monitoring -m 'not integration' "${pytest_arguments[@]}"
}

run_integration_tests() {
  local -a pytest_arguments=("$@")
  local harness_status

  [[ -f "$HARNESS_PATH" ]] || {
    monitoring_runner_fail "database harness is missing: $HARNESS_PATH"
    return 69
  }
  [[ "${MONITORING_DOCKER_UNAVAILABLE:-0}" != '1' ]] || {
    monitoring_runner_fail 'integration harness is unavailable (MONITORING_DOCKER_UNAVAILABLE=1)'
    return 69
  }

  # shellcheck source=../../database/tests/test-monitoring-read-models.sh
  source "$HARNESS_PATH"
  declare -F with_monitoring_test_db >/dev/null || {
    monitoring_runner_fail 'database harness does not define with_monitoring_test_db'
    return 69
  }

  cd "$BACKEND_DIRECTORY"
  if with_monitoring_test_db \
    "$PYTHON_COMMAND" -m pytest -q app/tests/monitoring -m integration "${pytest_arguments[@]}"; then
    return 0
  else
    harness_status=$?
  fi

  if [[ "${MONITORING_REQUIRE_INTEGRATION:-0}" == '1' ]]; then
    monitoring_runner_fail \
      'required integration harness failed before pytest received a disposable database URL'
  fi
  return "$harness_status"
}

main() {
  local mode=''
  local case_filter=''
  local -a pytest_arguments=()

  while (($#)); do
    case "$1" in
      --pure | --integration)
        [[ -z "$mode" ]] || {
          monitoring_runner_fail 'choose exactly one of --pure or --integration'
          return 2
        }
        mode="$1"
        shift
        ;;
      --case)
        [[ "$#" -ge 2 ]] || {
          monitoring_runner_fail '--case requires a name'
          return 2
        }
        case_filter="${2//-/_}"
        shift 2
        ;;
      --help | -h)
        monitoring_runner_usage
        return 0
        ;;
      *)
        pytest_arguments+=("$1")
        shift
        ;;
    esac
  done

  [[ -n "$mode" ]] || {
    monitoring_runner_usage >&2
    return 2
  }
  [[ -z "$case_filter" ]] || pytest_arguments+=("-k" "$case_filter")

  case "$mode" in
    --pure) run_pure_tests "${pytest_arguments[@]}" ;;
    --integration) run_integration_tests "${pytest_arguments[@]}" ;;
  esac
}

main "$@"
