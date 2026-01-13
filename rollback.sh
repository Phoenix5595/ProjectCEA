#!/bin/bash
set -e

RELEASES="/opt/projectcea/releases"
CURRENT=$(readlink /opt/projectcea/current)
CURRENT_NAME=$(basename "$CURRENT")

# Find previous release
PREVIOUS=$(ls -1t "$RELEASES" | grep -v "$CURRENT_NAME" | head -1)

if [ -z "$PREVIOUS" ]; then
  echo "ERROR: No previous release to rollback to"
  exit 1
fi

echo "Current:  $CURRENT_NAME"
echo "Rollback: $PREVIOUS"
read -p "Proceed? [y/N] " confirm
if [ "$confirm" != "y" ]; then
  echo "Aborted."
  exit 0
fi

# Switch symlink
sudo ln -sfn "$RELEASES/$PREVIOUS" /opt/projectcea/current

# Restart services
sudo systemctl daemon-reload
sudo systemctl restart can-setup can-processor cea-backend automation-service soil-sensor-service

echo "Rolled back to: $PREVIOUS"
