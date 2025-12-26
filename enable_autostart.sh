#!/bin/bash
# Enable Autostart for All CEA Services
# This script enables all services to start automatically on boot

echo "=========================================="
echo "Enabling Autostart for CEA Services"
echo "=========================================="
echo ""

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Services to enable
SERVICES=(
    "redis-server"
    "postgresql"
    "can-setup"
    "can-processor"
    "soil-sensor-service"
    "cea-backend"
    "automation-service"
    "grafana-server"
)

# Track results
ALREADY_ENABLED=()
NEWLY_ENABLED=()
FAILED=()

# Function to enable a service
enable_service() {
    local service=$1
    
    # Check if service is already enabled
    if systemctl is-enabled "$service" >/dev/null 2>&1; then
        ALREADY_ENABLED+=("$service")
        echo -e "${YELLOW}  $service: already enabled${NC}"
        return 0
    fi
    
    # Try to enable the service
    if sudo systemctl enable "$service" >/dev/null 2>&1; then
        NEWLY_ENABLED+=("$service")
        echo -e "${GREEN}  $service: enabled${NC}"
        return 0
    else
        FAILED+=("$service")
        echo -e "${RED}  $service: failed to enable${NC}"
        return 1
    fi
}

# Enable each service
echo "Enabling services..."
echo ""

for service in "${SERVICES[@]}"; do
    enable_service "$service"
done

echo ""
echo "=========================================="
echo "Summary"
echo "=========================================="
echo ""

if [ ${#ALREADY_ENABLED[@]} -gt 0 ]; then
    echo -e "${YELLOW}Already enabled (${#ALREADY_ENABLED[@]}):${NC}"
    for service in "${ALREADY_ENABLED[@]}"; do
        echo "  - $service"
    done
    echo ""
fi

if [ ${#NEWLY_ENABLED[@]} -gt 0 ]; then
    echo -e "${GREEN}Newly enabled (${#NEWLY_ENABLED[@]}):${NC}"
    for service in "${NEWLY_ENABLED[@]}"; do
        echo "  - $service"
    done
    echo ""
fi

if [ ${#FAILED[@]} -gt 0 ]; then
    echo -e "${RED}Failed to enable (${#FAILED[@]}):${NC}"
    for service in "${FAILED[@]}"; do
        echo "  - $service"
        echo "    Check if service file exists:"
        echo "      ls /etc/systemd/system/${service}.service"
        echo "    Check service status:"
        echo "      sudo systemctl status $service"
    done
    echo ""
fi

# Verify all services are enabled
echo "Verifying enabled status..."
echo ""
ALL_ENABLED=true
for service in "${SERVICES[@]}"; do
    if systemctl is-enabled "$service" >/dev/null 2>&1; then
        echo -e "${GREEN}✓${NC} $service: enabled"
    else
        echo -e "${RED}✗${NC} $service: not enabled"
        ALL_ENABLED=false
    fi
done

echo ""
if [ "$ALL_ENABLED" = true ]; then
    echo -e "${GREEN}=========================================="
    echo "All services enabled successfully!"
    echo "==========================================${NC}"
    echo ""
    echo "Services will now start automatically on boot."
    echo ""
    echo "To verify, reboot and check:"
    echo "  sudo systemctl status <service-name>"
else
    echo -e "${YELLOW}=========================================="
    echo "Some services failed to enable"
    echo "==========================================${NC}"
    echo ""
    echo "Please check the failed services above."
    echo "You may need to:"
    echo "  1. Install service files to /etc/systemd/system/"
    echo "  2. Run: sudo systemctl daemon-reload"
    echo "  3. Run this script again"
fi

