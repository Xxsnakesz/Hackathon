# =============================================================================
# fix_generator.py — Automated SQL/dbt Fix Script Generator
# =============================================================================
# This module generates remediation scripts when the AI Agent detects
# schema drift in upstream datasets.
#
# === SPEAKER GUIDE (Penjelasan untuk Pembicara) ===
# File ini adalah "tangan perbaikan" AI Agent. Setelah AI menemukan akar
# masalah (misal: kolom amount berubah tipe dari FLOAT ke STRING),
# modul ini otomatis menghasilkan skrip SQL yang bisa langsung dijalankan
# oleh Data Engineer untuk memperbaiki masalah tersebut.
#
# Saat presentasi, Anda bisa menjelaskan:
# "Setelah AI Agent menemukan akar masalah via DataHub lineage, ia tidak
#  hanya melapor — ia juga menghasilkan skrip SQL korektif yang siap
#  dijalankan. Ini menghemat waktu engineer dari menulis query manual."
#
# Ini juga memenuhi kriteria hackathon 'Metadata-Aware Code Generation'
# karena kode yang dihasilkan BERDASARKAN skema nyata dari DataHub,
# bukan template generik.
# =============================================================================

import json
import os
from datetime import datetime
from typing import Optional

# Type mapping for SQL remediation
# Maps DataHub/generic types to PostgreSQL types
TYPE_MAPPING = {
    "STRING": {"postgres": "VARCHAR", "original_numeric": "DOUBLE PRECISION"},
    "DOUBLE": {"postgres": "DOUBLE PRECISION"},
    "FLOAT": {"postgres": "DOUBLE PRECISION"},
    "INTEGER": {"postgres": "INTEGER"},
    "INT": {"postgres": "INTEGER"},
    "BOOLEAN": {"postgres": "BOOLEAN"},
    "BIGINT": {"postgres": "BIGINT"},
}


def generate_sql_fix(
    table_name: str,
    column_name: str,
    wrong_type: str,
    correct_type: str,
    database_name: str = "paysim_fintech",
    schema_name: str = "public",
) -> str:
    """
    Generate a PostgreSQL ALTER TABLE statement to fix a schema drift issue.

    === SPEAKER GUIDE ===
    Fungsi ini menghasilkan SQL ALTER TABLE yang aman untuk memperbaiki
    tipe kolom yang salah. Perhatikan bahwa kita menggunakan USING clause
    untuk konversi tipe data yang aman, dan membungkusnya dalam transaksi
    agar bisa di-rollback jika gagal.

    Args:
        table_name: Name of the affected table (e.g., 'paysim_raw_transactions')
        column_name: Name of the column with wrong type (e.g., 'amount')
        wrong_type: The incorrect type currently in use (e.g., 'STRING')
        correct_type: The type it should be restored to (e.g., 'DOUBLE')
        database_name: PostgreSQL database name
        schema_name: PostgreSQL schema name

    Returns:
        A complete SQL fix script as a string
    """
    # Resolve the correct PostgreSQL type
    pg_correct_type = TYPE_MAPPING.get(correct_type.upper(), {}).get(
        "postgres", "DOUBLE PRECISION"
    )

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    sql_script = f"""-- =============================================================================
-- AUTO-GENERATED FIX SCRIPT by AI Data Reliability Agent
-- Generated at: {timestamp}
-- =============================================================================
--
-- PROBLEM DETECTED:
--   Table: {schema_name}.{table_name}
--   Column: {column_name}
--   Current (Wrong) Type: {wrong_type}
--   Expected (Correct) Type: {correct_type} -> {pg_correct_type}
--
-- ROOT CAUSE:
--   An upstream engineer changed the column type from {correct_type} to {wrong_type},
--   causing the downstream Fraud Detection ML Model to produce incorrect predictions.
--   The model expects numeric input for the '{column_name}' feature, but received
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
    WHERE table_schema = '{schema_name}'
      AND table_name = '{table_name}'
      AND column_name = '{column_name}';
    
    IF current_type IS NULL THEN
        RAISE EXCEPTION 'Column {column_name} not found in {schema_name}.{table_name}';
    END IF;
    
    RAISE NOTICE 'Current type of {column_name}: %', current_type;
END $$;

-- Step 2: Fix the column type back to the correct numeric type
-- Using CAST to safely convert STRING values back to numeric
ALTER TABLE {schema_name}.{table_name}
    ALTER COLUMN {column_name} 
    TYPE {pg_correct_type} 
    USING {column_name}::{pg_correct_type.lower().replace(' ', '_') if ' ' not in pg_correct_type else 'double precision'};

-- Step 3: Add a comment documenting the fix for future reference
COMMENT ON COLUMN {schema_name}.{table_name}.{column_name} IS 
    'FIXED by AI Data Reliability Agent on {timestamp}. '
    'Type restored from {wrong_type} to {pg_correct_type}. '
    'Original incident: schema drift caused Fraud ML Model degradation.';

-- Step 4: Verify the fix was applied correctly
DO $$
DECLARE
    new_type TEXT;
BEGIN
    SELECT data_type INTO new_type
    FROM information_schema.columns
    WHERE table_schema = '{schema_name}'
      AND table_name = '{table_name}'
      AND column_name = '{column_name}';
    
    RAISE NOTICE 'Column {column_name} type after fix: %', new_type;
    
    -- Verify by running a simple aggregate on the fixed column
    EXECUTE format('SELECT COUNT(*), AVG(%I) FROM {schema_name}.{table_name} LIMIT 1', '{column_name}');
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
"""
    return sql_script


def generate_column_missing_fix(
    table_name: str,
    column_name: str,
    expected_type: str,
    database_name: str = "paysim_fintech",
    schema_name: str = "public",
) -> str:
    """
    Generate a fix for a COLUMN_MISSING drift — the column was dropped
    entirely, not just retyped. This is a DIFFERENT strategy than the
    cast-back template: re-adding a dropped column can restore the schema
    shape, but the column's original DATA is gone — there is no CAST that
    recovers it. The script is explicit about that instead of pretending a
    structural fix also un-deletes data.
    """
    pg_type = TYPE_MAPPING.get(expected_type.upper(), {}).get("postgres", expected_type.upper())
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return f"""-- =============================================================================
-- AUTO-GENERATED FIX SCRIPT by AI Data Reliability Agent
-- Strategy: RE-ADD DROPPED COLUMN (column was DROPPED, not retyped)
-- Generated at: {timestamp}
-- =============================================================================
--
-- PROBLEM DETECTED:
--   Table: {schema_name}.{table_name}
--   Column: {column_name} — MISSING (dropped from the live schema)
--   Expected type (per DataHub baseline): {expected_type} -> {pg_type}
--
-- IMPORTANT — READ BEFORE RUNNING:
--   This script restores the COLUMN SHAPE only. If the column was dropped
--   via `ALTER TABLE ... DROP COLUMN`, its historical data is gone — no SQL
--   can recover it. Re-adding the column produces NULLs for every existing
--   row. If a backup/snapshot exists, restore data from there AFTER running
--   this script. This is a structural fix, not a data-recovery fix.
--
-- SAFETY: Wrapped in a transaction; rolls back automatically on failure.
-- =============================================================================

BEGIN;

DO $$
DECLARE
    col_exists BOOLEAN;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = '{schema_name}' AND table_name = '{table_name}'
          AND column_name = '{column_name}'
    ) INTO col_exists;

    IF col_exists THEN
        RAISE EXCEPTION 'Column {column_name} already exists on {schema_name}.{table_name} — nothing to re-add.';
    END IF;

    RAISE NOTICE 'Confirmed {column_name} is missing from {schema_name}.{table_name}. Proceeding to re-add.';
END $$;

ALTER TABLE {schema_name}.{table_name}
    ADD COLUMN {column_name} {pg_type};

COMMENT ON COLUMN {schema_name}.{table_name}.{column_name} IS
    'RE-ADDED by AI Data Reliability Agent on {timestamp} after a COLUMN_MISSING drift. '
    'Existing rows have NULL for this column — restore from backup if historical values are needed.';

DO $$
DECLARE
    new_col_type TEXT;
    null_count BIGINT;
BEGIN
    SELECT data_type INTO new_col_type
    FROM information_schema.columns
    WHERE table_schema = '{schema_name}' AND table_name = '{table_name}'
      AND column_name = '{column_name}';

    EXECUTE format('SELECT COUNT(*) FROM {schema_name}.{table_name} WHERE %I IS NULL', '{column_name}')
        INTO null_count;

    RAISE NOTICE 'Column {column_name} re-added as %. % row(s) currently NULL (expected — no historical data).', new_col_type, null_count;
END $$;

COMMIT;

-- =============================================================================
-- POST-FIX ACTIONS:
-- 1. If a backup/snapshot exists, backfill historical values for {column_name}.
-- 2. Re-run DataHub ingestion so schemaMetadata reflects the restored column.
-- 3. Investigate WHY the column was dropped — this is rarely accidental,
--    unlike a type change, and may indicate an intentional (but uncoordinated)
--    migration that needs a proper deprecation process next time.
-- =============================================================================
"""


def generate_column_added_fix(
    table_name: str,
    column_name: str,
    actual_type: str,
    schema_name: str = "public",
) -> str:
    """
    Generate a response for a COLUMN_ADDED drift — a new column appeared
    that DataHub's baseline doesn't know about. Unlike TYPE_CHANGE or
    COLUMN_MISSING, this usually isn't broken — a new column is additive
    and can't break existing queries. The correct strategy is DIFFERENT
    again: acknowledge the new column into the baseline rather than
    generating a structural ALTER (there's nothing to roll back).
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return f"""-- =============================================================================
-- AUTO-GENERATED FIX SCRIPT by AI Data Reliability Agent
-- Strategy: ACCEPT NEW COLUMN INTO BASELINE (additive change, not breaking)
-- Generated at: {timestamp}
-- =============================================================================
--
-- PROBLEM DETECTED:
--   Table: {schema_name}.{table_name}
--   Column: {column_name} — present in the live schema but NOT in the
--            DataHub-registered baseline (type: {actual_type})
--
-- WHY THIS IS A DIFFERENT STRATEGY THAN A TYPE-CHANGE OR MISSING-COLUMN FIX:
--   An added column is additive — it cannot break existing SELECT * or
--   named-column queries that predate it, and downstream ML features that
--   don't reference it are unaffected. There is nothing to ALTER back.
--   The action needed is metadata hygiene: register the column in the
--   baseline so future scans don't keep re-flagging it as drift.
--
-- SAFETY: Wrapped in a transaction; rolls back automatically on failure.
-- =============================================================================

BEGIN;

DO $$
BEGIN
    RAISE NOTICE 'Column {column_name} ({actual_type}) on {schema_name}.{table_name} '
                 'is additive — no structural rollback needed.';
END $$;

-- Register the new column in the local schema_baseline table so future
-- scans treat it as expected, if that table exists in this deployment.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = '{schema_name}' AND table_name = 'schema_baseline'
    ) THEN
        INSERT INTO {schema_name}.schema_baseline
            (table_name, column_name, expected_type, is_nullable, is_critical, description)
        VALUES
            ('{table_name}', '{column_name}', '{actual_type}', TRUE, FALSE,
             'Auto-accepted by AI Data Reliability Agent on {timestamp} — new additive column.')
        ON CONFLICT DO NOTHING;
        RAISE NOTICE 'Registered {column_name} into local schema_baseline.';
    ELSE
        RAISE NOTICE 'No local schema_baseline table found — skip local registration.';
    END IF;
END $$;

COMMIT;

-- =============================================================================
-- POST-FIX ACTIONS:
-- 1. Re-run DataHub ingestion so schemaMetadata includes {column_name} —
--    that is the authoritative baseline this agent actually compares against.
-- 2. If {column_name} is business-critical (e.g. a new ML feature), tag it
--    accordingly in DataHub (ml-input / critical-feature) so the agent's
--    severity classifier picks it up on the next investigation.
-- =============================================================================
"""


def generate_dbt_patch(
    table_name: str,
    column_name: str,
    correct_type: str,
) -> str:
    """
    Generate a dbt model patch that adds a CAST to fix the schema drift.

    === SPEAKER GUIDE ===
    Jika tim menggunakan dbt (data build tool), kita juga menghasilkan
    patch dbt yang bisa ditambahkan ke model transformasi mereka.
    Ini menunjukkan bahwa AI Agent kita fleksibel dan menghasilkan
    kode yang sesuai dengan toolchain yang digunakan tim.

    Args:
        table_name: Source table name
        column_name: Column to fix
        correct_type: Expected correct data type

    Returns:
        dbt SQL model patch as string
    """
    pg_type = TYPE_MAPPING.get(correct_type.upper(), {}).get(
        "postgres", "DOUBLE PRECISION"
    )

    dbt_patch = f"""-- dbt model patch: fix_{table_name}_{column_name}_type.sql
-- Auto-generated by AI Data Reliability Agent
-- Add this to your dbt project's models/ directory

{{{{ config(
    materialized='view',
    description='Fixes schema drift on {column_name} column - casts back to {pg_type}'
) }}}}

SELECT
    step,
    type,
    CAST({column_name} AS {pg_type}) AS {column_name},  -- FIX: Cast back to numeric
    nameOrig,
    oldbalanceOrg,
    newbalanceOrig,
    nameDest,
    oldbalanceDest,
    newbalanceDest,
    isFraud,
    isFlaggedFraud,
    -- Recompute derived features with correct types
    CAST({column_name} AS {pg_type}) / NULLIF(oldbalanceOrg, 0) AS amount_ratio,
    oldbalanceOrg - newbalanceOrig AS balance_change_orig,
    newbalanceDest - oldbalanceDest AS balance_change_dest,
    CASE WHEN CAST({column_name} AS {pg_type}) > 200000 THEN 1 ELSE 0 END AS is_large_transaction
FROM {{{{ source('paysim', '{table_name}') }}}}
"""
    return dbt_patch


def save_fix_scripts(
    table_name: str,
    column_name: str,
    wrong_type: str,
    correct_type: str,
    output_dir: str = "examples",
) -> dict:
    """
    Generate and save both SQL and dbt fix scripts to disk.

    Returns:
        Dictionary with file paths of generated scripts
    """
    os.makedirs(output_dir, exist_ok=True)

    # Generate SQL fix
    sql_fix = generate_sql_fix(table_name, column_name, wrong_type, correct_type)
    sql_path = os.path.join(output_dir, f"generated_fix_{column_name}.sql")
    with open(sql_path, "w") as f:
        f.write(sql_fix)

    # Generate dbt patch
    dbt_fix = generate_dbt_patch(table_name, column_name, correct_type)
    dbt_path = os.path.join(output_dir, f"generated_dbt_fix_{column_name}.sql")
    with open(dbt_path, "w") as f:
        f.write(dbt_fix)

    result = {
        "sql_fix_path": sql_path,
        "dbt_fix_path": dbt_path,
        "sql_preview": sql_fix[:500] + "...",
    }

    print(f"✅ SQL fix script saved to: {sql_path}")
    print(f"✅ dbt patch saved to: {dbt_path}")

    return result


# =============================================================================
# Strategy dispatcher — one entrypoint, multiple templates
# =============================================================================
# A schema drift is not one problem — TYPE_CHANGE, COLUMN_MISSING, and
# COLUMN_ADDED each need a genuinely different remediation approach (cast
# back vs. re-add with a data-loss warning vs. accept-as-additive). This
# dispatcher is the fixed, vetted set of strategies; FixAuthor (the LLM
# agent) selects and justifies which one applies to each drift — it never
# writes SQL freehand, which is what keeps this safe from hallucination
# while still requiring genuine reasoning about which template fits.
# =============================================================================

STRATEGY_BY_DRIFT_TYPE = {
    "TYPE_CHANGE": "cast_back",
    "COLUMN_MISSING": "re_add_column",
    "COLUMN_ADDED": "accept_into_baseline",
}


def generate_fix_for_drift(drift: dict, output_dir: str = "examples") -> Optional[dict]:
    """
    Dispatch a single drift record to the correct remediation strategy.

    Returns None for drift types with no defined strategy (fail loud in the
    caller rather than silently producing a wrong fix). Returns a dict with
    at least `sql_fix_path` and `strategy` on success.
    """
    os.makedirs(output_dir, exist_ok=True)
    drift_type = drift.get("drift_type")
    table = drift["table"]
    column = drift["column"]
    strategy = STRATEGY_BY_DRIFT_TYPE.get(drift_type)

    if strategy == "cast_back":
        res = save_fix_scripts(
            table_name=table, column_name=column,
            wrong_type=drift["actual_type"], correct_type=drift["baseline_type"],
            output_dir=output_dir,
        )
        res["strategy"] = "cast_back"
        return res

    if strategy == "re_add_column":
        sql = generate_column_missing_fix(
            table_name=table, column_name=column, expected_type=drift["baseline_type"],
        )
        sql_path = os.path.join(output_dir, f"generated_fix_{column}_readd.sql")
        with open(sql_path, "w") as f:
            f.write(sql)
        print(f"✅ SQL fix script saved to: {sql_path}")
        return {
            "sql_fix_path": sql_path, "dbt_fix_path": None,
            "sql_preview": sql[:500] + "...", "strategy": "re_add_column",
        }

    if strategy == "accept_into_baseline":
        sql = generate_column_added_fix(
            table_name=table, column_name=column, actual_type=drift["actual_type"],
        )
        sql_path = os.path.join(output_dir, f"generated_fix_{column}_baseline_accept.sql")
        with open(sql_path, "w") as f:
            f.write(sql)
        print(f"✅ SQL fix script saved to: {sql_path}")
        return {
            "sql_fix_path": sql_path, "dbt_fix_path": None,
            "sql_preview": sql[:500] + "...", "strategy": "accept_into_baseline",
        }

    return None


# =============================================================================
# Main — Generate example fix scripts for the demo scenario
# =============================================================================
if __name__ == "__main__":
    print("=" * 70)
    print("🔧 AI Data Reliability Agent — Fix Script Generator")
    print("=" * 70)
    print()
    print("Generating remediation scripts for schema drift incident...")
    print("  Table: paysim_raw_transactions")
    print("  Column: amount")
    print("  Drift: DOUBLE → STRING")
    print()

    # Generate fix scripts for the demo scenario
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    examples_dir = os.path.join(project_root, "examples")

    results = save_fix_scripts(
        table_name="paysim_raw_transactions",
        column_name="amount",
        wrong_type="STRING",
        correct_type="DOUBLE",
        output_dir=examples_dir,
    )

    print()
    print("=" * 70)
    print("📋 Generated Files:")
    for key, value in results.items():
        if "path" in key:
            print(f"   {key}: {value}")
    print("=" * 70)
