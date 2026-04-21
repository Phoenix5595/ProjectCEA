#!/bin/bash
set -e
# Sync Grafana dashboards back to JSON files
# Usage: ./sync_dashboards.sh [password]

GRAFANA_URL="${GRAFANA_URL:-http://iskraprojectcea:3001}"
GRAFANA_USER="${GRAFANA_USER:-admin}"
GRAFANA_PASS="${1:-admin}"
DASH_DIR="/home/antoine/ProjectCEA/Infrastructure/frontend/grafana/dashboards"

# Dashboard mappings: UID -> filename
declare -A DASHBOARDS=(
    ["7467103e-9964-4e06-9fc8-c43610129ba9"]="flower_room_dashboard.json"
    ["80bcfd37-f781-48da-aba9-48d3b06a6347"]="vegetation_room_dashboard.json"
)

echo "Syncing Grafana dashboards to files..."

for uid in "${!DASHBOARDS[@]}"; do
    filename="${DASHBOARDS[$uid]}"
    echo -n "  $filename ... "
    
    # Get dashboard JSON and extract just the dashboard object
    response=$(curl -s -u "$GRAFANA_USER:$GRAFANA_PASS" "$GRAFANA_URL/api/dashboards/uid/$uid")
    
    # Check if successful
    if echo "$response" | grep -q '"dashboard"'; then
        echo "$response" | python3 -c "
import sys, json
data = json.load(sys.stdin)
dashboard = data.get('dashboard', {})
# Remove runtime fields
dashboard.pop('id', None)
dashboard.pop('version', None)
print(json.dumps(dashboard, indent=2))
" > "$DASH_DIR/$filename"
        echo "OK"
    else
        echo "FAILED: $response"
    fi
done

echo "Done! Files saved to $DASH_DIR"
