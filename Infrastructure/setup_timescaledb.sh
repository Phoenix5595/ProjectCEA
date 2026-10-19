#!/bin/bash
# PostgreSQL and TimescaleDB Installation and Configuration Script

set -e

echo "=========================================="
echo "PostgreSQL and TimescaleDB Installation"
echo "=========================================="

if [ "$EUID" -ne 0 ]; then 
    echo "Please run as root (use sudo)"
    exit 1
fi

# Required: cea_user DB password. No default — refusing to install with a known-leaked value.
# Generate one with:  openssl rand -base64 24
if [ -z "${CEA_USER_PASSWORD:-}" ]; then
    echo "ERROR: CEA_USER_PASSWORD env var is required."
    echo "  Generate with: openssl rand -base64 24"
    echo "  Then re-run:   sudo CEA_USER_PASSWORD='<generated>' $0"
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
sudo -u postgres psql --set=cea_pw="$CEA_USER_PASSWORD" <<'EOF'
-- Create database
CREATE DATABASE sj_cea;

-- Create user with operator-supplied password (no plaintext default in this script)
\set quoted_pw '\'' :cea_pw '\''
CREATE USER cea_user WITH PASSWORD :quoted_pw;

GRANT ALL PRIVILEGES ON DATABASE sj_cea TO cea_user;

-- Connect to database and create extension
\c sj_cea
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
echo "Database: sj_cea"
echo "User: cea_user"
echo "Password: <set via CEA_USER_PASSWORD env var at install time>"
echo ""
echo "Persist the password where systemd units can read it:"
echo "  sudo install -d -m 0700 /opt/projectcea/shared/env"
echo "  echo \"POSTGRES_PASSWORD=\$CEA_USER_PASSWORD\" | sudo install -m 0600 /dev/stdin /opt/projectcea/shared/env/postgres.env"
echo ""
echo "To rotate later:"
echo "  NEW_PW=\$(openssl rand -base64 24)"
echo "  sudo -u postgres psql -c \"ALTER USER cea_user WITH PASSWORD '\$NEW_PW';\""
echo "  echo \"POSTGRES_PASSWORD=\$NEW_PW\" | sudo install -m 0600 /dev/stdin /opt/projectcea/shared/env/postgres.env"
echo "  sudo systemctl restart automation-service cea-backend soil-sensor-service weather-service onewire-worker can-processor"
echo ""
echo "Test connection:"
echo "  psql -h localhost -U cea_user -d sj_cea"
echo ""
echo "=========================================="
echo "Post-Install Database Setup (Recommended)"
echo "=========================================="
echo "Apply the normalized schema:"
echo ""
echo "1. Create normalized tables:"
echo "   psql -h localhost -U cea_user -d sj_cea -f /home/antoine/Project\\ CEA/Infrastructure/database/create_normalized_tables.sql"
echo "   (Note: Migration from can-worker to unified architecture complete)"
echo ""
echo "=========================================="

