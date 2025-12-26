#!/bin/bash
# Service Error Handler
# Opens a terminal window with error information when a service fails

SERVICE_NAME="$1"

# Detect desktop environment and open appropriate terminal
if [ -n "$DISPLAY" ]; then
    # Try to detect which terminal to use
    if command -v gnome-terminal &> /dev/null; then
        TERMINAL_CMD="gnome-terminal"
    elif command -v xterm &> /dev/null; then
        TERMINAL_CMD="xterm"
    elif command -v konsole &> /dev/null; then
        TERMINAL_CMD="konsole"
    else
        # Fallback: try to find any terminal
        TERMINAL_CMD=$(which gnome-terminal xterm konsole 2>/dev/null | head -n1)
    fi
    
    if [ -n "$TERMINAL_CMD" ]; then
        # Create temporary script with error information
        TEMP_SCRIPT=$(mktemp /tmp/service_error_XXXXXX.sh)
        
        cat > "$TEMP_SCRIPT" << EOF
#!/bin/bash
clear
echo "=========================================="
echo "SERVICE FAILURE ALERT"
echo "=========================================="
echo ""
echo "Service: $SERVICE_NAME"
echo "Time: $(date)"
echo ""
echo "=========================================="
echo "SERVICE STATUS"
echo "=========================================="
systemctl status $SERVICE_NAME --no-pager -l
echo ""
echo "=========================================="
echo "RECENT LOGS (last 50 lines)"
echo "=========================================="
journalctl -u $SERVICE_NAME -n 50 --no-pager
echo ""
echo "=========================================="
echo "TROUBLESHOOTING COMMANDS"
echo "=========================================="
echo ""
echo "View full logs:"
echo "  journalctl -u $SERVICE_NAME -f"
echo ""
echo "Restart service:"
echo "  sudo systemctl restart $SERVICE_NAME"
echo ""
echo "Check service status:"
echo "  sudo systemctl status $SERVICE_NAME"
echo ""
echo "View service configuration:"
echo "  systemctl cat $SERVICE_NAME"
echo ""
echo "=========================================="
echo "Press any key to close this window..."
echo "=========================================="
read -n 1
EOF
        
        chmod +x "$TEMP_SCRIPT"
        
        # Open terminal with the script
        if [ "$TERMINAL_CMD" = "gnome-terminal" ]; then
            gnome-terminal -- bash -c "$TEMP_SCRIPT; rm -f $TEMP_SCRIPT; exec bash"
        elif [ "$TERMINAL_CMD" = "xterm" ]; then
            xterm -e bash -c "$TEMP_SCRIPT; rm -f $TEMP_SCRIPT; exec bash" &
        elif [ "$TERMINAL_CMD" = "konsole" ]; then
            konsole -e bash -c "$TEMP_SCRIPT; rm -f $TEMP_SCRIPT; exec bash" &
        fi
    else
        # No terminal found, log to syslog
        logger -t service-error-handler "Service $SERVICE_NAME failed. Check logs with: journalctl -u $SERVICE_NAME"
    fi
else
    # No display, log to syslog
    logger -t service-error-handler "Service $SERVICE_NAME failed. Check logs with: journalctl -u $SERVICE_NAME"
fi

