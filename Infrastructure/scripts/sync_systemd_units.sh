#!/usr/bin/env bash
# Sync repo-tracked systemd unit files in Infrastructure/*.service to
# /etc/systemd/system/. Opt-in; NOT wired into deploy.sh (so a bad unit file
# cannot brick the box via auto-deploy).
#
# Semantics:
#   - For each service in services.yaml with repo_unit != null:
#     * Compare repo_unit contents with /etc/systemd/system/<unit> byte-for-byte.
#     * If different, back up the current /etc/systemd/system/<unit> to
#       /var/lib/projectcea/systemd-backup/<TIMESTAMP>/<unit>, then install
#       the repo version.
#   - daemon-reload once at the end.
#   - Offer --reenable to also reenable every affected unit so [Install]
#     section changes (renamed WantedBy=, new deps) take effect on boot.
#   - --dry-run: only show diffs, don't touch anything.
#
# This script does NOT touch /etc/systemd/system/<unit>.service.d/ drop-ins.
# The drop-ins merge with the base unit on daemon-reload and override it for
# single-value keys (ExecStart, WorkingDirectory, etc.), so behaviour stays
# identical as long as repo base units match the merged behaviour captured
# in Phase 0 (which is by construction for the initial rollout).
#
# Rollback: the backup dir printed at the end can be rsync'd back:
#   sudo rsync -a /var/lib/projectcea/systemd-backup/<TS>/ /etc/systemd/system/
#   sudo systemctl daemon-reload

set -euo pipefail

usage() {
  cat <<EOF
Usage: $0 [--dry-run] [--reenable] [--help]

  --dry-run   Show diffs but don't modify /etc/systemd/system/ or run daemon-reload.
  --reenable  After install, run 'systemctl reenable <unit>' for every changed
              unit so [Install] section changes take effect on boot.
  --help      This message.
EOF
}

DRY_RUN=0
REENABLE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --reenable) REENABLE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SERVICE_LIST="${SCRIPT_DIR}/service_list.py"
BACKUP_ROOT="/var/lib/projectcea/systemd-backup"
BACKUP_DIR="${BACKUP_ROOT}/$(date -u +%Y%m%dT%H%M%SZ)"
SYSTEMD_DIR="/etc/systemd/system"

if [[ ! -x "$SERVICE_LIST" ]]; then
  echo "ERROR: $SERVICE_LIST not found or not executable" >&2
  exit 2
fi

# Collect <unit>\t<repo_unit> pairs
mapfile -t PAIRS < <(python3 "$SERVICE_LIST" --list-repo-unit-paths)

if [[ ${#PAIRS[@]} -eq 0 ]]; then
  echo "No services with repo_unit in services.yaml — nothing to do."
  exit 0
fi

CHANGED=()
echo "=== systemd unit sync dry-run: repo vs /etc/systemd/system ==="
for pair in "${PAIRS[@]}"; do
  unit="${pair%%$'\t'*}"
  repo_rel="${pair##*$'\t'}"
  repo_path="${REPO_ROOT}/${repo_rel}"
  installed_path="${SYSTEMD_DIR}/${unit}"

  if [[ ! -f "$repo_path" ]]; then
    echo "  SKIP $unit (missing: $repo_path)"
    continue
  fi

  if [[ ! -f "$installed_path" ]]; then
    echo "  NEW  $unit (no current /etc/systemd/system/$unit)"
    CHANGED+=("$unit"$'\t'"$repo_path"$'\t'"$installed_path")
    continue
  fi

  if cmp -s "$repo_path" "$installed_path"; then
    echo "  OK   $unit (identical)"
  else
    echo "  DIFF $unit"
    if [[ "$DRY_RUN" -eq 1 ]]; then
      diff -u "$installed_path" "$repo_path" | sed 's/^/      /' || true
    fi
    CHANGED+=("$unit"$'\t'"$repo_path"$'\t'"$installed_path")
  fi
done

if [[ ${#CHANGED[@]} -eq 0 ]]; then
  echo ""
  echo "All repo unit files match /etc/systemd/system. Nothing to do."
  exit 0
fi

echo ""
echo "Changed units: ${#CHANGED[@]}"
if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "(--dry-run: no changes applied)"
  exit 0
fi

# Interactive confirmation. Skip if stdin is not a TTY — expect caller to have
# already looked at --dry-run output.
if [[ -t 0 ]]; then
  read -r -p "Apply changes to $SYSTEMD_DIR? [y/N] " ans
  if [[ "$ans" != "y" && "$ans" != "Y" ]]; then
    echo "Aborted."
    exit 1
  fi
fi

sudo mkdir -p "$BACKUP_DIR"
echo "Backup dir: $BACKUP_DIR"

for row in "${CHANGED[@]}"; do
  IFS=$'\t' read -r unit repo_path installed_path <<<"$row"
  if [[ -f "$installed_path" ]]; then
    sudo cp -a "$installed_path" "${BACKUP_DIR}/${unit}"
    echo "  backed up  $unit  ->  ${BACKUP_DIR}/${unit}"
  fi
  sudo install -o root -g root -m 0644 "$repo_path" "$installed_path"
  echo "  installed  $unit  <-  ${repo_path}"
done

echo ""
echo "Running daemon-reload..."
sudo systemctl daemon-reload

if [[ "$REENABLE" -eq 1 ]]; then
  echo ""
  echo "Reenabling changed units (Install-section changes take effect)..."
  for row in "${CHANGED[@]}"; do
    unit="${row%%$'\t'*}"
    sudo systemctl reenable "$unit" 2>/dev/null || sudo systemctl enable "$unit" || true
  done
fi

echo ""
echo "Done. Backup: $BACKUP_DIR"
echo ""
echo "Services NOT restarted by this script. To apply runtime changes, restart manually:"
for row in "${CHANGED[@]}"; do
  unit="${row%%$'\t'*}"
  echo "  sudo systemctl restart $unit"
done

echo ""
echo "Rollback if needed:"
echo "  sudo rsync -a ${BACKUP_DIR}/ ${SYSTEMD_DIR}/"
echo "  sudo systemctl daemon-reload"
