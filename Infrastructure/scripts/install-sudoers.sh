#!/usr/bin/env bash
# Install sudoers rule allowing cea user to restart automation-service without password.
set -euo pipefail

SOURCE="/home/antoine/ProjectCEA"
SUDOERS_FILE="$SOURCE/Infrastructure/automation-service/install/sudoers-cea-restart"
TARGET_FILE="/etc/sudoers.d/sudoers-cea-restart"

if [[ ! -f "$SUDOERS_FILE" ]]; then
    echo "ERROR: sudoers source file not found: $SUDOERS_FILE" >&2
    exit 1
fi

# Validate syntax before copying
if ! visudo -c -f "$SUDOERS_FILE"; then
    echo "ERROR: sudoers file failed validation" >&2
    exit 1
fi

sudo cp "$SUDOERS_FILE" "$TARGET_FILE"
sudo chmod 0440 "$TARGET_FILE"
echo "Installed $TARGET_FILE"
