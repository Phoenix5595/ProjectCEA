setup_deploy_lock() {
  local conflict_message="$1"
  sudo mkdir -p "$(dirname "$DEPLOY_LOCK_FILE")" 2>/dev/null || true
  sudo touch "$DEPLOY_LOCK_FILE" 2>/dev/null || true
  sudo chown antoine:antoine "$DEPLOY_LOCK_FILE" 2>/dev/null || true
  sudo chmod 0644 "$DEPLOY_LOCK_FILE" 2>/dev/null || true
  exec 9>"$DEPLOY_LOCK_FILE"
  if ! flock -n 9; then
    printf '%s\n' "$conflict_message" >&2
    return 1
  fi
}

serialize_deploy_event() {
  python3 "$EMIT" | python3 "$JSON_LINE"
}

systemd_load_state() {
  local unit="$1" state
  if state="$(sudo systemctl show -p LoadState --value "$unit" 2>/dev/null)"; then
    printf '%s\n' "$state"
  else
    printf 'unknown\n'
  fi
}

deploy_stop_services_reverse() {
  local service_list="$1" event_handler="$2"
  local rc=0 state unit index
  local -a units=()
  mapfile -t units < <(python3 "$service_list" --list-deploy-managed-units)
  for ((index=${#units[@]} - 1; index >= 0; index--)); do
    unit="${units[$index]}"
    state="$(systemd_load_state "$unit")"
    if [[ "$state" == "not-found" ]]; then
      "$event_handler" skipped "$unit" >/dev/null
      continue
    fi
    if ! sudo systemctl stop "$unit"; then
      "$event_handler" stop_failed "$unit" >/dev/null
      rc=1
    fi
  done
  return "$rc"
}

deploy_restart_services() {
  local service_list="$1" event_handler="$2"
  local rc=0 state unit
  sudo systemctl daemon-reload || rc=1
  while IFS= read -r unit; do
    [[ -n "$unit" ]] || continue
    state="$(systemd_load_state "$unit")"
    if [[ "$state" == "not-found" ]]; then
      "$event_handler" skipped "$unit" >/dev/null
      continue
    fi
    if ! sudo systemctl restart "$unit"; then
      "$event_handler" restart_failed "$unit" >/dev/null
      rc=1
    fi
  done < <(python3 "$service_list" --list-deploy-managed-units)
  return "$rc"
}

load_postgres_environment() {
  local name
  local -A original=()
  for name in POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD; do
    if [[ -v "$name" ]]; then original["$name"]="${!name}"; fi
  done
  if [[ -r "$POSTGRES_ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$POSTGRES_ENV_FILE"
    set +a
  fi
  for name in "${!original[@]}"; do
    printf -v "$name" '%s' "${original[$name]}"
  done
  : "${POSTGRES_HOST:=127.0.0.1}"
  : "${POSTGRES_PORT:=5432}"
  : "${POSTGRES_DB:=sj_cea}"
  : "${POSTGRES_USER:=cea_user}"
  export POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER
  if [[ -n "${POSTGRES_PASSWORD:-}" ]]; then
    export PGPASSWORD="$POSTGRES_PASSWORD"
  fi
}

current_alembic_heads() {
  local exists rows
  local -a psql_args=(
    -X --no-psqlrc --set=ON_ERROR_STOP=1 --tuples-only --no-align
    --host "$POSTGRES_HOST" --port "$POSTGRES_PORT"
    --username "$POSTGRES_USER" --dbname "$POSTGRES_DB"
  )
  if ! exists="$("$PSQL_BIN" "${psql_args[@]}" --command="SELECT to_regclass('public.alembic_version') IS NOT NULL")"; then
    return 1
  fi
  exists="$(printf '%s' "$exists" | tr -d '[:space:]')"
  if [[ "$exists" == "f" ]]; then
    printf '[]\n'
    return 0
  fi
  [[ "$exists" == "t" ]] || return 1
  rows="$("$PSQL_BIN" "${psql_args[@]}" --command="SELECT version_num FROM public.alembic_version ORDER BY 1")" || return 1
  printf '%s\n' "$rows" | python3 -c 'import json, sys; print(json.dumps(sorted(line.strip() for line in sys.stdin if line.strip())))'
}

manifest_schema_metadata() {
  python3 - "$1/deploy_manifest.json" "${2:-current}" <<'PY'
import json
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
section = sys.argv[2]
manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
metadata = manifest if section == "current" else manifest.get("previous_schema")
if (
    not isinstance(metadata, dict)
    or "migration_head" not in metadata
    or "alembic_heads" not in metadata
    or not isinstance(metadata["alembic_heads"], list)
):
    print(json.dumps({"known": False}, separators=(",", ":")))
else:
    print(
        json.dumps(
            {
                "known": True,
                "migration_head": metadata["migration_head"],
                "alembic_heads": sorted(str(head) for head in metadata["alembic_heads"]),
            },
            separators=(",", ":"),
        )
    )
PY
}

schema_metadata_known() {
  python3 - "$1" <<'PY'
import json
import sys

print("true" if json.loads(sys.argv[1]).get("known") is True else "false")
PY
}

schema_metadata_head() {
  python3 - "$1" <<'PY'
import json
import sys

value = json.loads(sys.argv[1])["migration_head"]
print("null" if value is None else str(value))
PY
}

schema_metadata_alembic_heads() {
  python3 - "$1" <<'PY'
import json
import sys

print(json.dumps(sorted(json.loads(sys.argv[1])["alembic_heads"])))
PY
}

alembic_heads_equal() {
  python3 - "$1" "$2" <<'PY'
import json
import sys

raise SystemExit(0 if sorted(json.loads(sys.argv[1])) == sorted(json.loads(sys.argv[2])) else 1)
PY
}

migration_head_relation() {
  python3 - "$1" "$2" <<'PY'
import re
import sys

current, target = sys.argv[1:]
current = None if current in ("", "null", "None") else current
target = None if target in ("", "null", "None") else target
if current == target:
    print("equal")
else:
    current_version = re.match(r"^(\d+)", current or "")
    target_version = re.match(r"^(\d+)", target or "")
    if current is None:
        print("older")
    elif target is None:
        print("newer")
    elif not current_version or not target_version:
        print("incomparable")
    elif int(current_version.group(1)) > int(target_version.group(1)):
        print("newer")
    else:
        print("older")
PY
}
