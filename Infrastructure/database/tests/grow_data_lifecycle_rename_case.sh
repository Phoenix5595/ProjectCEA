#!/usr/bin/env bash
# Sourced only from test-grow-data-lifecycle.sh's marked disposable case.
# The parent harness must run this case in-process so its EXIT trap owns both
# generated names if a failure occurs between rename and rename-back.

rename_set_owned_database_connections() {
  local database_name="$1"
  local allow_connections="$2"

  [[ "$database_name" =~ ^monitoring_test_[a-z0-9_]+$ ]] || {
    grow_fail 'refusing database connection change outside monitoring_test_ namespace'
    return 1
  }
  [[ "${database_name,,}" != *cea_sensors* && "${database_name,,}" != *sj_cea* ]] || {
    grow_fail 'refusing database connection change for a protected name'
    return 1
  }
  [[ "$allow_connections" == 'true' || "$allow_connections" == 'false' ]] || {
    grow_fail 'invalid disposable database connection setting'
    return 1
  }
  sudo -u postgres psql -X --set=ON_ERROR_STOP=1 \
    --host="$POSTGRES_ADMIN_HOST" --dbname="$POSTGRES_ADMIN_DATABASE" \
    --command="ALTER DATABASE \"${database_name}\" WITH ALLOW_CONNECTIONS ${allow_connections}" \
    >/dev/null
}

rename_quiesce_owned_database() {
  local database_name="$1"
  local session_count attempt=0

  rename_set_owned_database_connections "$database_name" false || return 1
  sudo -u postgres psql -X --set=ON_ERROR_STOP=1 \
    --host="$POSTGRES_ADMIN_HOST" --dbname="$POSTGRES_ADMIN_DATABASE" \
    --command="SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${database_name}' AND pid <> pg_backend_pid()" \
    >/dev/null || return 1
  while ((attempt < 50)); do
    session_count="$(sudo -u postgres psql -X --tuples-only --no-align \
      --host="$POSTGRES_ADMIN_HOST" --dbname="$POSTGRES_ADMIN_DATABASE" \
      --command="SELECT count(*) FROM pg_stat_activity WHERE datname = '${database_name}'")" || return 1
    [[ "$session_count" == '0' ]] && return 0
    sleep 0.1
    ((attempt += 1))
  done
  grow_fail "could not quiesce disposable database sessions: ${database_name}"
}

run_owned_database_rename_proof() {
  local database_url="$1"
  local original_url="$database_url"
  local original_name port url_prefix target_name target_url target_exists
  local privilege_target privilege_url privilege_result privilege_restore_result
  local owner_state owner_result owner_without_createdb before_snapshot locked_snapshot renamed_snapshot restored_snapshot
  local status=0 database_name role_name

  [[ "${GROW_HARNESS_INTERNAL:-}" == '1' && "${MONITORING_HARNESS_INTERNAL:-}" == '1' ]] || {
    grow_fail 'rename proof requires the grow case and monitoring disposable-harness markers'
    return 1
  }
  [[ "${GROW_RENAME_CASE_LOCKED:-0}" == '1' ]] || {
    grow_fail 'rename proof must run under /tmp/grow-lifecycle-harness.lock'
    return 1
  }
  [[ "$database_url" == "${MONITORING_TEST_DATABASE_URL:-}" ]] || {
    grow_fail 'rename proof URL is not the exact marked disposable database URL'
    return 1
  }
  [[ "$database_url" =~ ^postgresql://[a-z0-9_]+:[a-f0-9]+@127\.0\.0\.1:([0-9]+)/([a-z0-9_]+)$ ]] || {
    grow_fail 'rename proof URL does not match the owned loopback harness form'
    return 1
  }
  port="${BASH_REMATCH[1]}"
  original_name="${BASH_REMATCH[2]}"
  [[ "$original_name" == "$MONITORING_DATABASE_NAME" ]] || {
    grow_fail 'rename proof database is not the name owned by the harness'
    return 1
  }
  [[ "$original_name" =~ ^monitoring_test_[a-z0-9_]+$ ]] || {
    grow_fail 'original database name is outside the disposable namespace'
    return 1
  }
  [[ "${#MONITORING_DATABASE_CREATED_NAMES[@]}" -eq 1 && "${MONITORING_DATABASE_CREATED_NAMES[0]}" == "$original_name" ]] || {
    grow_fail 'harness ownership list does not contain exactly the created source database'
    return 1
  }
  [[ "${MONITORING_ROLE_NAME:-}" =~ ^monitoring_role_[a-z0-9_]+$ ]] || {
    grow_fail 'monitoring harness did not provide its owned disposable role'
    return 1
  }
  role_name="$MONITORING_ROLE_NAME"

  # Exercise the actual shell harness parser without opening a connection.
  for database_name in \
    cea_sensors CEA_SENSORS prefix_cea_sensors_alias \
    sj_cea SJ_CEA prefix_sj_cea_alias; do
    if assert_safe_database_url "postgresql://guard:probe@127.0.0.1:${port}/${database_name}" >/dev/null 2>&1; then
      grow_fail "disposable URL guard accepted protected database alias: ${database_name}"
      return 1
    fi
  done
  "$GROW_LIFECYCLE_PYTHON" "$GROW_RENAME_CASE_PY" guard-only --database-url "$database_url"

  # Source retention and compression shorter than three months must block;
  # compliant source compression and unrelated aggregate refresh stay enabled.
  "$GROW_LIFECYCLE_PYTHON" "$GROW_RENAME_CASE_PY" policy-probe --database-url "$database_url"
  "$GROW_LIFECYCLE_PYTHON" "$GROW_RENAME_CASE_PY" database-setting-probe \
    --database-url "$database_url"

  url_prefix="${original_url%/*}"
  local suffix="${original_name#monitoring_test_}"
  suffix="${suffix:0:24}"
  target_name="monitoring_test_rename_${suffix}_${RANDOM}"
  privilege_target="monitoring_test_privilege_${suffix}_${RANDOM}"
  for database_name in "$target_name" "$privilege_target"; do
    [[ "$database_name" =~ ^monitoring_test_[a-z0-9_]+$ ]] || {
      grow_fail 'generated rename target is outside the disposable namespace'
      return 1
    }
    (( ${#database_name} <= 63 )) || {
      grow_fail 'generated rename target exceeds PostgreSQL identifier length'
      return 1
    }
    if [[ "${database_name,,}" == *cea_sensors* || "${database_name,,}" == *sj_cea* ]]; then
      grow_fail 'generated rename target contains a protected database alias'
      return 1
    fi
    target_exists="$(sudo -u postgres psql -X --tuples-only --no-align \
      --host="$POSTGRES_ADMIN_HOST" --dbname="$POSTGRES_ADMIN_DATABASE" \
      --command="SELECT count(*) FROM pg_database WHERE datname = '${database_name}'")" || return 1
    [[ "$target_exists" == '0' ]] || {
      grow_fail "generated rename target already exists; refusing to claim or clean it: ${database_name}"
      return 1
    }
  done
  target_url="${url_prefix}/${target_name}"
  privilege_url="${url_prefix}/${privilege_target}"
  assert_safe_database_url "$target_url" || return 1
  assert_safe_database_url "$privilege_url" || return 1

  owner_state="$("$GROW_LIFECYCLE_PYTHON" "$GROW_RENAME_CASE_PY" \
    owner-role-state --database-url "$original_url")" || {
    grow_fail 'could not inspect the disposable database owner role'
    return 1
  }
  [[ "$owner_state" == 'owner_role_state createdb=false superuser=false' ]] || {
    grow_fail "expected the initial disposable owner to be NOSUPERUSER NOCREATEDB; got: ${owner_state}"
    return 1
  }
  printf 'rename privilege probe: %s\n' "$owner_state"
  rename_quiesce_owned_database "$original_name" || return 1

  # Observe what a plain database owner can do before granting CREATEDB.
  trap '' INT TERM HUP
  if ! privilege_result="$("$GROW_LIFECYCLE_PYTHON" "$GROW_RENAME_CASE_PY" \
      owner-rename --database-url "$original_url" --target-name "$privilege_target")"; then
    trap 'exit 130' INT
    trap 'exit 143' TERM HUP
    grow_fail 'owner-without-CREATEDB privilege probe failed unexpectedly'
    return 1
  fi
  if [[ "$privilege_result" == 'owner_rename=succeeded createdb=false superuser=false' ]]; then
    MONITORING_DATABASE_CREATED_NAMES+=("$privilege_target")
    MONITORING_DATABASE_NAME="$privilege_target"
    MONITORING_TEST_DATABASE_URL="$privilege_url"
    if ! privilege_restore_result="$("$GROW_LIFECYCLE_PYTHON" "$GROW_RENAME_CASE_PY" \
        owner-rename --database-url "$privilege_url" --target-name "$original_name")"; then
      trap 'exit 130' INT
      trap 'exit 143' TERM HUP
      grow_fail 'owner without CREATEDB renamed successfully but could not restore the source name'
      return 1
    fi
    [[ "$privilege_restore_result" == 'owner_rename=succeeded createdb=false superuser=false' ]] || {
      trap 'exit 130' INT
      trap 'exit 143' TERM HUP
      grow_fail "owner without CREATEDB could not rename back: ${privilege_restore_result}"
      return 1
    }
    MONITORING_DATABASE_NAME="$original_name"
    MONITORING_TEST_DATABASE_URL="$original_url"
    owner_result='owner_without_createdb=succeeded'
    owner_without_createdb="$owner_result"
  elif [[ "$privilege_result" == 'owner_rename=denied sqlstate=42501 createdb=false superuser=false' ]]; then
    owner_result='owner_without_createdb=denied_sqlstate_42501'
    owner_without_createdb="$owner_result"
  else
    trap 'exit 130' INT
    trap 'exit 143' TERM HUP
    grow_fail "owner-without-CREATEDB returned an unexpected result: ${privilege_result}"
    return 1
  fi
  rename_set_owned_database_connections "$original_name" true || return 1
  trap 'exit 130' INT
  trap 'exit 143' TERM HUP
  printf 'rename privilege probe: %s\n' "$owner_result"

  # CREATEDB is granted only to the generated disposable role, then the proof
  # rechecks that it remains a non-superuser database owner.
  sudo -u postgres psql -X --set=ON_ERROR_STOP=1 \
    --host="$POSTGRES_ADMIN_HOST" --dbname="$POSTGRES_ADMIN_DATABASE" \
    --command="ALTER ROLE \"${role_name}\" CREATEDB" >/dev/null || return 1
  owner_state="$("$GROW_LIFECYCLE_PYTHON" "$GROW_RENAME_CASE_PY" \
    owner-role-state --database-url "$original_url")" || {
    grow_fail 'could not verify disposable owner privileges after granting CREATEDB'
    return 1
  }
  [[ "$owner_state" == 'owner_role_state createdb=true superuser=false' ]] || {
    grow_fail "disposable owner is not CREATEDB without SUPERUSER: ${owner_state}"
    return 1
  }

  before_snapshot="$("$GROW_LIFECYCLE_PYTHON" "$GROW_RENAME_CASE_PY" \
    snapshot --database-url "$original_url")" || {
    grow_fail 'could not capture the pre-rename disposable parity snapshot'
    return 1
  }
  privilege_result="$("$GROW_LIFECYCLE_PYTHON" "$GROW_RENAME_CASE_PY" \
    locked-rename-probe --database-url "$original_url" --target-name "$target_name")" || {
    grow_fail 'connected-session SQLSTATE 55006 proof failed'
    return 1
  }
  [[ "$privilege_result" == 'locked_rename=blocked sqlstate=55006 condition=object_in_use' ]] || {
    grow_fail "connected-session rename returned an unexpected result: ${privilege_result}"
    return 1
  }
  locked_snapshot="$("$GROW_LIFECYCLE_PYTHON" "$GROW_RENAME_CASE_PY" \
    snapshot --database-url "$original_url")" || {
    grow_fail 'could not capture the post-failure disposable parity snapshot'
    return 1
  }
  [[ "$before_snapshot" == "$locked_snapshot" ]] || {
    grow_fail 'failed SQLSTATE 55006 rename changed data or database settings'
    return 1
  }
  printf '%s; failed rename left database settings and row/schema state unchanged\n' "$privilege_result"
  rename_quiesce_owned_database "$original_name" || return 1

  # Ignore catchable termination signals only over ALTER + ownership registration,
  # so EXIT cleanup cannot miss a successfully renamed owned database.
  trap '' INT TERM HUP
  if ! owner_result="$("$GROW_LIFECYCLE_PYTHON" "$GROW_RENAME_CASE_PY" \
      owner-rename --database-url "$original_url" --target-name "$target_name")"; then
    trap 'exit 130' INT
    trap 'exit 143' TERM HUP
    grow_fail 'non-superuser database owner could not rename the owned disposable database'
    return 1
  fi
  if [[ "$owner_result" == owner_rename=succeeded* ]]; then
    MONITORING_DATABASE_CREATED_NAMES+=("$target_name")
    MONITORING_DATABASE_NAME="$target_name"
    MONITORING_TEST_DATABASE_URL="$target_url"
    rename_set_owned_database_connections "$target_name" true || return 1
  fi
  if [[ "$owner_result" != 'owner_rename=succeeded createdb=true superuser=false' ]]; then
    trap 'exit 130' INT
    trap 'exit 143' TERM HUP
    grow_fail "CREATEDB non-superuser owner rename failed: ${owner_result}"
    return 1
  fi
  trap 'exit 130' INT
  trap 'exit 143' TERM HUP
  printf 'successful rename privilege: %s\n' "$owner_result"

  if ! renamed_snapshot="$("$GROW_LIFECYCLE_PYTHON" "$GROW_RENAME_CASE_PY" \
      snapshot --database-url "$target_url")"; then
    status=1
  elif [[ "$before_snapshot" != "$renamed_snapshot" ]]; then
    grow_fail 'database settings, owner, extension, hypertable, CAGG, compression, job, or row parity changed after rename'
    status=1
  fi

  # Always attempt an explicit owner rename-back and reconnect; if this fails
  # the parent EXIT trap still knows both generated names.
  rename_quiesce_owned_database "$target_name" || return 1
  trap '' INT TERM HUP
  if ! owner_result="$("$GROW_LIFECYCLE_PYTHON" "$GROW_RENAME_CASE_PY" \
      owner-rename --database-url "$target_url" --target-name "$original_name")"; then
    trap 'exit 130' INT
    trap 'exit 143' TERM HUP
    grow_fail 'rename-back failed; the parent cleanup trap owns both generated names'
    return 1
  fi
  if [[ "$owner_result" != 'owner_rename=succeeded createdb=true superuser=false' ]]; then
    trap 'exit 130' INT
    trap 'exit 143' TERM HUP
    grow_fail "rename-back failed; the parent cleanup trap owns both generated names: ${owner_result}"
    return 1
  fi
  MONITORING_DATABASE_NAME="$original_name"
  MONITORING_TEST_DATABASE_URL="$original_url"
  rename_set_owned_database_connections "$original_name" true || return 1
  trap 'exit 130' INT
  trap 'exit 143' TERM HUP

  if ! restored_snapshot="$("$GROW_LIFECYCLE_PYTHON" "$GROW_RENAME_CASE_PY" \
      snapshot --database-url "$original_url")"; then
    status=1
  elif [[ "$before_snapshot" != "$restored_snapshot" ]]; then
    grow_fail 'database settings, owner, extension, hypertable, CAGG, compression, job, or row parity changed after rename-back'
    status=1
  fi
  ((status == 0)) || return "$status"
  printf 'owner without CREATEDB: %s; owner with CREATEDB and NOSUPERUSER renamed and restored %s with full database parity\n' \
    "$owner_without_createdb" "$original_name"
}
