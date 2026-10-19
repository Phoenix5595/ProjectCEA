-- Intentional no-op. 0001 only records an alembic branch whose schema effects
-- already exist; alembic revisions are never downgraded (see README.md), so the
-- stamp stays true after rolling back SQL migrations.
SELECT 1;
