-- =============================================================
-- drift_scenarios.sql — Preset Schema Drift Scripts
-- =============================================================
-- These scripts simulate realistic schema drift incidents in a
-- FinTech fraud detection pipeline. Each scenario corresponds to
-- a real-world mistake an engineer might make.
--
-- IMPORTANT: Each scenario also inserts a record into schema_change_log
-- so the AI agent can trace when and why the change occurred.
--
-- Usage (in psql):
--   -- Apply Scenario A:
--   \i sql/drift_scenarios.sql
--   SELECT apply_drift_scenario_a('bambang_engineer', 'ETL refactor — changed amount to VARCHAR by mistake');
--
--   -- Reset all:
--   SELECT reset_all_drifts();
-- =============================================================

-- =============================================================
-- Helper: Log a schema change
-- =============================================================
CREATE OR REPLACE FUNCTION log_schema_change(
    p_table   VARCHAR,
    p_column  VARCHAR,
    p_old     VARCHAR,
    p_new     VARCHAR,
    p_by      VARCHAR,
    p_reason  TEXT,
    p_severity VARCHAR DEFAULT 'HIGH'
) RETURNS VOID AS $$
BEGIN
    INSERT INTO schema_change_log (table_name, column_name, old_type, new_type, changed_by, change_reason, severity)
    VALUES (p_table, p_column, p_old, p_new, p_by, p_reason, p_severity);
END;
$$ LANGUAGE plpgsql;

-- =============================================================
-- SCENARIO A: ETL Refactor Gone Wrong
-- Engineer changes `amount` to VARCHAR during ETL pipeline refactor.
-- Impact: ALL numeric ML features derived from amount BREAK.
--   - amount_ratio = amount / oldbalanceOrg → FAILS (can't divide text)
--   - is_large_transaction CASE → FAILS (can't compare text to number)
-- =============================================================
CREATE OR REPLACE FUNCTION apply_drift_scenario_a(
    p_changed_by VARCHAR DEFAULT 'unknown_engineer',
    p_reason TEXT DEFAULT 'ETL pipeline refactor — column type changed unintentionally'
) RETURNS TEXT AS $$
BEGIN
    -- Alter the column type
    ALTER TABLE paysim_raw_transactions
        ALTER COLUMN amount TYPE VARCHAR(50) USING amount::TEXT;

    -- Log the change
    PERFORM log_schema_change(
        'paysim_raw_transactions', 'amount',
        'double precision', 'character varying',
        p_changed_by, p_reason, 'CRITICAL'
    );

    RETURN 'Scenario A applied: amount changed to VARCHAR. Run investigation to detect impact.';
END;
$$ LANGUAGE plpgsql;

-- =============================================================
-- SCENARIO B: Boolean Label Corruption
-- Data pipeline outputs isFraud as string "true"/"false"/"0"/"1"
-- instead of integer 0 or 1. Happens during CSV→DB migration.
-- Impact: ML model cannot use isFraud as a numeric target label.
-- =============================================================
CREATE OR REPLACE FUNCTION apply_drift_scenario_b(
    p_changed_by VARCHAR DEFAULT 'data_pipeline_v3',
    p_reason TEXT DEFAULT 'CSV ingestion pipeline updated — writes fraud label as string instead of integer'
) RETURNS TEXT AS $$
BEGIN
    ALTER TABLE paysim_raw_transactions
        ALTER COLUMN isFraud TYPE VARCHAR(10) USING isFraud::TEXT;

    PERFORM log_schema_change(
        'paysim_raw_transactions', 'isfraud',
        'integer', 'character varying',
        p_changed_by, p_reason, 'CRITICAL'
    );

    RETURN 'Scenario B applied: isFraud changed to VARCHAR. ML model target label corrupted.';
END;
$$ LANGUAGE plpgsql;

-- =============================================================
-- SCENARIO C: Balance Column Type Downgrade
-- DBA runs a storage optimization migration that converts DOUBLE
-- PRECISION balance columns to TEXT to "save space."
-- Impact: balance_change_orig and balance_change_dest features BREAK.
-- =============================================================
CREATE OR REPLACE FUNCTION apply_drift_scenario_c(
    p_changed_by VARCHAR DEFAULT 'dba_team',
    p_reason TEXT DEFAULT 'Storage optimization — converting numeric columns to text to reduce storage'
) RETURNS TEXT AS $$
BEGIN
    ALTER TABLE paysim_raw_transactions
        ALTER COLUMN oldbalanceOrg TYPE VARCHAR(50) USING oldbalanceOrg::TEXT;

    ALTER TABLE paysim_raw_transactions
        ALTER COLUMN newbalanceOrig TYPE VARCHAR(50) USING newbalanceOrig::TEXT;

    PERFORM log_schema_change(
        'paysim_raw_transactions', 'oldbalanceorg',
        'double precision', 'character varying',
        p_changed_by, p_reason, 'HIGH'
    );

    PERFORM log_schema_change(
        'paysim_raw_transactions', 'newbalanceorig',
        'double precision', 'character varying',
        p_changed_by, p_reason, 'HIGH'
    );

    RETURN 'Scenario C applied: oldbalanceOrg + newbalanceOrig changed to VARCHAR. Balance features broken.';
END;
$$ LANGUAGE plpgsql;

-- =============================================================
-- RESET: Restore all columns to baseline types
-- =============================================================
CREATE OR REPLACE FUNCTION reset_all_drifts() RETURNS TEXT AS $$
DECLARE
    v_count INTEGER := 0;
BEGIN
    -- Restore amount
    BEGIN
        ALTER TABLE paysim_raw_transactions
            ALTER COLUMN amount TYPE DOUBLE PRECISION USING amount::DOUBLE PRECISION;
        UPDATE schema_change_log
            SET is_reverted = TRUE, reverted_at = NOW(), notes = 'Reverted by reset_all_drifts()'
            WHERE table_name = 'paysim_raw_transactions' AND column_name = 'amount' AND is_reverted = FALSE;
        v_count := v_count + 1;
    EXCEPTION WHEN OTHERS THEN
        NULL; -- Column already correct type, skip
    END;

    -- Restore isFraud
    BEGIN
        ALTER TABLE paysim_raw_transactions
            ALTER COLUMN isFraud TYPE INTEGER USING isFraud::INTEGER;
        UPDATE schema_change_log
            SET is_reverted = TRUE, reverted_at = NOW(), notes = 'Reverted by reset_all_drifts()'
            WHERE table_name = 'paysim_raw_transactions' AND column_name = 'isfraud' AND is_reverted = FALSE;
        v_count := v_count + 1;
    EXCEPTION WHEN OTHERS THEN
        NULL;
    END;

    -- Restore isFlaggedFraud
    BEGIN
        ALTER TABLE paysim_raw_transactions
            ALTER COLUMN isFlaggedFraud TYPE INTEGER USING isFlaggedFraud::INTEGER;
        UPDATE schema_change_log
            SET is_reverted = TRUE, reverted_at = NOW(), notes = 'Reverted by reset_all_drifts()'
            WHERE table_name = 'paysim_raw_transactions' AND column_name = 'isflaggedfraud' AND is_reverted = FALSE;
        v_count := v_count + 1;
    EXCEPTION WHEN OTHERS THEN
        NULL;
    END;

    -- Restore oldbalanceOrg
    BEGIN
        ALTER TABLE paysim_raw_transactions
            ALTER COLUMN oldbalanceOrg TYPE DOUBLE PRECISION USING oldbalanceOrg::DOUBLE PRECISION;
        UPDATE schema_change_log
            SET is_reverted = TRUE, reverted_at = NOW(), notes = 'Reverted by reset_all_drifts()'
            WHERE table_name = 'paysim_raw_transactions' AND column_name = 'oldbalanceorg' AND is_reverted = FALSE;
        v_count := v_count + 1;
    EXCEPTION WHEN OTHERS THEN
        NULL;
    END;

    -- Restore newbalanceOrig
    BEGIN
        ALTER TABLE paysim_raw_transactions
            ALTER COLUMN newbalanceOrig TYPE DOUBLE PRECISION USING newbalanceOrig::DOUBLE PRECISION;
        UPDATE schema_change_log
            SET is_reverted = TRUE, reverted_at = NOW(), notes = 'Reverted by reset_all_drifts()'
            WHERE table_name = 'paysim_raw_transactions' AND column_name = 'newbalanceorig' AND is_reverted = FALSE;
        v_count := v_count + 1;
    EXCEPTION WHEN OTHERS THEN
        NULL;
    END;

    -- Restore oldbalanceDest
    BEGIN
        ALTER TABLE paysim_raw_transactions
            ALTER COLUMN oldbalanceDest TYPE DOUBLE PRECISION USING oldbalanceDest::DOUBLE PRECISION;
        UPDATE schema_change_log
            SET is_reverted = TRUE, reverted_at = NOW(), notes = 'Reverted by reset_all_drifts()'
            WHERE table_name = 'paysim_raw_transactions' AND column_name = 'oldbalancedest' AND is_reverted = FALSE;
        v_count := v_count + 1;
    EXCEPTION WHEN OTHERS THEN
        NULL;
    END;

    -- Restore newbalanceDest
    BEGIN
        ALTER TABLE paysim_raw_transactions
            ALTER COLUMN newbalanceDest TYPE DOUBLE PRECISION USING newbalanceDest::DOUBLE PRECISION;
        UPDATE schema_change_log
            SET is_reverted = TRUE, reverted_at = NOW(), notes = 'Reverted by reset_all_drifts()'
            WHERE table_name = 'paysim_raw_transactions' AND column_name = 'newbalancedest' AND is_reverted = FALSE;
        v_count := v_count + 1;
    EXCEPTION WHEN OTHERS THEN
        NULL;
    END;

    RETURN format('Reset complete. %s column(s) checked/restored.', v_count);
END;
$$ LANGUAGE plpgsql;
