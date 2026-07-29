#!/usr/bin/env bash
set -euo pipefail

readonly EXPECTED_DATABASE="cea_sensors"
readonly EXPECTED_USER="cea_user"
readonly EXPECTED_HOST="localhost"
readonly EXPECTED_PORT="5432"
readonly SAFE_PROOF_NAME="automation-safe-output.json"

fail() {
  printf 'registry reset refused: %s\n' "$1" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command unavailable: $1"
}

usage() {
  printf 'Usage: %s --confirm\n' "${0##*/}" >&2
}

[[ $# -eq 1 && "$1" == "--confirm" ]] || {
  usage
  fail "explicit --confirm is required"
}

readonly DATABASE="${PGDATABASE:-$EXPECTED_DATABASE}"
readonly DATABASE_USER="${PGUSER:-$EXPECTED_USER}"
readonly DATABASE_HOST="${PGHOST:-$EXPECTED_HOST}"
readonly DATABASE_PORT="${PGPORT:-$EXPECTED_PORT}"
[[ "$DATABASE" == "$EXPECTED_DATABASE" ]] || fail "database must be $EXPECTED_DATABASE"
[[ "$DATABASE_USER" == "$EXPECTED_USER" ]] || fail "database user must be $EXPECTED_USER"
[[ "$DATABASE_HOST" == "$EXPECTED_HOST" ]] || fail "database host must be $EXPECTED_HOST"
[[ "$DATABASE_PORT" == "$EXPECTED_PORT" ]] || fail "database port must be $EXPECTED_PORT"

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
readonly ROLLBACK_SCRIPT="${RESET_ROLLBACK_SCRIPT:-$PROJECT_ROOT/rollback-deploy.sh}"
readonly DEPLOY_STATE="${RESET_DEPLOY_STATE:-/var/lib/projectcea/deploy_state.json}"
readonly OUTPUT_DIRECTORY="${RESET_OUTPUT_DIR:-/var/lib/projectcea/registry-reset}"
readonly RUNTIME_DIRECTORY="${AUTOMATION_RUNTIME_DIR:-/run/projectcea}"
readonly SAFE_PROOF="$RUNTIME_DIRECTORY/$SAFE_PROOF_NAME"
readonly MINIMUM_FREE_KB="${RESET_MINIMUM_FREE_KB:-1048576}"

for command in psql pg_dump sha256sum systemctl python3 df stat; do
  require_command "$command"
done

rollback_state_is_valid=false
if [[ -f "$DEPLOY_STATE" ]]; then
  rollback_path="$(python3 - "$DEPLOY_STATE" <<'PY'
import json
import sys
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text())
value = state.get("rollback_to_path")
print(value if isinstance(value, str) else "")
PY
)"
  if [[ -n "$rollback_path" && -d "$rollback_path" ]]; then
    rollback_state_is_valid=true
  fi
fi
[[ "$rollback_state_is_valid" == true || -x "$ROLLBACK_SCRIPT" ]] || \
  fail "no usable deploy_state.json rollback target or rollback-deploy.sh"

umask 077
mkdir -p -m 0700 "$OUTPUT_DIRECTORY"
chmod 0700 "$OUTPUT_DIRECTORY"
[[ "$(stat -c '%a' "$OUTPUT_DIRECTORY")" == "700" ]] || fail "output directory must be mode 0700"

available_kb="$(df -Pk "$OUTPUT_DIRECTORY" | awk 'NR > 1 { available = $4 } END { print available }')"
[[ "$available_kb" =~ ^[0-9]+$ && "$available_kb" -ge "$MINIMUM_FREE_KB" ]] || \
  fail "insufficient disk space for registry backup"

readonly SCRIPT_STARTED_AT="$(python3 - <<'PY'
from datetime import UTC, datetime

print(datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z"))
PY
)"
PSQL=(
  psql --no-psqlrc -X -v ON_ERROR_STOP=1
  --dbname="$DATABASE" --username="$DATABASE_USER"
  --host="$DATABASE_HOST" --port="$DATABASE_PORT"
)
PG_DUMP=(
  pg_dump --no-owner --no-privileges --data-only --inserts
  --dbname="$DATABASE" --username="$DATABASE_USER"
  --host="$DATABASE_HOST" --port="$DATABASE_PORT"
)

rm -f "$SAFE_PROOF"

systemctl stop automation-service
service_state="$(systemctl is-active automation-service || true)"
[[ "$service_state" == "inactive" ]] || fail "automation-service is not inactive after stop"
[[ -f "$SAFE_PROOF" ]] || fail "automation-service did not write a safe-output proof"

python3 - "$SAFE_PROOF" "$SCRIPT_STARTED_AT" <<'PY'
import json
import stat
import sys
from datetime import datetime
from pathlib import Path

proof_path = Path(sys.argv[1])
started_at = datetime.fromisoformat(sys.argv[2].replace("Z", "+00:00"))
proof = json.loads(proof_path.read_text())

if stat.S_IMODE(proof_path.stat().st_mode) != 0o600:
    raise SystemExit("safe-output proof must be mode 0600")
if proof_path.stat().st_mtime_ns <= int(started_at.timestamp() * 1_000_000_000):
    raise SystemExit("safe-output proof mtime predates reset start")
created_at = datetime.fromisoformat(proof["created_at"].replace("Z", "+00:00"))
if created_at <= started_at:
    raise SystemExit("safe-output proof timestamp predates reset start")
mcp = proof["mcp"]
if mcp["all_off_command_succeeded"] is not True or mcp["logical_states"] != [False] * 16:
    raise SystemExit("safe-output proof does not show sixteen logical relay OFF states")
dfr_outputs = proof["dfr_outputs"]
if len(dfr_outputs) != 6 or not all(
    output["command_succeeded"] is True
    and output["cached_zero"] is True
    and output["cached_intensity"] == 0.0
    for output in dfr_outputs
):
    raise SystemExit("safe-output proof does not show six DFR zero commands with cached zero state")
PY

readonly RUN_DIRECTORY="$OUTPUT_DIRECTORY/registry-reset-$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -m 0700 "$RUN_DIRECTORY"
printf '%s\n' "$SCRIPT_STARTED_AT" > "$RUN_DIRECTORY/script-started-at.txt"

backup_table() {
  local table_name="$1"
  "${PG_DUMP[@]}" --table="public.$table_name" > "$RUN_DIRECTORY/$table_name.sql"
}

backup_table device_registry
backup_table device_states
backup_table device_mappings
backup_table light_target_intensity
"${PSQL[@]}" -c \
  'COPY (SELECT * FROM public.light_programs WHERE device_id IS NOT NULL) TO STDOUT WITH (FORMAT binary)' \
  > "$RUN_DIRECTORY/device-linked-light-programs.copy"

sha256sum \
  "$RUN_DIRECTORY/device_registry.sql" \
  "$RUN_DIRECTORY/device_states.sql" \
  "$RUN_DIRECTORY/device_mappings.sql" \
  "$RUN_DIRECTORY/light_target_intensity.sql" \
  "$RUN_DIRECTORY/device-linked-light-programs.copy" \
  > "$RUN_DIRECTORY/checksums.sha256"

"${PSQL[@]}" -Atc "
SELECT 'schedules', count(*) FROM public.schedules
UNION ALL SELECT 'room_modes', count(*) FROM public.room_modes
UNION ALL SELECT 'mode_parameters', count(*) FROM public.mode_parameters
UNION ALL SELECT 'climate_periods', count(*) FROM public.climate_periods
UNION ALL SELECT 'setpoints', count(*) FROM public.setpoints
UNION ALL SELECT 'measurement', count(*) FROM public.measurement
UNION ALL SELECT 'control_history', count(*) FROM public.control_history;
" > "$RUN_DIRECTORY/preserved-table-counts.tsv"

printf '%s\n' \
  'BEGIN;' \
  'DELETE FROM public.device_states;' \
  'DELETE FROM public.device_mappings;' \
  'DELETE FROM public.device_registry;' \
  'COMMIT;' \
  | "${PSQL[@]}"

empty_registry_count="$("${PSQL[@]}" -Atc 'SELECT count(*) FROM public.device_registry;')"
[[ "$empty_registry_count" == "0" ]] || fail "device_registry is not empty after reset transaction"

cat > "$RUN_DIRECTORY/restore-device-registry.sh" <<'RESTORE'
#!/usr/bin/env bash
set -euo pipefail

readonly BACKUP_DIRECTORY="${1:?Usage: restore-device-registry.sh BACKUP_DIRECTORY}"
readonly DATABASE="${PGDATABASE:-cea_sensors}"
readonly DATABASE_USER="${PGUSER:-cea_user}"
readonly DATABASE_HOST="${PGHOST:-localhost}"
readonly DATABASE_PORT="${PGPORT:-5432}"

[[ "$DATABASE" == "cea_sensors" && "$DATABASE_USER" == "cea_user" ]] || {
  printf 'restore refused: expected cea_sensors as cea_user\n' >&2
  exit 1
}
[[ "$DATABASE_HOST" == "localhost" && "$DATABASE_PORT" == "5432" ]] || {
  printf 'restore refused: expected localhost:5432\n' >&2
  exit 1
}

PSQL=(psql --no-psqlrc -X -v ON_ERROR_STOP=1 --dbname="$DATABASE" --username="$DATABASE_USER" --host="$DATABASE_HOST" --port="$DATABASE_PORT")
sha256sum --check "$BACKUP_DIRECTORY/checksums.sha256"
registry_count="$("${PSQL[@]}" -Atc 'SELECT count(*) FROM public.device_registry;')"
[[ "$registry_count" == "0" ]] || {
  printf 'restore refused: new registry rows already exist\n' >&2
  exit 1
}

"${PSQL[@]}" -f "$BACKUP_DIRECTORY/device_registry.sql"
"${PSQL[@]}" -f "$BACKUP_DIRECTORY/device_mappings.sql"
"${PSQL[@]}" -f "$BACKUP_DIRECTORY/device_states.sql"
"${PSQL[@]}" -f "$BACKUP_DIRECTORY/light_target_intensity.sql"
"${PSQL[@]}" -c 'COPY public.light_programs FROM STDIN WITH (FORMAT binary)' < "$BACKUP_DIRECTORY/device-linked-light-programs.copy"
RESTORE
chmod 0700 "$RUN_DIRECTORY/restore-device-registry.sh"

cat > "$RUN_DIRECTORY/RESTORE_INSTRUCTIONS.md" <<EOF
# Device registry restore

Only restore after stopping automation-service and only while device_registry
remains empty. The generated restore script refuses to run
after new registry rows exist, verifies every backup checksum, and restores this
order: registry, mappings, states, target intensities, then device-linked light
programs. Room-level light programs were preserved and are never restored.

$RUN_DIRECTORY/restore-device-registry.sh $RUN_DIRECTORY
EOF

systemctl start automation-service
printf 'Registry reset complete. Backup: %s\n' "$RUN_DIRECTORY"
