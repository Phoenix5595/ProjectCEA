#!/bin/bash
set -e

SOURCE="/home/antoine/ProjectCEA"
RELEASES="/opt/projectcea/releases"
MAX_RELEASES=10

# Generate release ID
RELEASE_ID=$(date +%Y%m%d-%H%M%S)-$(git -C "$SOURCE" rev-parse --short HEAD 2>/dev/null || echo "nogit")
TARGET="$RELEASES/$RELEASE_ID"

echo "=== Deploying release: $RELEASE_ID ==="

# 1. Copy code to new release
echo "[1/6] Copying code..."
sudo mkdir -p "$TARGET"
sudo rsync -a --delete "$SOURCE/Infrastructure/" "$TARGET/Infrastructure/"
sudo chown -R root:root "$TARGET"

# 2. Build Python venvs
echo "[2/6] Building Python venvs..."
for svc in backend automation-service can-processor-service soil-sensor-service; do
  if [ -f "$TARGET/Infrastructure/$svc/requirements.txt" ]; then
    echo "  - $svc"
    cd "$TARGET/Infrastructure/$svc"
    sudo python3 -m venv .venv
    sudo .venv/bin/pip install -q --upgrade pip
    sudo .venv/bin/pip install -q -r requirements.txt
  fi
done

# 3. Build frontend
echo "[3/6] Building frontend..."
cd "$TARGET/Infrastructure/frontend"
sudo rm -rf dist/
sudo env CI=true npm ci --silent --no-audit --no-fund
sudo env CI=true npm run build

# 4. Atomic symlink switch
echo "[4/6] Switching symlink..."
sudo ln -sfn "$TARGET" /opt/projectcea/current

# 5. Reload and restart services
echo "[5/6] Restarting services..."
sudo systemctl daemon-reload
sudo systemctl restart can-setup
sleep 1
sudo systemctl restart can-processor cea-backend
sleep 2
sudo systemctl restart automation-service soil-sensor-service

# 6. Health checks
echo "[6/6] Health checks..."
sleep 3
curl -fsS http://127.0.0.1:8000/health && echo " ✓ backend (8000)"
curl -fsS http://127.0.0.1:8001/health && echo " ✓ automation (8001)"

# Cleanup old releases (keep MAX_RELEASES)
echo "Cleaning old releases..."
cd "$RELEASES"
ls -1t | tail -n +$((MAX_RELEASES + 1)) | xargs -r sudo rm -rf

echo ""
echo "=== Deploy complete: $RELEASE_ID ==="
echo "Current release: $(readlink /opt/projectcea/current)"
