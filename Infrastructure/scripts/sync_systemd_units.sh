#!/usr/bin/env bash
# Install repo-managed systemd units and drop-ins into the system unit tree.
# Deploys pass --release and --backup-dir for a non-interactive, reversible sync.

set -euo pipefail

usage() {
  cat <<EOF
Usage: $0 [--release <repo-root> --backup-dir <dir>] [--dry-run] [--reenable] [--help]

  --release <path>  Repository root to install from (defaults to this checkout).
  --backup-dir <dir> Reversible backup directory; required with --release.
  --dry-run         Show differences without installing or reloading systemd.
  --reenable        Reenable changed base units after installation.
  --help            Show this help.
EOF
}

DRY_RUN=0
REENABLE=0
RELEASE_ROOT=""
BACKUP_DIR=""
EXPLICIT_RELEASE=0
EXPLICIT_BACKUP=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --reenable) REENABLE=1; shift ;;
    --release)
      [[ $# -ge 2 && -n "$2" ]] || { echo "--release requires a path" >&2; exit 2; }
      RELEASE_ROOT="$2"
      EXPLICIT_RELEASE=1
      shift 2
      ;;
    --backup-dir)
      [[ $# -ge 2 && -n "$2" ]] || { echo "--backup-dir requires a path" >&2; exit 2; }
      BACKUP_DIR="$2"
      EXPLICIT_BACKUP=1
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEFAULT_REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
REPO_ROOT="${RELEASE_ROOT:-$DEFAULT_REPO_ROOT}"
SERVICE_LIST="$REPO_ROOT/Infrastructure/scripts/service_list.py"
SYSTEMD_DIR="${DEPLOY_SYSTEMD_DIR:-/etc/systemd/system}"
BACKUP_ROOT="${SYSTEMD_BACKUP_ROOT:-/var/lib/projectcea/systemd-backup}"

if [[ "$EXPLICIT_RELEASE" -ne "$EXPLICIT_BACKUP" ]]; then
  echo "--release and --backup-dir must be specified together" >&2
  exit 2
fi
if [[ ! -x "$SERVICE_LIST" ]]; then
  echo "ERROR: $SERVICE_LIST not found or not executable" >&2
  exit 2
fi
if [[ "$EXPLICIT_RELEASE" -eq 1 && ! -d "$REPO_ROOT/Infrastructure" ]]; then
  echo "ERROR: release Infrastructure directory not found: $REPO_ROOT/Infrastructure" >&2
  exit 2
fi

if [[ -z "$BACKUP_DIR" ]]; then
  BACKUP_DIR="$BACKUP_ROOT/$(date -u +%Y%m%dT%H%M%SZ)"
fi

declare -a DEST_PATHS=()
declare -a SOURCE_PATHS=()
declare -a UNIT_NAMES=()
declare -a CHANGED_INDEXES=()
declare -a REENABLE_UNITS=()
mapfile -t SERVICE_PAIRS < <(python3 "$SERVICE_LIST" --list-repo-unit-paths)
for pair in "${SERVICE_PAIRS[@]}"; do
  unit="${pair%%$'\t'*}"
  repo_rel="${pair##*$'\t'}"
  DEST_PATHS+=("$unit")
  SOURCE_PATHS+=("$REPO_ROOT/$repo_rel")
  UNIT_NAMES+=("$unit")
done

HANDLER_REL="Infrastructure/service-error-handler@.service"
HANDLER_SOURCE="$REPO_ROOT/$HANDLER_REL"
if [[ ! -f "$HANDLER_SOURCE" ]]; then
  echo "ERROR: required failure handler unit is missing: $HANDLER_SOURCE" >&2
  exit 2
fi
DEST_PATHS+=("service-error-handler@.service")
SOURCE_PATHS+=("$HANDLER_SOURCE")
UNIT_NAMES+=("service-error-handler@.service")

DROPIN_ROOT="$REPO_ROOT/Infrastructure/systemd-overrides"
if [[ -d "$DROPIN_ROOT" ]]; then
  while IFS= read -r source_path; do
    [[ -n "$source_path" ]] || continue
    relative="${source_path#"$DROPIN_ROOT"/}"
    DEST_PATHS+=("$relative")
    SOURCE_PATHS+=("$source_path")
    UNIT_NAMES+=("")
  done < <(find "$DROPIN_ROOT" -mindepth 2 -maxdepth 2 -type f -name '*.conf' -print | sort)
fi

echo "=== systemd unit sync: $REPO_ROOT -> $SYSTEMD_DIR ==="
for index in "${!DEST_PATHS[@]}"; do
  relative="${DEST_PATHS[$index]}"
  repo_path="${SOURCE_PATHS[$index]}"
  installed_path="$SYSTEMD_DIR/$relative"
  if [[ ! -f "$repo_path" ]]; then
    echo "  SKIP $relative (missing: $repo_path)"
    if [[ "$EXPLICIT_RELEASE" -eq 1 ]]; then
      exit 1
    fi
    continue
  fi
  if [[ -f "$installed_path" ]] && cmp -s "$repo_path" "$installed_path"; then
    echo "  OK   $relative (identical)"
    continue
  fi
  if [[ -f "$installed_path" ]]; then
    echo "  DIFF $relative"
    if [[ "$DRY_RUN" -eq 1 ]]; then
      diff -u "$installed_path" "$repo_path" | sed 's/^/      /' || true
    fi
  else
    echo "  NEW  $relative"
  fi
  CHANGED_INDEXES+=("$index")
  if [[ -n "${UNIT_NAMES[$index]}" ]]; then
    REENABLE_UNITS+=("${UNIT_NAMES[$index]}")
  fi
done

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "(--dry-run: no changes applied)"
  exit 0
fi
if [[ ${#CHANGED_INDEXES[@]} -eq 0 && "$EXPLICIT_RELEASE" -eq 0 ]]; then
  echo "All repo unit files and drop-ins match. Nothing to do."
  exit 0
fi

if [[ "$EXPLICIT_RELEASE" -eq 0 && -t 0 ]]; then
  read -r -p "Apply changes to $SYSTEMD_DIR? [y/N] " answer
  if [[ "$answer" != "y" && "$answer" != "Y" ]]; then
    echo "Aborted."
    exit 1
  fi
fi

sudo install -d -m 0755 "$BACKUP_DIR"
created_tmp="$(mktemp)"
trap 'rm -f -- "$created_tmp"' EXIT
sudo install -m 0644 -o root -g root "$created_tmp" "$BACKUP_DIR/created.txt"
for index in "${CHANGED_INDEXES[@]}"; do
  relative="${DEST_PATHS[$index]}"
  repo_path="${SOURCE_PATHS[$index]}"
  installed_path="$SYSTEMD_DIR/$relative"
  if [[ -f "$installed_path" ]]; then
    sudo install -d -m 0755 "$BACKUP_DIR/$(dirname "$relative")"
    sudo cp -a "$installed_path" "$BACKUP_DIR/$relative"
    echo "  backed up  $relative"
  else
    printf '%s\n' "$relative" >> "$created_tmp"
    sudo install -m 0644 -o root -g root "$created_tmp" "$BACKUP_DIR/created.txt"
  fi
  sudo install -d -m 0755 "$(dirname "$installed_path")"
  sudo install -o root -g root -m 0644 "${SOURCE_PATHS[$index]}" "$installed_path"
  echo "  installed  $relative"
done
sudo install -m 0644 -o root -g root "$created_tmp" "$BACKUP_DIR/created.txt"

sudo systemctl daemon-reload
if [[ "$REENABLE" -eq 1 ]]; then
  for unit in "${REENABLE_UNITS[@]}"; do
    sudo systemctl reenable "$unit" 2>/dev/null || sudo systemctl enable "$unit" || true
  done
fi

echo "Done. Backup: $BACKUP_DIR"
echo "Rollback restores backed-up paths, removes created.txt entries, then daemon-reloads."
