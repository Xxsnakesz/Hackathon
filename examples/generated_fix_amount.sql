-- =============================================================================
-- AUTO-GENERATED FIX SCRIPT by AI Data Reliability Agent
-- Generated at: 2026-07-11 15:07:29
-- =============================================================================
--
-- PROBLEM DETECTED:
--   Table: public.paysim_raw_transactions
--   Column: amount
--   Current (Wrong) Type: STRING
--   Expected (Correct) Type: DOUBLE -> DOUBLE PRECISION
--
-- ROOT CAUSE:
--   An upstream engineer changed the column type from DOUBLE to STRING,
--   causing the downstream Fraud Detection ML Model to produce incorrect predictions.
--   The model expects numeric input for the 'amount' feature, but received
--   string values, leading to massive false positives.
--
-- IMPACT:
--   - Fraud_Detection_ML_Model accuracy dropped from ~97.8% to ~42.3%
--   - False positive rate spiked from 2.1% to 78.5%
--   - Legitimate transactions being incorrectly blocked
--
-- SAFETY: This script runs inside a transaction. If any step fails, 
--         all changes are rolled back automatically.
-- =============================================================================

BEGIN;

-- Step 1: Verify the current column type before making changes
DO $$
DECLARE
    current_type TEXT;
BEGIN
    SELECT data_type INTO current_type
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'paysim_raw_transactions'
      AND column_name = 'amount';
    
    IF current_type IS NULL THEN
        RAISE EXCEPTION 'Column amount not found in public.paysim_raw_transactions';
    END IF;
    
    RAISE NOTICE 'Current type of amount: %', current_type;
END $$;

-- Step 2: Fix the column type back to the correct numeric type
-- Using CAST to safely convert STRING values back to numeric
ALTER TABLE public.paysim_raw_transactions
    ALTER COLUMN amount 
    TYPE DOUBLE PRECISION 
    USING amount::double precision;

-- Step 3: Add a comment documenting the fix for future reference
COMMENT ON COLUMN public.paysim_raw_transactions.amount IS 
    'FIXED by AI Data Reliability Agent on 2026-07-11 15:07:29. '
    'Type restored from STRING to DOUBLE PRECISION. '
    'Original incident: schema drift caused Fraud ML Model degradation.';

-- Step 4: Verify the fix was applied correctly
DO $$
DECLARE
    new_type TEXT;
BEGIN
    SELECT data_type INTO new_type
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'paysim_raw_transactions'
      AND column_name = 'amount';
    
    RAISE NOTICE 'Column amount type after fix: %', new_type;
    
    -- Verify by running a simple aggregate on the fixed column
    EXECUTE format('SELECT COUNT(*), AVG(%I) FROM public.paysim_raw_transactions LIMIT 1', 'amount');
    RAISE NOTICE 'Aggregate check passed — column is now numeric and queryable.';
END $$;

COMMIT;

-- =============================================================================
-- POST-FIX ACTIONS (Run manually after verifying the fix):
-- 1. Re-run DataHub ingestion to update metadata:
--    datahub ingest -c metadata/ingest_postgres.yaml
-- 2. Trigger model revalidation on the updated feature table
-- 3. Remove the DATA-INCIDENT tag from Fraud_Detection_ML_Model in DataHub
-- =============================================================================
