#!/bin/bash
# PostgreSQL and TimescaleDB Installation and Configuration Script

set -e

echo "=========================================="
echo "PostgreSQL and TimescaleDB Installation"
echo "=========================================="

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo "Please run as root (use sudo)"
    exit 1
fi

# Update package list
echo "Updating package list..."
apt update

# Install PostgreSQL
echo "Installing PostgreSQL..."
apt install -y postgresql postgresql-contrib

# Install TimescaleDB repository
echo "Adding TimescaleDB repository..."
# For Raspberry Pi / Debian-based systems
sh -c "echo 'deb https://packagecloud.io/timescale/timescaledb/debian/ $(lsb_release -c -s) main' > /etc/apt/sources.list.d/timescaledb.list"
wget --quiet -O - https://packagecloud.io/timescale/timescaledb/gpgkey | apt-key add -

# Update package list again
apt update

# Install TimescaleDB
echo "Installing TimescaleDB..."
apt install -y timescaledb-2-postgresql-$(psql --version | grep -oP '\d+' | head -1)

# Tune TimescaleDB
echo "Tuning TimescaleDB..."
timescaledb-tune --quiet --yes

# Start and enable PostgreSQL
echo "Starting PostgreSQL service..."
systemctl enable postgresql
systemctl restart postgresql

# Wait for PostgreSQL to be ready
sleep 3

# Create database and user
echo "Creating database and user..."
sudo -u postgres psql <<EOF
-- Create database
CREATE DATABASE cea_sensors;

-- Create user (adjust password as needed)
CREATE USER cea_user WITH PASSWORD 'cea_password_change_me';

-- Grant privileges
GRANT ALL PRIVILEGES ON DATABASE cea_sensors TO cea_user;

-- Connect to database and create extension
\c cea_sensors
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Grant schema privileges
GRANT ALL ON SCHEMA public TO cea_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO cea_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO cea_user;
EOF

echo ""
echo "=========================================="
echo "PostgreSQL and TimescaleDB installation complete!"
echo "=========================================="
echo "Database: cea_sensors"
echo "User: cea_user"
echo "Password: cea_password_change_me (CHANGE THIS!)"
echo ""
echo "To change password:"
echo "  sudo -u postgres psql -c \"ALTER USER cea_user WITH PASSWORD 'your_new_password';\""
echo ""
echo "Test connection:"
echo "  psql -h localhost -U cea_user -d cea_sensors"
echo ""
echo "=========================================="
echo "Post-Install Optimization (Recommended)"
echo "=========================================="
echo "After creating your normalized tables, apply optimizations:"
echo ""
echo "1. Create normalized tables:"
echo "   psql -h localhost -U cea_user -d cea_sensors -f /home/antoine/Project\\ CEA/Infrastructure/database/create_normalized_tables.sql"
echo "   (Note: Migration from can-worker to unified architecture complete)"
echo ""
echo "2. Enable compression (90-day threshold):"
echo "   psql -h localhost -U cea_user -d cea_sensors -f /home/antoine/Project\\ CEA/Infrastructure/database/timescaledb_compression.sql"
echo ""
echo "3. Create continuous aggregates:"
echo "   psql -h localhost -U cea_user -d cea_sensors -f /home/antoine/Project\\ CEA/Infrastructure/database/timescaledb_continuous_aggregates.sql"
echo ""
echo "See Infrastructure/database/TIMESCALEDB_OPTIMIZATION.md for details."
echo "=========================================="

