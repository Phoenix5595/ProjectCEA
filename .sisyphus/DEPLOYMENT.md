# ProjectCEA Deployment Pipeline

## Quick Deploy
1. git add -A && git commit -m "msg" && git push ProjectCEA main
2. RELEASE=/opt/projectcea/releases/release-$(date +%Y%m%d-%H%M%S) && sudo mkdir -p $RELEASE && sudo cp -r /home/antoine/ProjectCEA/* $RELEASE/ && sudo ln -sfn $RELEASE /opt/projectcea/current
3. Create venvs: sudo python3 -m venv /opt/.../automation-service/.venv && pip install -r requirements.txt
4. sudo systemctl restart automation-service cea-backend cea-frontend

## Health Checks
- curl localhost:8001/health (automation)
- curl localhost:8000/health (backend)

## Rollback
sudo ln -sfn /opt/projectcea/releases/PREVIOUS /opt/projectcea/current

## Logs
sudo journalctl -xeu SERVICE --no-pager -n 50
