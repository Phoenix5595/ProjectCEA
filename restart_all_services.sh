#!/bin/bash
# Restart All CEA Services in Correct Dependency Order
# This ensures services restart properly without dependency issues

echo "=========================================="
echo "Restarting All CEA Services"
echo "=========================================="
echo "Restarting in dependency order to avoid issues..."
echo ""

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Function to restart and verify service
restart_service() {
    local service=$1
    local description=$2
    
    echo -e "${YELLOW}Restarting $description ($service)...${NC}"
    sudo systemctl restart "$service"
    
    # Wait a moment for service to start
    sleep 2
    
    # Check if service is active
    if systemctl is-active --quiet "$service"; then
        echo -e "  ${GREEN}✓ $service is active${NC}"
    else
        echo -e "  ${RED}✗ $service failed to start${NC}"
        echo "  Check logs: sudo journalctl -u $service -n 20"
    fi
    echo ""
}

# Restart in dependency order (reverse of startup order)
# Stop in reverse order, start in forward order

echo "Step 1: Stopping services (reverse dependency order)..."
echo ""

# Stop services that depend on others first
sudo systemctl stop automation-service.service 2>/dev/null
sleep 1

sudo systemctl stop cea-backend.service 2>/dev/null
sleep 1

sudo systemctl stop soil-sensor-service.service 2>/dev/null
sleep 1

sudo systemctl stop can-processor.service 2>/dev/null
sleep 1

sudo systemctl stop can-setup.service 2>/dev/null
sleep 1

sudo systemctl stop grafana-server.service 2>/dev/null
sleep 1

echo "Step 2: Starting services (dependency order)..."
echo ""

# Start in correct dependency order
restart_service "can-setup.service" "CAN Setup"
restart_service "can-processor.service" "CAN Processor"
restart_service "soil-sensor-service.service" "Soil Sensor Service"
restart_service "cea-backend.service" "CEA Backend"
restart_service "automation-service.service" "Automation Service"
restart_service "grafana-server.service" "Grafana Server"

echo "=========================================="
echo "Service Status Summary"
echo "=========================================="
echo ""

# Check all services
services=("redis-server" "postgresql" "can-setup" "can-processor" "soil-sensor-service" "cea-backend" "automation-service" "grafana-server")

for service in "${services[@]}"; do
    if systemctl is-active --quiet "$service.service"; then
        echo -e "${GREEN}✓${NC} $service: active"
    else
        echo -e "${RED}✗${NC} $service: inactive"
    fi
done

echo ""
echo "=========================================="
echo "Done!"
echo "=========================================="
echo ""
echo "To view logs:"
echo "  sudo journalctl -u <service-name> -f"
echo ""
echo "To check specific service:"
echo "  sudo systemctl status <service-name>"


