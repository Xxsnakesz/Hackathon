-- =============================================================
-- PaySim Financial Transaction Database Initialization
-- Database: paysim_fintech
-- =============================================================

-- =============================================================
-- 1. Raw Transactions Table (matches PaySim CSV schema exactly)
-- =============================================================
CREATE TABLE IF NOT EXISTS paysim_raw_transactions (
    step              INTEGER           NOT NULL,
    type              VARCHAR(20)       NOT NULL,
    amount            DOUBLE PRECISION  NOT NULL,
    nameOrig          VARCHAR(30)       NOT NULL,
    oldbalanceOrg     DOUBLE PRECISION  NOT NULL,
    newbalanceOrig    DOUBLE PRECISION  NOT NULL,
    nameDest          VARCHAR(30)       NOT NULL,
    oldbalanceDest    DOUBLE PRECISION  NOT NULL,
    newbalanceDest    DOUBLE PRECISION  NOT NULL,
    isFraud           INTEGER           NOT NULL DEFAULT 0,
    isFlaggedFraud    INTEGER           NOT NULL DEFAULT 0
);

-- Table comment
COMMENT ON TABLE paysim_raw_transactions IS
    'Raw transaction data from PaySim mobile money simulation. Each row represents a single financial transaction.';

-- Column comments
COMMENT ON COLUMN paysim_raw_transactions.step IS
    'Unit of time in the simulation. 1 step = 1 hour. Total steps: 744 (30 days).';
COMMENT ON COLUMN paysim_raw_transactions.type IS
    'Type of transaction: CASH_IN, CASH_OUT, DEBIT, PAYMENT, or TRANSFER.';
COMMENT ON COLUMN paysim_raw_transactions.amount IS
    'Amount of the transaction in local currency.';
COMMENT ON COLUMN paysim_raw_transactions.nameOrig IS
    'Customer ID who initiated the transaction (origin).';
COMMENT ON COLUMN paysim_raw_transactions.oldbalanceOrg IS
    'Initial balance of the origin account before the transaction.';
COMMENT ON COLUMN paysim_raw_transactions.newbalanceOrig IS
    'New balance of the origin account after the transaction.';
COMMENT ON COLUMN paysim_raw_transactions.nameDest IS
    'Customer ID who is the recipient of the transaction (destination).';
COMMENT ON COLUMN paysim_raw_transactions.oldbalanceDest IS
    'Initial balance of the destination account before the transaction.';
COMMENT ON COLUMN paysim_raw_transactions.newbalanceDest IS
    'New balance of the destination account after the transaction.';
COMMENT ON COLUMN paysim_raw_transactions.isFraud IS
    'Fraud label. 1 if the transaction is fraudulent, 0 otherwise. Fraud is injected for TRANSFER and CASH_OUT types.';
COMMENT ON COLUMN paysim_raw_transactions.isFlaggedFraud IS
    'Flag raised by the business model when a transfer exceeds 200,000 in a single transaction. 1 if flagged, 0 otherwise.';

-- =============================================================
-- 2. Feature Engineering View (derived computed features)
-- =============================================================
CREATE OR REPLACE VIEW feature_engineering_table AS
SELECT
    -- Original columns
    step,
    type,
    amount,
    nameOrig,
    oldbalanceOrg,
    newbalanceOrig,
    nameDest,
    oldbalanceDest,
    newbalanceDest,
    isFraud,
    isFlaggedFraud,

    -- Computed features
    amount / NULLIF(oldbalanceOrg, 0)                           AS amount_ratio,
    oldbalanceOrg - newbalanceOrig                               AS balance_change_orig,
    newbalanceDest - oldbalanceDest                              AS balance_change_dest,
    CASE WHEN amount > 200000 THEN 1 ELSE 0 END                 AS is_large_transaction,
    CASE
        WHEN ABS((oldbalanceOrg - amount) - newbalanceOrig) > 1
        THEN 1
        ELSE 0
    END                                                          AS is_balance_mismatch
FROM
    paysim_raw_transactions;

-- View comment
COMMENT ON VIEW feature_engineering_table IS
    'Derived view with engineered features for fraud detection ML models. Includes ratio, balance delta, large-txn flag, and mismatch detection.';
