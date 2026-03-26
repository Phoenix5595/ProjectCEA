#!/bin/bash
set -e

SOURCE="/home/antoine/ProjectCEA"
RELEASES="/opt/projectcea/releases"
MAX_RELEASES=10

# Generate release ID
RELEASE_ID=$(date +%Y%m%d-%H%M%S)-$(git -C "$SOURCE" rev-parse --short HEAD 2>/dev/null || echo "nogit")
TARGET="$RELEASES/$RELEASE_ID"

echo "=== Deploying release: $RELEASE_ID ==="

# 0. Lint and format (Ruff) on Infrastructure — must pass before release
echo "[0/7] Running Ruff (lint + format) on Infrastructure..."
cd "$SOURCE"
ruff check --fix Infrastructure/
ruff format Infrastructure/
cd - >/dev/null

# 1. Copy code to new release
echo "[1/7] Copying code..."
sudo mkdir -p "$TARGET"
sudo rsync -a --delete "$SOURCE/Infrastructure/" "$TARGET/Infrastructure/"
sudo chown -R root:root "$TARGET"

# 2. Build Python venvs
echo "[2/7] Building Python venvs..."
for svc in backend automation-service can-processor-service soil-sensor-service onewire-worker-service weather-service; do
  if [ -f "$TARGET/Infrastructure/$svc/requirements.txt" ]; then
    echo "  - $svc"
    cd "$TARGET/Infrastructure/$svc"
    sudo python3 -m venv .venv
    sudo .venv/bin/pip install -q --upgrade pip
    sudo .venv/bin/pip install -q -r requirements.txt
  fi
done

# 3. Build frontend
echo "[3/7] Building frontend..."
cd "$TARGET/Infrastructure/frontend"
sudo rm -rf dist/
sudo env CI=true npm ci --silent --no-audit --no-fund
sudo env CI=true npm run build

# 4. Data dirs (notes persist outside release)
echo "[4/7] Ensuring data directories..."
NOTES_DIR="${NOTES_DATA_DIR:-/var/lib/projectcea/notes}"
sudo mkdir -p "$NOTES_DIR"
# Service user (automation-service.service); override NOTES_USER/NOTES_GROUP if different
sudo chown -R "${NOTES_USER:-antoine}:${NOTES_GROUP:-antoine}" /var/lib/projectcea 2>/dev/null || true

# 5. Atomic symlink switch
echo "[5/7] Switching symlink..."
sudo ln -sfn "$TARGET" /opt/projectcea/current

# 6. Reload and restart services
echo "[6/7] Restarting services..."
sudo systemctl daemon-reload
sudo systemctl restart can-setup
sleep 1
sudo systemctl restart can-processor cea-backend onewire-worker
sleep 2
sudo systemctl restart automation-service soil-sensor-service weather-service

# 7. Health checks
echo "[7/7] Health checks..."
sleep 3
curl -fsS http://127.0.0.1:8000/health && echo " ✓ backend (8000)"
curl -fsS http://127.0.0.1:8001/health && echo " ✓ automation (8001)"
curl -fsS http://127.0.0.1:8004/health && echo " ✓ onewire-worker (8004)"

# Cleanup old releases (keep MAX_RELEASES)
echo "Cleaning old releases..."
cd "$RELEASES"
ls -1t | tail -n +$((MAX_RELEASES + 1)) | xargs -r sudo rm -rf

echo ""
echo "=== Deploy complete: $RELEASE_ID ==="
echo "Current release: $(readlink /opt/projectcea/current)"
