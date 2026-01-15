# Grafana Dashboard Management

## Dashboard Location
- **Repo**: `/home/antoine/ProjectCEA/Infrastructure/frontend/grafana/dashboards/`
- **Deployed**: `/var/lib/grafana/dashboards/`
- **Provisioning**: `/etc/grafana/provisioning/dashboards/`

## Deployment Process (What Works)

### 1. Edit the JSON file in repo
```bash
# Edit using Python for reliable JSON manipulation
python3 << 'EOF'
import json

with open('/home/antoine/ProjectCEA/Infrastructure/frontend/grafana/dashboards/flower_room_dashboard.json') as f:
    d = json.load(f)

# Make changes to d...

with open('/home/antoine/ProjectCEA/Infrastructure/frontend/grafana/dashboards/flower_room_dashboard.json', 'w') as f:
    json.dump(d, f, indent=2)
EOF
```

### 2. Deploy to Grafana
```bash
sudo cp /home/antoine/ProjectCEA/Infrastructure/frontend/grafana/dashboards/flower_room_dashboard.json \
       /var/lib/grafana/dashboards/flower_room_dashboard.json
```

### 3. Force Grafana to reload (if changes don't appear)
```bash
sudo systemctl stop grafana-server
sudo sqlite3 /var/lib/grafana/grafana.db "DELETE FROM dashboard WHERE title LIKE '%Flower%';"
sudo systemctl start grafana-server
```

## Common Fixes

### Fix: Panels showing "time" and "A-series" instead of data
**Cause**: Missing default datasource in Grafana
**Fix**: Set CEA Sensors as default datasource
```bash
sudo sqlite3 /var/lib/grafana/grafana.db "UPDATE data_source SET is_default = 1 WHERE uid = 'bf6vebq5ipybke';"
sudo systemctl restart grafana-server
```

### Fix: Modify fillOpacity for specific series
```python
import json

with open('/home/antoine/ProjectCEA/Infrastructure/frontend/grafana/dashboards/flower_room_dashboard.json') as f:
    d = json.load(f)

series_to_fix = ['Heating Setpoint - Main', 'Cooling Setpoint - Main']

for p in d.get('panels', []):
    if p.get('type') == 'timeseries':
        for o in p.get('fieldConfig', {}).get('overrides', []):
            series_name = o.get('matcher', {}).get('options', '')
            if series_name in series_to_fix:
                for prop in o.get('properties', []):
                    if prop.get('id') == 'custom.fillOpacity':
                        prop['value'] = 0  # Set desired opacity

with open('/home/antoine/ProjectCEA/Infrastructure/frontend/grafana/dashboards/flower_room_dashboard.json', 'w') as f:
    json.dump(d, f, indent=2)
```

## Datasources
| UID | Name | Type | Default |
|-----|------|------|---------|
| bf6vebq5ipybke | CEA Sensors | PostgreSQL | Yes |
| bf9yw6nuqt81sa | Redis | Redis | No |

## Panel Types
- **timeseries**: Graphs (Temperature, RH & VPD, CO2 & Pressure)
- **table**: Cluster tables (Averages, Front Cluster, Back Cluster)
- **stat**: Statistics panel

## Important Notes
- Panels with `datasource: null` rely on the **default datasource** being set
- `allowUiUpdates: true` in provisioning means UI changes save to Grafana DB, not file
- Always delete dashboard from DB before deploying to force fresh reload from file
