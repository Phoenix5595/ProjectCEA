#!/bin/bash
#
# CAN Interface Setup Script
# Initializes can0 interface with proper bitrate
#

CAN_INTERFACE="can0"
CAN_BITRATE="250000"

# Check if interface exists
if ! ip link show "$CAN_INTERFACE" &>/dev/null; then
    echo "ERROR: CAN interface '$CAN_INTERFACE' does not exist"
    exit 1
fi

# Always reset the interface to clear any error states
# Bring interface down first (if it exists and is up)
if ip link show "$CAN_INTERFACE" 2>/dev/null | grep -q "state UP"; then
    echo "Bringing down CAN interface '$CAN_INTERFACE' to reset..."
    ip link set "$CAN_INTERFACE" down 2>/dev/null || true
    sleep 0.5
fi

# Bring interface up with proper bitrate
echo "Bringing up CAN interface '$CAN_INTERFACE'..."
ip link set "$CAN_INTERFACE" up type can bitrate "$CAN_BITRATE"

if [ $? -eq 0 ]; then
    echo "CAN interface '$CAN_INTERFACE' initialized successfully (bitrate: $CAN_BITRATE)"
else
    echo "ERROR: Failed to initialize CAN interface '$CAN_INTERFACE'"
    exit 1
fi






