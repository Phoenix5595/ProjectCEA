#!/bin/bash
# Redis Restart Script with AOF Fix
# This script attempts to fix AOF corruption and restart Redis

LOG_FILE="/var/log/redis/restart-fix.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

log "Redis failed to start, attempting AOF fix..."

# Run the AOF fix script
if /usr/local/bin/redis-aof-fix.sh >> "$LOG_FILE" 2>&1; then
    log "AOF fix completed, attempting to start Redis..."
    sleep 2
    
    # Try to start Redis again
    if systemctl start redis-server.service; then
        log "Redis started successfully after AOF fix"
        exit 0
    else
        log "Redis still failed to start after AOF fix"
        exit 1
    fi
else
    log "AOF fix script failed"
    exit 1
fi




















