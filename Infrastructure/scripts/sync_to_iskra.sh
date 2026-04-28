#!/usr/bin/env bash
# Sync the repo-canonical iskra_stack to the iskraprojectcea host.
#
# Safety: rsync --delete is guarded. More than three deletes requires
# CONFIRM=1 so a bad source path cannot wipe the remote stack silently.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCE_DIR="$INFRA_DIR/iskra_stack/"

ISKRA_HOST="${ISKRA_HOST:-iskraprojectcea}"
ISKRA_DIR="${ISKRA_DIR:-/home/antoine/ProjectCEA/Infrastructure/iskra_stack}"
CONFIRM="${CONFIRM:-0}"
RSYNC_EXCLUDES=(
  "--exclude=.env"
  "--exclude=.env.*"
  "--exclude=*.bak"
  "--exclude=*.bak.*"
)

if [[ ! -d "$SOURCE_DIR" ]]; then
  echo "[sync_to_iskra] source missing: $SOURCE_DIR" >&2
  exit 2
fi

echo "[sync_to_iskra] source: $SOURCE_DIR"
echo "[sync_to_iskra] target: $ISKRA_HOST:$ISKRA_DIR"

ssh "$ISKRA_HOST" "mkdir -p '$ISKRA_DIR'"

dry_run="$(rsync -az --delete --dry-run --out-format='%i %n' "${RSYNC_EXCLUDES[@]}" "$SOURCE_DIR" "$ISKRA_HOST:$ISKRA_DIR/")"
delete_count="$(printf '%s\n' "$dry_run" | awk '/^\*deleting / {n++} END {print n+0}')"

if [[ "$delete_count" -gt 3 && "$CONFIRM" != "1" ]]; then
  echo "[sync_to_iskra] refusing $delete_count remote deletes; rerun with CONFIRM=1" >&2
  printf '%s\n' "$dry_run" >&2
  exit 3
fi

rsync -az --delete "${RSYNC_EXCLUDES[@]}" "$SOURCE_DIR" "$ISKRA_HOST:$ISKRA_DIR/"
echo "[sync_to_iskra] complete"
