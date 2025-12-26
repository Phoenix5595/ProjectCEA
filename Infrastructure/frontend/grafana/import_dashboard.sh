#!/bin/bash
# Import CEA Sensors Dashboard to Grafana
# Usage: ./import_dashboard.sh [grafana_url] [admin_password]

GRAFANA_URL="${1:-http://localhost:3000}"
GRAFANA_USER="${GRAFANA_USER:-admin}"
GRAFANA_PASS="${2:-admin}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DASHBOARD_FILE="$SCRIPT_DIR/dashboards/cea_sensors_example.json"

if [ ! -f "$DASHBOARD_FILE" ]; then
    echo "Error: Dashboard file not found: $DASHBOARD_FILE"
    exit 1
fi

echo "=========================================="
echo "Importing CEA Sensors Dashboard"
echo "=========================================="
echo "Grafana URL: $GRAFANA_URL"
echo "Dashboard file: $DASHBOARD_FILE"
echo ""

# Test Grafana connection
echo "Testing Grafana connection..."
if ! curl -s -u "$GRAFANA_USER:$GRAFANA_PASS" "$GRAFANA_URL/api/health" > /dev/null 2>&1; then
    echo "Error: Cannot connect to Grafana at $GRAFANA_URL"
    echo "Make sure Grafana is running and credentials are correct"
    exit 1
fi
echo "✓ Connected to Grafana"
echo ""

# Import dashboard
echo "Importing dashboard..."
RESPONSE=$(curl -s -X POST \
  -H "Content-Type: application/json" \
  -u "$GRAFANA_USER:$GRAFANA_PASS" \
  -d @"$DASHBOARD_FILE" \
  "$GRAFANA_URL/api/dashboards/db")

# Check if import was successful
if echo "$RESPONSE" | grep -q '"status":"success"'; then
    echo "✓ Dashboard imported successfully!"
    DASHBOARD_URL=$(echo "$RESPONSE" | python3 -c "import sys, json; data=json.load(sys.stdin); print(data.get('url', ''))" 2>/dev/null)
    if [ -n "$DASHBOARD_URL" ]; then
        echo ""
        echo "Dashboard URL: $GRAFANA_URL$DASHBOARD_URL"
    fi
else
    echo "Error importing dashboard:"
    echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"
    exit 1
fi

echo ""
echo "=========================================="
echo "Done! Open Grafana to view your dashboard"
echo "=========================================="

