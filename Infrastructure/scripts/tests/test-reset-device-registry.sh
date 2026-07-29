#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly RESET_SCRIPT="$SCRIPT_DIRECTORY/../reset-device-registry.sh"
readonly SANDBOX="$(mktemp -d)"
readonly BIN_DIRECTORY="$SANDBOX/bin"
readonly OUTPUT_DIRECTORY="$SANDBOX/output"
readonly RUNTIME_DIRECTORY="$SANDBOX/runtime"
readonly EVENT_LOG="$SANDBOX/events.log"

cleanup() {
  rm -rf "$SANDBOX"
}
trap cleanup EXIT

mkdir -p "$BIN_DIRECTORY" "$RUNTIME_DIRECTORY" "$SANDBOX/previous-release"
printf '{"rollback_to_path":"%s"}\n' "$SANDBOX/previous-release" > "$SANDBOX/deploy_state.json"
printf '#!/usr/bin/env bash\nexit 0\n' > "$SANDBOX/rollback-deploy.sh"
chmod 0700 "$SANDBOX/rollback-deploy.sh"

cat > "$BIN_DIRECTORY/systemctl" <<'SYSTEMCTL'
#!/usr/bin/env bash
set -euo pipefail

case "${1:-}" in
  stop)
    if [[ "${SYSTEM_STATE:-inactive}" == "active" ]]; then
      printf 'active\n' > "$STUB_STATE"
      exit 0
    fi
    printf 'inactive\n' > "$STUB_STATE"
    python3 - "$AUTOMATION_RUNTIME_DIR/automation-safe-output.json" "${PROOF_MODE:-0600}" <<'PY'
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

proof_path = Path(sys.argv[1])
proof_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
created_at = datetime(1970, 1, 1, tzinfo=UTC) if os.environ.get("PROOF_STALE") == "1" else datetime.now(UTC)
proof = {
    "created_at": created_at.isoformat(timespec="microseconds").replace("+00:00", "Z"),
    "mcp": {"all_off_command_succeeded": True, "logical_states": [False] * 16},
    "dfr_outputs": [
        {"board_id": board, "channel": channel, "command_succeeded": True, "cached_intensity": 0.0, "cached_zero": True}
        for board in (8, 9, 10)
        for channel in (0, 1)
    ],
}
proof_path.write_text(json.dumps(proof))
if os.environ.get("PROOF_STALE") == "1":
    os.utime(proof_path, (1, 1))
os.chmod(proof_path, int(sys.argv[2], 8))
PY
    ;;
  is-active)
    state="$(cat "$STUB_STATE")"
    printf '%s\n' "$state"
    [[ "$state" == "active" ]]
    ;;
  start)
    printf 'active\n' > "$STUB_STATE"
    printf 'start\n' >> "$EVENT_LOG"
    ;;
  *)
    printf 'unexpected systemctl invocation: %s\n' "$*" >&2
    exit 1
    ;;
esac
SYSTEMCTL

cat > "$BIN_DIRECTORY/pg_dump" <<'PGDUMP'
#!/usr/bin/env bash
set -euo pipefail

printf 'backup %s\n' "$*" >> "$EVENT_LOG"
[[ "${FAIL_PG_DUMP:-0}" != "1" ]] || exit 1
printf '%s\n' '-- sandbox backup'
PGDUMP

cat > "$BIN_DIRECTORY/psql" <<'PSQL'
#!/usr/bin/env bash
set -euo pipefail

arguments="$*"
printf 'psql %s\n' "$arguments" >> "$EVENT_LOG"
if [[ "$arguments" == *'SELECT count(*) FROM public.device_registry;'* ]]; then
  printf '%s\n' "${REGISTRY_COUNT:-0}"
  exit 0
fi
if [[ "$arguments" == *'COPY (SELECT * FROM public.light_programs WHERE device_id IS NOT NULL)'* ]]; then
  printf 'sandbox-binary-copy\n'
  exit 0
fi
if [[ "$arguments" == *'-Atc'* ]]; then
  printf 'schedules|2\nroom_modes|1\n'
  exit 0
fi
if [[ ! -t 0 ]]; then
  cat >> "$EVENT_LOG"
fi
PSQL

cat > "$BIN_DIRECTORY/sha256sum" <<'SHA256'
#!/usr/bin/env bash
set -euo pipefail

printf 'checksum %s\n' "$*" >> "$EVENT_LOG"
for file in "$@"; do
  [[ "$file" == --* ]] && continue
  printf 'sandbox-checksum  %s\n' "$file"
done
SHA256

chmod 0700 "$BIN_DIRECTORY/systemctl" "$BIN_DIRECTORY/pg_dump" "$BIN_DIRECTORY/psql" "$BIN_DIRECTORY/sha256sum"

base_environment() {
  export PATH="$BIN_DIRECTORY:$PATH"
  export AUTOMATION_RUNTIME_DIR="$RUNTIME_DIRECTORY"
  export EVENT_LOG STUB_STATE="$SANDBOX/service-state"
  export RESET_OUTPUT_DIR="$OUTPUT_DIRECTORY"
  export RESET_DEPLOY_STATE="$SANDBOX/deploy_state.json"
  export RESET_ROLLBACK_SCRIPT="$SANDBOX/rollback-deploy.sh"
  export RESET_MINIMUM_FREE_KB=1
  unset PGDATABASE PGUSER PGHOST PGPORT PROOF_MODE PROOF_STALE SYSTEM_STATE FAIL_PG_DUMP REGISTRY_COUNT
}

expect_failure() {
  set +e
  "$@" >/dev/null 2>&1
  status=$?
  set -e
  [[ $status -ne 0 ]] || {
    printf 'expected command to fail: %s\n' "$*" >&2
    exit 1
  }
}

base_environment
expect_failure bash "$RESET_SCRIPT"
[[ ! -e "$EVENT_LOG" ]] || {
  printf 'missing confirmation reached an external stub\n' >&2
  exit 1
}

base_environment
export PGDATABASE=wrong_database
expect_failure bash "$RESET_SCRIPT" --confirm
[[ ! -e "$EVENT_LOG" ]] || {
  printf 'identity guard reached an external stub\n' >&2
  exit 1
}

base_environment
export PROOF_MODE=0644
expect_failure bash "$RESET_SCRIPT" --confirm
[[ ! -e "$EVENT_LOG" ]] || {
  printf 'invalid proof reached backup or delete calls\n' >&2
  exit 1
}

base_environment
export PROOF_STALE=1
expect_failure bash "$RESET_SCRIPT" --confirm
[[ ! -e "$EVENT_LOG" ]] || {
  printf 'stale proof reached backup or delete calls\n' >&2
  exit 1
}

base_environment
export SYSTEM_STATE=active
expect_failure bash "$RESET_SCRIPT" --confirm
[[ ! -e "$EVENT_LOG" ]] || {
  printf 'active service reached backup or delete calls\n' >&2
  exit 1
}

base_environment
export FAIL_PG_DUMP=1
expect_failure bash "$RESET_SCRIPT" --confirm
[[ ! -f "$EVENT_LOG" ]] || ! grep -q 'DELETE FROM public.device_states' "$EVENT_LOG" || {
  printf 'backup failure reached delete transaction\n' >&2
  exit 1
}
rm -f "$EVENT_LOG"

base_environment
bash "$RESET_SCRIPT" --confirm

shopt -s nullglob
backup_directories=("$OUTPUT_DIRECTORY"/registry-reset-*)
backup_directory=""
for candidate in "${backup_directories[@]}"; do
  if [[ -f "$candidate/checksums.sha256" ]]; then
    backup_directory="$candidate"
  fi
done
[[ -n "$backup_directory" && -d "$backup_directory" ]] || {
  printf 'backup directory was not created\n' >&2
  exit 1
}
[[ "$(stat -c '%a' "$OUTPUT_DIRECTORY")" == "700" ]] || {
  printf 'output directory is not mode 0700\n' >&2
  exit 1
}
[[ -f "$backup_directory/checksums.sha256" && -f "$backup_directory/preserved-table-counts.tsv" ]] || {
  printf 'backup manifest is incomplete\n' >&2
  exit 1
}
for table in device_registry device_states device_mappings light_target_intensity; do
  [[ -f "$backup_directory/$table.sql" ]] || {
    printf 'missing %s backup\n' "$table" >&2
    exit 1
  }
done
[[ -f "$backup_directory/device-linked-light-programs.copy" ]] || {
  printf 'missing device-linked program backup\n' >&2
  exit 1
}

events="$(cat "$EVENT_LOG")"
[[ "$events" == *'backup '* ]] || {
  printf 'pg_dump backup calls were not made\n' >&2
  exit 1
}
[[ "$events" == *'--table=public.device_registry'* ]] || {
  printf 'device_registry was not backed up\n' >&2
  exit 1
}
[[ "$events" == *'WHERE device_id IS NOT NULL'* ]] || {
  printf 'room-level programs were not excluded from backup\n' >&2
  exit 1
}
backup_position="${events%%backup *}"
delete_states_position="${events%%DELETE FROM public.device_states*}"
delete_mappings_position="${events%%DELETE FROM public.device_mappings*}"
delete_registry_position="${events%%DELETE FROM public.device_registry*}"
[[ ${#backup_position} -lt ${#delete_states_position} && ${#delete_states_position} -lt ${#delete_mappings_position} && ${#delete_mappings_position} -lt ${#delete_registry_position} ]] || {
  printf 'delete order is not states, mappings, registry\n' >&2
  exit 1
}
[[ "$events" == *'start'* ]] || {
  printf 'automation service was not restarted\n' >&2
  exit 1
}
[[ "$events" == *'checksum '* ]] || {
  printf 'backup checksums were not calculated\n' >&2
  exit 1
}
preserved_counts="$(cat "$backup_directory/preserved-table-counts.tsv")"
[[ "$preserved_counts" == *'schedules|2'* && "$preserved_counts" == *'room_modes|1'* ]] || {
  printf 'preserved table counts were not recorded\n' >&2
  exit 1
}

restore_script="$backup_directory/restore-device-registry.sh"
[[ -x "$restore_script" ]] || {
  printf 'restore script was not generated\n' >&2
  exit 1
}
restore_contents="$(cat "$restore_script")"
[[ "$restore_contents" == *'new registry rows already exist'* ]] || {
  printf 'restore script lacks new-registry guard\n' >&2
  exit 1
}
[[ "$restore_contents" == *'device_registry.sql'* && "$restore_contents" == *'device_mappings.sql'* && "$restore_contents" == *'device_states.sql'* ]] || {
  printf 'restore script lacks dependency-ordered files\n' >&2
  exit 1
}
restore_registry_position="${restore_contents%%device_registry.sql*}"
restore_mappings_position="${restore_contents%%device_mappings.sql*}"
restore_states_position="${restore_contents%%device_states.sql*}"
restore_targets_position="${restore_contents%%light_target_intensity.sql*}"
restore_programs_position="${restore_contents%%device-linked-light-programs.copy*}"
[[ ${#restore_registry_position} -lt ${#restore_mappings_position} && ${#restore_mappings_position} -lt ${#restore_states_position} && ${#restore_states_position} -lt ${#restore_targets_position} && ${#restore_targets_position} -lt ${#restore_programs_position} ]] || {
  printf 'restore order is not dependency ordered\n' >&2
  exit 1
}
[[ "$restore_contents" != *'TRUNCATE'* && "$restore_contents" != *'alembic'* && "$restore_contents" != *'seed'* ]] || {
  printf 'restore script contains a forbidden mutation path\n' >&2
  exit 1
}

export REGISTRY_COUNT=1
expect_failure bash "$restore_script" "$backup_directory"

printf 'reset-device-registry sandbox test passed\n'
