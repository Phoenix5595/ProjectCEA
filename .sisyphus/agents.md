# Mothernode Development Workflow

## Overview

Mothernode is a Raspberry Pi running ProjectCEA. Development happens in /home/antoine/ProjectCEA/ and production runs from /opt/projectcea/current/.

## Git Branches

- **dev branch** → /home/antoine/ProjectCEA/ (development work)
- **master branch** → /opt/projectcea/current/ (production)

## Key Paths

- Development: /home/antoine/ProjectCEA/
- Production: /opt/projectcea/current/ (symlink to latest release)
- Releases: /opt/projectcea/releases/TIMESTAMP-SHA/
- Shared secrets: /opt/projectcea/shared/env/

## Deployment Procedure

### Deploy Changes (Dev to Production)

```bash
ssh mothernode "cd /home/antoine/ProjectCEA && ./deploy.sh"
```

What deploy.sh does:
1. Copies dev code to /opt/projectcea/releases/NEW/
2. Builds Python venvs for each service
3. Builds frontend (npm run build)
4. Switches /opt/projectcea/current symlink atomically
5. Restarts all services
6. Runs health checks

### Rollback (If Something Breaks)

```bash
ssh mothernode "cd /home/antoine/ProjectCEA && ./rollback.sh"
```

What rollback.sh does:
1. Switches symlink back to previous release (about 10 seconds)
2. Restarts services
3. No code changes needed

### Check Current Release

```bash
ssh mothernode "readlink /opt/projectcea/current"
```

### List All Releases

```bash
ssh mothernode "ls -la /opt/projectcea/releases/"
```

## Service Management

### Services

- cea-backend (port 8000): Main FastAPI API
- automation-service (port 8001): Automation + Frontend SPA
- can-processor: CAN bus processor
- grafana-server (port 3000): Monitoring dashboards

### Common Commands

```bash
# Check status
ssh mothernode "systemctl status automation-service cea-backend"

# View logs (live)
ssh mothernode "journalctl -u automation-service -f"

# Restart a service
ssh mothernode "sudo systemctl restart automation-service"

# Health checks
ssh mothernode "curl -fsS http://127.0.0.1:8000/health && curl -fsS http://127.0.0.1:8001/health"
```

## Important Notes

1. Always edit in dev (/home/antoine/ProjectCEA/), then deploy
2. Service restarts use production code - editing dev alone does not affect running services
3. Rollback is fast - do not hesitate to rollback if something breaks
4. Keep last 10 releases - older ones are auto-cleaned by deploy.sh
5. On service restart mid-ramp - ramps are cancelled, final setpoints applied immediately (by design)
