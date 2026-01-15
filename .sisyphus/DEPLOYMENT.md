# ProjectCEA Deployment Guide

## Current Architecture

| Location | Purpose |
|----------|---------|
| `/home/antoine/ProjectCEA/` | Git repo (source code) |
| `/opt/projectcea/current/` | Deployed code (what services run) |
| `/opt/projectcea/releases/` | Release history |

**CRITICAL: Changes to git repo are NOT automatically deployed!**

---

## Recommended Workflow: GitHub + Auto-Deploy

### Option A: GitHub Actions (Recommended)
1. Push to GitHub
2. GitHub Actions runs tests
3. SSH to Pi and deploy automatically

### Option B: Git Hook on Pi
1. Push to GitHub
2. Pi pulls from GitHub on schedule or webhook
3. Auto-deploy script runs

---

## Current Manual Workflow

### Step 1: Make changes in git repo
```bash
cd /home/antoine/ProjectCEA
# Edit files...
git add -A && git commit -m "message" && git push ProjectCEA main
```

### Step 2: Deploy to production
```bash
sudo bash /opt/projectcea/scripts/deploy.sh
```

### Step 3: If deploy fails, manual deploy
```bash
RELEASE_DIR=/opt/projectcea/releases/$(date +%Y%m%d-%H%M%S)-$(git rev-parse --short HEAD)
sudo mkdir -p $RELEASE_DIR/Infrastructure

# Copy code
sudo cp -a /home/antoine/ProjectCEA/Infrastructure/automation-service $RELEASE_DIR/Infrastructure/
sudo cp -a /home/antoine/ProjectCEA/Infrastructure/backend $RELEASE_DIR/Infrastructure/
sudo cp -a /home/antoine/ProjectCEA/Infrastructure/can-processor-service $RELEASE_DIR/Infrastructure/
sudo cp -a /home/antoine/ProjectCEA/Infrastructure/frontend $RELEASE_DIR/Infrastructure/

# Copy venvs from previous release
PREV=$(readlink /opt/projectcea/current)
sudo cp -a $PREV/Infrastructure/automation-service/.venv $RELEASE_DIR/Infrastructure/automation-service/
sudo cp -a $PREV/Infrastructure/backend/.venv $RELEASE_DIR/Infrastructure/backend/
sudo cp -a $PREV/Infrastructure/can-processor-service/.venv $RELEASE_DIR/Infrastructure/can-processor-service/

# Update symlink and restart
sudo ln -sfn $RELEASE_DIR /opt/projectcea/current
sudo systemctl restart automation-service can-processor cea-backend cea-frontend grafana-server
```

---

## Service Ports

| Service | Port |
|---------|------|
| cea-backend (frontend) | 8000 |
| automation-service (API) | 8001 |
| grafana-server | 3000 |

---

## Rollback
```bash
ls /opt/projectcea/releases/  # List releases
sudo ln -sfn /opt/projectcea/releases/YYYYMMDD-HHMMSS-sha /opt/projectcea/current
sudo systemctl restart automation-service can-processor cea-backend cea-frontend
```
