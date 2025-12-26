#!/bin/bash
# Redis Installation and Configuration Script
# Installs Redis server with AOF + RDB persistence

set -e

echo "=========================================="
echo "Redis Installation and Configuration"
echo "=========================================="

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo "Please run as root (use sudo)"
    exit 1
fi

# Update package list
echo "Updating package list..."
apt update

# Install Redis
echo "Installing Redis server..."
apt install -y redis-server

# Backup original config
if [ ! -f /etc/redis/redis.conf.backup ]; then
    echo "Backing up original Redis configuration..."
    cp /etc/redis/redis.conf /etc/redis/redis.conf.backup
fi

# Configure Redis for AOF + RDB persistence
echo "Configuring Redis for AOF + RDB persistence..."

# Enable AOF (Append-Only File)
sed -i 's/^# appendonly no/appendonly yes/' /etc/redis/redis.conf
sed -i 's/^appendonly no/appendonly yes/' /etc/redis/redis.conf

# Set AOF fsync policy (everysec is a good balance)
sed -i 's/^# appendfsync everysec/appendfsync everysec/' /etc/redis/redis.conf
sed -i 's/^appendfsync .*/appendfsync everysec/' /etc/redis/redis.conf

# Enable RDB snapshots
sed -i 's/^save 900 1/save 900 1/' /etc/redis/redis.conf
sed -i 's/^save 300 10/save 300 10/' /etc/redis/redis.conf
sed -i 's/^save 60 10000/save 60 10000/' /etc/redis/redis.conf

# Bind to localhost only (for security)
sed -i 's/^# bind 127.0.0.1/bind 127.0.0.1/' /etc/redis/redis.conf
sed -i 's/^bind .*/bind 127.0.0.1/' /etc/redis/redis.conf

# Set max memory (optional, adjust based on your needs)
# Uncomment and adjust if needed:
# sed -i 's/^# maxmemory <bytes>/maxmemory 256mb/' /etc/redis/redis.conf

# Enable and start Redis service
echo "Enabling and starting Redis service..."
systemctl enable redis-server
systemctl restart redis-server

# Wait a moment for Redis to start
sleep 2

# Verify Redis is running
echo "Verifying Redis installation..."
if redis-cli ping | grep -q "PONG"; then
    echo "✅ Redis is running successfully!"
    redis-cli INFO server | grep redis_version
else
    echo "❌ Redis failed to start. Check logs with: journalctl -u redis-server"
    exit 1
fi

# Test persistence
echo "Testing persistence configuration..."
redis-cli CONFIG GET appendonly | grep -q "yes" && echo "✅ AOF enabled" || echo "❌ AOF not enabled"
redis-cli CONFIG GET save | grep -q "900" && echo "✅ RDB snapshots enabled" || echo "❌ RDB not configured"

echo ""
echo "=========================================="
echo "Redis installation complete!"
echo "=========================================="
echo "Configuration file: /etc/redis/redis.conf"
echo "Test with: redis-cli ping"
echo "Monitor with: redis-cli MONITOR"
echo "=========================================="

