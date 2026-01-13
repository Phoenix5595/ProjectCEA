# ProjectCEA Agent Reference

## Environment Locations

| Environment | Path | Purpose |
|-------------|------|---------|
| **DEV** | /home/antoine/ProjectCEA/ | Development environment - user workspace |
| **LIVE/PROD** | /opt/projectcea/ | Production environment - deployed releases |

## Directory Structure

### DEV (/home/antoine/ProjectCEA/)
- Source code and active development
- Git repository root
- Run tests and experiments here

### PROD (/opt/projectcea/)
- Deployment target
- Uses release-based structure with symlinks
- /opt/projectcea/current/ -> active release
- /opt/projectcea/releases/ -> versioned deployments
- /opt/projectcea/shared/ -> persistent data (env files, logs)

## Services
All systemd services run from PROD (/opt/projectcea/):
- cea-backend
- cea-frontend
- automation-service
- can-processor
- soil-sensor-service

## Deployment Flow
1. Develop in DEV (/home/antoine/ProjectCEA/)
2. Commit and push to GitHub
3. Deploy to PROD using deploy.sh
