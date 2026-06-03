# Plan: Atomic Symlink Deployment for ProjectCEA

> Status: READY FOR IMPLEMENTATION  
> Created: 2026-01-10  
> Author: opencode/omo

## Goal

Migrate ProjectCEA from in-place deployment at `/home/antoine/ProjectCEA/` to atomic symlink-based deployment at `/opt/projectcea/` with:
- Per-release venvs for isolation
- Instant rollback capability (<10 seconds)
- Secrets in EnvironmentFile (not inline)
- Zero extended downtime (only ~5-10s restart window)

## Directory Structure (Target)

```
/opt/projectcea/                    # root (owned by root)
├── releases/                       # immutable release directories
│   ├── 20260110-150000-abc1234/
│   │   └── Infrastructure/
│   │       ├── backend/.venv/
│   │       ├── automation-service/.venv/
│   │       ├── can-processor-service/.venv/
│   │       ├── soil-sensor-service/.venv/
│   │       └── frontend/dist/
│   └── 20260110-160000-def5678/
├── current -> releases/20260110-160000-def5678/  # atomic symlink
└── shared/
    └── env/
        └── postgres.env            # POSTGRES_PASSWORD=<value>
```

## Prerequisites

- [ ] Push any unpushed git commits to GitHub (backup)
- [ ] Note current POSTGRES_PASSWORD value from existing units

---

## Phase 0: Pre-Migration Code Fixes

Fix hardcoded paths before deploying to new location.

### Task 0.1: Fix automation-service config path
**File:** `Infrastructure/automation-service/app/config.py`  
**Change:** Make `automation_config.yaml` path relative to `__file__`

### Task 0.2: Fix backend config paths
**File:** `Infrastructure/backend/app/config.py`  
**Change:** Remove hardcoded `/home/antoine/ProjectCEA/...` paths, use relative paths

### Task 0.3: Remove debug.log writes
**Files:** 
- `Infrastructure/automation-service/app/database.py`
- `Infrastructure/automation-service/app/background_tasks.py`
- `Infrastructure/automation-service/app/control/control_engine.py`
- `Infrastructure/automation-service/app/control/sensor_data_manager.py`

**Change:** Remove or comment out writes to `/home/antoine/.cursor/debug.log`

### Task 0.4: Fix missing logging import
**File:** `Infrastructure/backend/app/main.py`  
**Change:** Add `import logging` at top of file

---

## Phase 1: Directory Setup

```bash
# Create directory structure
sudo mkdir -p /opt/projectcea/{releases,shared/env}

# Set ownership (root owns structure, antoine runs services)
sudo chown -R root:root /opt/projectcea
sudo chmod 755 /opt/projectcea /opt/projectcea/releases
sudo chmod 750 /opt/projectcea/shared
sudo chmod 700 /opt/projectcea/shared/env
```

---

## Phase 2: Secrets Migration

```bash
# Get password from existing unit
sudo grep POSTGRES_PASSWORD /etc/systemd/system/automation-service.service | head -1

# Create env file (replace <actual_password_here> with real value)
sudo tee /opt/projectcea/shared/env/postgres.env << 'EOF'
POSTGRES_PASSWORD=<actual_password_here>
EOF

sudo chmod 600 /opt/projectcea/shared/env/postgres.env
sudo chown root:root /opt/projectcea/shared/env/postgres.env
```

---

## Phase 3: Create Deploy Script

**File:** `/home/antoine/ProjectCEA/deploy.sh`

```bash
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
sudo npm ci --silent
sudo npm run build

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
```

Make executable:
```bash
chmod +x /home/antoine/ProjectCEA/deploy.sh
```

---

## Phase 4: Create Rollback Script

**File:** `/home/antoine/ProjectCEA/rollback.sh`

```bash
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
```

Make executable:
```bash
chmod +x /home/antoine/ProjectCEA/rollback.sh
```

---

## Phase 5: Create systemd Drop-in Overrides

Create drop-in overrides for each service to use new paths.

### 5.1: cea-backend

```bash
sudo mkdir -p /etc/systemd/system/cea-backend.service.d
sudo tee /etc/systemd/system/cea-backend.service.d/override.conf << 'EOF'
[Service]
WorkingDirectory=
WorkingDirectory=/opt/projectcea/current/Infrastructure/backend
ExecStart=
ExecStart=/opt/projectcea/current/Infrastructure/backend/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
Environment=
Environment=PYTHONPATH=/opt/projectcea/current/Infrastructure/automation-service
Environment=REDIS_URL=redis://localhost:6379
Environment=POSTGRES_HOST=localhost
Environment=POSTGRES_DB=cea_sensors
Environment=POSTGRES_USER=cea_user
EnvironmentFile=/opt/projectcea/shared/env/postgres.env
EOF
```

### 5.2: automation-service

```bash
sudo mkdir -p /etc/systemd/system/automation-service.service.d
sudo tee /etc/systemd/system/automation-service.service.d/override.conf << 'EOF'
[Service]
WorkingDirectory=
WorkingDirectory=/opt/projectcea/current/Infrastructure/automation-service
ExecStart=
ExecStart=/opt/projectcea/current/Infrastructure/automation-service/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8001
Environment=
Environment=PYTHONPATH=/opt/projectcea/current/Infrastructure/automation-service
Environment=REDIS_URL=redis://localhost:6379
Environment=POSTGRES_HOST=localhost
Environment=POSTGRES_DB=cea_sensors
Environment=POSTGRES_USER=cea_user
EnvironmentFile=/opt/projectcea/shared/env/postgres.env
EOF
```

### 5.3: can-processor

```bash
sudo mkdir -p /etc/systemd/system/can-processor.service.d
sudo tee /etc/systemd/system/can-processor.service.d/override.conf << 'EOF'
[Service]
WorkingDirectory=
WorkingDirectory=/opt/projectcea/current/Infrastructure/can-processor-service
ExecStart=
ExecStart=/opt/projectcea/current/Infrastructure/can-processor-service/.venv/bin/python -m app.main --verbose
Environment=
Environment=PYTHONPATH=/opt/projectcea/current/Infrastructure/automation-service
Environment=REDIS_URL=redis://localhost:6379
Environment=POSTGRES_HOST=localhost
Environment=POSTGRES_DB=cea_sensors
Environment=POSTGRES_USER=cea_user
Environment=CAN_PROCESSOR_DISPLAY=1
EnvironmentFile=/opt/projectcea/shared/env/postgres.env
EOF
```

### 5.4: can-setup

```bash
sudo mkdir -p /etc/systemd/system/can-setup.service.d
sudo tee /etc/systemd/system/can-setup.service.d/override.conf << 'EOF'
[Service]
ExecStart=
ExecStart=/bin/bash /opt/projectcea/current/Infrastructure/can-processor-service/setup_can.sh
EOF
```

### 5.5: soil-sensor-service

```bash
sudo mkdir -p /etc/systemd/system/soil-sensor-service.service.d
sudo tee /etc/systemd/system/soil-sensor-service.service.d/override.conf << 'EOF'
[Service]
WorkingDirectory=
WorkingDirectory=/opt/projectcea/current/Infrastructure/soil-sensor-service
ExecStart=
ExecStart=/opt/projectcea/current/Infrastructure/soil-sensor-service/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8002
Environment=
Environment=PYTHONPATH=/opt/projectcea/current/Infrastructure/automation-service
Environment=REDIS_URL=redis://localhost:6379
Environment=POSTGRES_HOST=localhost
Environment=POSTGRES_DB=cea_sensors
Environment=POSTGRES_USER=cea_user
EnvironmentFile=/opt/projectcea/shared/env/postgres.env
EOF
```

---

## Phase 6: Disable Frontend Dev Server

```bash
sudo systemctl disable cea-frontend.service
sudo systemctl stop cea-frontend.service
```

The frontend is now served by automation-service from the built `dist/` folder.

---

## Phase 7: First Deploy & Verification

```bash
# Run first deploy
cd /home/antoine/ProjectCEA
./deploy.sh

# Verify all services
systemctl status cea-backend automation-service can-processor can-setup soil-sensor-service

# Check for failures
systemctl --failed

# Test endpoints
curl -s http://127.0.0.1:8000/health | jq .
curl -s http://127.0.0.1:8001/health | jq .

# Test frontend loads (from your tower via Tailscale)
# Open browser: http://mothernode:8001

# Check can-processor logs
journalctl -u can-processor -f
```

---

## Success Criteria

- [ ] `cea-backend` running, `curl http://127.0.0.1:8000/health` returns 200
- [ ] `automation-service` running, `curl http://127.0.0.1:8001/health` returns 200
- [ ] `can-processor` running (check `journalctl -u can-processor`)
- [ ] `soil-sensor-service` running (if sensor connected)
- [ ] Frontend loads at `http://mothernode:8001`
- [ ] **Light control works** (critical!)
- [ ] `systemctl --failed` returns empty
- [ ] Rollback works: `./rollback.sh` switches to previous release

---

## Rollback to Old Setup (Emergency)

If everything breaks and you need to go back to the old in-place setup:

```bash
# Remove all drop-in overrides
sudo rm -rf /etc/systemd/system/*.service.d/

# Reload and restart
sudo systemctl daemon-reload
sudo systemctl restart can-setup can-processor cea-backend automation-service

# Re-enable frontend dev server if needed
sudo systemctl enable --now cea-frontend.service
```

---

## Daily Workflow (After Migration)

1. Edit code in `/home/antoine/ProjectCEA/` (via opencode or Cursor)
2. Test locally if possible
3. Deploy: `./deploy.sh`
4. Verify in browser + health checks
5. If broken: `./rollback.sh`
6. If good: commit + push to GitHub

---

## Notes

- **Ownership:** /opt/projectcea is root-owned for security
- **Releases kept:** 10 (configurable in deploy.sh)
- **Secrets:** Stored in /opt/projectcea/shared/env/postgres.env
- **Frontend:** Served by automation-service, no separate service needed
