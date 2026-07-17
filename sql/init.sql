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

-- Column comments
COMMENT ON TABLE paysim_raw_transactions IS
    'Raw transaction data from PaySim mobile money simulation. Each row = one financial transaction.';
COMMENT ON COLUMN paysim_raw_transactions.step IS
    'Unit of time in the simulation. 1 step = 1 hour. Total steps: 744 (30 days).';
COMMENT ON COLUMN paysim_raw_transactions.type IS
    'Type of transaction: CASH_IN, CASH_OUT, DEBIT, PAYMENT, or TRANSFER.';
COMMENT ON COLUMN paysim_raw_transactions.amount IS
    'Amount of the transaction in local currency. CRITICAL ML feature — must be DOUBLE PRECISION.';
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
    'Fraud label. 1 if the transaction is fraudulent, 0 otherwise.';
COMMENT ON COLUMN paysim_raw_transactions.isFlaggedFraud IS
    'Flag raised by rule engine when transfer exceeds 200,000. 1 = flagged, 0 = not flagged.';

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

    -- Computed features for ML model
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

COMMENT ON VIEW feature_engineering_table IS
    'Derived view with engineered features for fraud detection ML models. Computes ratio, balance delta, large-txn flag, and mismatch detection.';

-- =============================================================
-- 3. Schema Change Log Table (AUDIT TRAIL)
-- Every ALTER TABLE is recorded here with who/when/why.
-- The AI agent reads this table to build its investigation report.
-- =============================================================
CREATE TABLE IF NOT EXISTS schema_change_log (
    id            SERIAL         PRIMARY KEY,
    table_name    VARCHAR(100)   NOT NULL,
    column_name   VARCHAR(100)   NOT NULL,
    old_type      VARCHAR(100)   NOT NULL,
    new_type      VARCHAR(100)   NOT NULL,
    changed_by    VARCHAR(100)   NOT NULL DEFAULT 'unknown',
    change_reason TEXT           NOT NULL DEFAULT 'No reason provided',
    changed_at    TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    reverted_at   TIMESTAMPTZ    NULL,
    is_reverted   BOOLEAN        NOT NULL DEFAULT FALSE,
    severity      VARCHAR(20)    NOT NULL DEFAULT 'HIGH',
    notes         TEXT           NULL
);

COMMENT ON TABLE schema_change_log IS
    'Audit trail of all schema changes applied to monitored tables. Populated by the AI agent and the Simulate Drift UI. Used by the agent to build investigation reports.';

-- =============================================================
-- 4. Baseline Schema Registry
-- Stores the "healthy" expected schema for each monitored table.
-- The agent compares information_schema.columns against this.
-- =============================================================
CREATE TABLE IF NOT EXISTS schema_baseline (
    id            SERIAL         PRIMARY KEY,
    table_name    VARCHAR(100)   NOT NULL,
    column_name   VARCHAR(100)   NOT NULL,
    expected_type VARCHAR(100)   NOT NULL,
    is_nullable   BOOLEAN        NOT NULL DEFAULT FALSE,
    is_critical   BOOLEAN        NOT NULL DEFAULT FALSE,  -- TRUE = used by ML model
    description   TEXT           NULL,
    registered_at TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    UNIQUE(table_name, column_name)
);

COMMENT ON TABLE schema_baseline IS
    'Expected (healthy) schema for all monitored tables. Agent compares actual DB schema against this table to detect drift. Populated at init time.';

-- =============================================================
-- 5. Seed Baseline Schema for paysim_raw_transactions
-- =============================================================
INSERT INTO schema_baseline (table_name, column_name, expected_type, is_nullable, is_critical, description)
VALUES
    ('paysim_raw_transactions', 'step',           'integer',          false, false, 'Hour of simulation (1 step = 1 hour)'),
    ('paysim_raw_transactions', 'type',           'character varying', false, false, 'Transaction type: CASH_IN, CASH_OUT, DEBIT, PAYMENT, TRANSFER'),
    ('paysim_raw_transactions', 'amount',         'double precision',  false, true,  'Transaction amount in local currency — CRITICAL ML feature'),
    ('paysim_raw_transactions', 'nameorig',       'character varying', false, false, 'Customer ID initiating the transaction'),
    ('paysim_raw_transactions', 'oldbalanceorg',  'double precision',  false, true,  'Balance before transaction (sender) — used in balance_change_orig feature'),
    ('paysim_raw_transactions', 'newbalanceorig', 'double precision',  false, true,  'Balance after transaction (sender) — used in balance_change_orig feature'),
    ('paysim_raw_transactions', 'namedest',       'character varying', false, false, 'Recipient customer ID'),
    ('paysim_raw_transactions', 'oldbalancedest', 'double precision',  false, true,  'Balance before transaction (recipient) — used in balance_change_dest feature'),
    ('paysim_raw_transactions', 'newbalancedest', 'double precision',  false, true,  'Balance after transaction (recipient) — used in balance_change_dest feature'),
    ('paysim_raw_transactions', 'isfraud',        'integer',           false, true,  'Ground truth fraud label (0=legitimate, 1=fraud) — ML model target variable'),
    ('paysim_raw_transactions', 'isflaggedfraud', 'integer',           false, false, 'System flag for large transfer attempts (>200K)')
ON CONFLICT (table_name, column_name) DO NOTHING;

-- =============================================================
-- 6. Seed Baseline Schema for feature_engineering_table
-- =============================================================
INSERT INTO schema_baseline (table_name, column_name, expected_type, is_nullable, is_critical, description)
VALUES
    ('feature_engineering_table', 'step',                'integer',          true,  false, 'Hour of simulation'),
    ('feature_engineering_table', 'type',                'character varying', true,  false, 'Transaction type'),
    ('feature_engineering_table', 'amount',              'double precision',  true,  true,  'Transaction amount — CRITICAL'),
    ('feature_engineering_table', 'nameorig',            'character varying', true,  false, 'Sender ID'),
    ('feature_engineering_table', 'oldbalanceorg',       'double precision',  true,  true,  'Sender balance before'),
    ('feature_engineering_table', 'newbalanceorig',      'double precision',  true,  true,  'Sender balance after'),
    ('feature_engineering_table', 'namedest',            'character varying', true,  false, 'Recipient ID'),
    ('feature_engineering_table', 'oldbalancedest',      'double precision',  true,  true,  'Recipient balance before'),
    ('feature_engineering_table', 'newbalancedest',      'double precision',  true,  true,  'Recipient balance after'),
    ('feature_engineering_table', 'isfraud',             'integer',           true,  true,  'Fraud label — ML model target'),
    ('feature_engineering_table', 'isflaggedfraud',      'integer',           true,  false, 'System flag'),
    ('feature_engineering_table', 'amount_ratio',        'double precision',  true,  true,  'amount / oldbalanceOrg — derived feature'),
    ('feature_engineering_table', 'balance_change_orig', 'double precision',  true,  true,  'oldbalanceOrg - newbalanceOrig — derived feature'),
    ('feature_engineering_table', 'balance_change_dest', 'double precision',  true,  true,  'newbalanceDest - oldbalanceDest — derived feature'),
    ('feature_engineering_table', 'is_large_transaction','integer',           true,  true,  '1 if amount > 200000 — binary feature'),
    ('feature_engineering_table', 'is_balance_mismatch', 'integer',           true,  true,  '1 if balance math inconsistency detected — fraud signal')
ON CONFLICT (table_name, column_name) DO NOTHING;

-- =============================================================
-- 7. Log the initial baseline registration
-- =============================================================
INSERT INTO schema_change_log (table_name, column_name, old_type, new_type, changed_by, change_reason, severity, notes)
VALUES
    ('paysim_raw_transactions', '__schema_init__', 'N/A', 'BASELINE REGISTERED', 'system/init.sql',
     'Initial schema registration — all columns set to baseline types', 'INFO',
     'This is the reference point. Any future changes will be detected as drift.');
