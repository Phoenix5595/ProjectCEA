-- Run on mothernode as postgres superuser to create replication user.
-- Mothernode runs PostgreSQL natively (systemd); no Docker on mothernode.
-- Usage: sudo -u postgres psql -d cea_sensors -f mothernode_replication_user.sql
-- Then set the same password in iskra .env as REPLICATION_PASSWORD.

CREATE USER cea_repl WITH REPLICATION PASSWORD 'CHANGE_ME_secure_password';

-- After running this, add to pg_hba.conf (see README):
--   host replication cea_repl <iskra_ip>/32 scram-sha-256
