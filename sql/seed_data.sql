-- =============================================================
-- seed_data.sql — Load sample PaySim transactions into DB
-- =============================================================
-- Run this after init.sql to populate the DB with real data.
-- The CSV file must be accessible by the PostgreSQL server.
--
-- From psql:
--   \i sql/seed_data.sql
--
-- Or via Docker:
--   docker exec -i paysim-postgres psql -U paysim_user -d paysim_fintech < sql/seed_data.sql
-- =============================================================

-- Only seed if table is empty (idempotent)
DO $$
DECLARE
    row_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO row_count FROM paysim_raw_transactions;
    IF row_count > 0 THEN
        RAISE NOTICE 'Table already has % rows. Skipping seed.', row_count;
        RETURN;
    END IF;

    RAISE NOTICE 'Table is empty. Loading sample data...';
END $$;

-- Load from CSV (path relative to PostgreSQL data dir — use COPY FROM STDIN for Docker)
-- The Python data_seeder.py handles this via psycopg2 copy_from() which is more portable.

-- After seeding, create an index for faster queries
CREATE INDEX IF NOT EXISTS idx_txn_type ON paysim_raw_transactions(type);
CREATE INDEX IF NOT EXISTS idx_txn_fraud ON paysim_raw_transactions(isFraud);
CREATE INDEX IF NOT EXISTS idx_txn_amount ON paysim_raw_transactions(amount);

-- Create a materialized summary view for quick health checks
CREATE MATERIALIZED VIEW IF NOT EXISTS transaction_health_summary AS
SELECT
    COUNT(*)                                     AS total_rows,
    COUNT(*) FILTER (WHERE isFraud = 1)          AS fraud_count,
    ROUND(100.0 * COUNT(*) FILTER (WHERE isFraud = 1) / COUNT(*), 4) AS fraud_rate_pct,
    COUNT(DISTINCT type)                         AS transaction_types,
    ROUND(AVG(amount)::numeric, 2)               AS avg_amount,
    ROUND(MAX(amount)::numeric, 2)               AS max_amount,
    NOW()                                        AS last_refreshed
FROM paysim_raw_transactions;

COMMENT ON MATERIALIZED VIEW transaction_health_summary IS
    'Quick health summary of transaction data. Refresh with: REFRESH MATERIALIZED VIEW transaction_health_summary';
