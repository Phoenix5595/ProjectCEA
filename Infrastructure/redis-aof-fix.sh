#!/bin/bash
# Redis AOF Corruption Fix Script
# This script checks and fixes corrupted AOF files before Redis starts

AOF_DIR="/var/lib/redis/appendonlydir"
LOG_FILE="/var/log/redis/aof-fix.log"

# Create log file if it doesn't exist
mkdir -p /var/log/redis
touch "$LOG_FILE"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

log "Checking AOF files for corruption..."

# Check if AOF directory exists
if [ ! -d "$AOF_DIR" ]; then
    log "AOF directory does not exist: $AOF_DIR"
    exit 0
fi

# Find manifest file
MANIFEST_FILE="$AOF_DIR/appendonly.aof.manifest"
if [ ! -f "$MANIFEST_FILE" ]; then
    log "No manifest file found, skipping AOF check"
    exit 0
fi

# Check for incremental AOF files mentioned in manifest
INCR_FILES=$(grep -E "\.incr\.aof" "$MANIFEST_FILE" 2>/dev/null | awk '{print $2}' || true)

if [ -z "$INCR_FILES" ]; then
    log "No incremental AOF files found in manifest"
    exit 0
fi

# Check each incremental AOF file
for INCR_FILE in $INCR_FILES; do
    FULL_PATH="$AOF_DIR/$INCR_FILE"
    
    if [ ! -f "$FULL_PATH" ]; then
        log "AOF file not found: $FULL_PATH"
        continue
    fi
    
    # Try to check if the file is valid
    # If redis-check-aof fails, the file is corrupted
    if ! redis-check-aof "$FULL_PATH" >/dev/null 2>&1; then
        log "WARNING: Corrupted AOF file detected: $INCR_FILE"
        log "Attempting to fix..."
        
        # Fix the AOF file (non-interactive)
        if echo "y" | redis-check-aof --fix "$FULL_PATH" >> "$LOG_FILE" 2>&1; then
            log "Successfully fixed AOF file: $INCR_FILE"
        else
            log "ERROR: Failed to fix AOF file: $INCR_FILE"
            # Don't exit with error - let Redis try to start anyway
            # Redis might be able to recover from the base RDB file
        fi
    else
        log "AOF file is valid: $INCR_FILE"
    fi
done

log "AOF check complete"
exit 0




















