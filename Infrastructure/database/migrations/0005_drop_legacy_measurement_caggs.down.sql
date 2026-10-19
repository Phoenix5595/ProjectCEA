-- Intentionally a no-op. These Grafana-era aggregates and helper objects
-- were already retired from production, and the replaced retention/compression
-- policies cannot be reconstructed safely from the post-migration catalog.
\set ON_ERROR_STOP on

BEGIN;
COMMIT;
