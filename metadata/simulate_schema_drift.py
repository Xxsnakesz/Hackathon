#!/usr/bin/env python3
"""
simulate_schema_drift.py — DataHub Schema Drift Simulation
============================================================

This script simulates a real-world schema drift incident by re-emitting
the `paysim_raw_transactions` dataset to DataHub with the `amount` column
type changed from DOUBLE to STRING.

WHY SCHEMA DRIFT MATTERS:
    Schema drift occurs when the structure (schema) of a dataset changes
    unexpectedly — for example, when an upstream engineer modifies a column
    type, renames a column, or adds/removes columns without coordinating
    with downstream consumers.

    In a fraud detection pipeline, if the `amount` column changes from
    DOUBLE to STRING, the ML model's feature engineering will break because
    numeric operations (ratios, comparisons) cannot be performed on strings.
    This is exactly the kind of incident that DataHub's schema change
    detection and metadata observability features are designed to catch.

WHAT THIS SCRIPT DOES:
    1. Connects to DataHub GMS (same as lineage_bootstrap.py).
    2. Re-emits the SchemaMetadata for paysim_raw_transactions, but with
       the `amount` column's type changed from NumberTypeClass (DOUBLE)
       to StringTypeClass (STRING).
    3. DataHub will detect this as a schema change and update the metadata
       accordingly. If schema change notifications are configured, alerts
       will be triggered.

USAGE:
    python simulate_schema_drift.py
    python simulate_schema_drift.py --gms-url http://datahub-gms:8080
    DATAHUB_GMS_URL=http://my-gms:8080 python simulate_schema_drift.py

Requirements (pip install):
    datahub[rest]
    # or individually:
    # acryl-datahub
    # datahub-rest-emitter
"""

import argparse
import logging
import os
import sys

# ---------------------------------------------------------------------------
# DataHub SDK imports
# ---------------------------------------------------------------------------
# DatahubRestEmitter sends metadata events to the DataHub GMS REST endpoint.
from datahub.emitter.rest_emitter import DatahubRestEmitter

# MetadataChangeProposalWrapper is the modern wrapper for emitting MCPs.
from datahub.emitter.mcp import MetadataChangeProposalWrapper

# Schema-related classes for describing dataset columns and types.
from datahub.metadata.schema_classes import (
    SchemaMetadataClass,          # Top-level schema container
    SchemaFieldClass,             # A single column/field in the schema
    SchemaFieldDataTypeClass,     # Wrapper around the type union
    NumberTypeClass,              # Numeric types (INT, DOUBLE, FLOAT, etc.)
    StringTypeClass,              # String/text types
    OtherSchemaClass,             # Generic platform schema marker
)

# URN builder for constructing dataset URNs.
from datahub.metadata.urns import DatasetUrn

# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ===========================================================================
# Constants — must match lineage_bootstrap.py for consistency
# ===========================================================================
PLATFORM = "postgres"
ENV = "PROD"
RAW_DATASET_NAME = "paysim_raw_transactions"


# ===========================================================================
# Helper: Build a SchemaFieldClass for a single column
# ===========================================================================
def _make_field(field_path: str, field_type, description: str = "") -> SchemaFieldClass:
    """
    Create a SchemaFieldClass for a single column.

    This is the same helper used in lineage_bootstrap.py, duplicated here
    for script independence.

    Args:
        field_path:  Column name (e.g. 'amount').
        field_type:  DataHub type class instance (NumberTypeClass or StringTypeClass).
        description: Human-readable description of the column.

    Returns:
        A populated SchemaFieldClass instance.
    """
    return SchemaFieldClass(
        fieldPath=field_path,
        nativeDataType=field_path,
        type=SchemaFieldDataTypeClass(type=field_type),
        description=description,
    )


# ===========================================================================
# Build the DRIFTED schema — amount column changed from DOUBLE to STRING
# ===========================================================================
def _drifted_transaction_fields() -> list:
    """
    Return the 11 columns of paysim_raw_transactions, but with the
    `amount` column type INTENTIONALLY changed from DOUBLE (NumberTypeClass)
    to STRING (StringTypeClass).

    This simulates a schema drift incident where an upstream engineer
    (or an automated migration) accidentally changes the column type.

    ORIGINAL schema (from lineage_bootstrap.py):
        amount  →  NumberTypeClass()  (represents DOUBLE)

    DRIFTED schema (this function):
        amount  →  StringTypeClass()  (represents STRING)  ← DRIFT!

    All other 10 columns remain unchanged.
    """
    return [
        # Column 1: step — unchanged (INT → NumberTypeClass)
        _make_field("step",             NumberTypeClass(), "Time step of the simulation (1 step = 1 hour of real time)"),

        # Column 2: type — unchanged (STRING → StringTypeClass)
        _make_field("type",             StringTypeClass(), "Transaction type: PAYMENT, TRANSFER, CASH_OUT, DEBIT, CASH_IN"),

        # ┌─────────────────────────────────────────────────────────────────┐
        # │  ⚠️  SCHEMA DRIFT HERE!                                        │
        # │  Column 3: amount — CHANGED from NumberTypeClass to            │
        # │  StringTypeClass. This simulates the column type changing from │
        # │  DOUBLE to STRING in the upstream database.                    │
        # └─────────────────────────────────────────────────────────────────┘
        _make_field(
            "amount",
            StringTypeClass(),  # ← WAS NumberTypeClass() — THIS IS THE DRIFT!
            "Transaction amount — ⚠️ TYPE CHANGED FROM DOUBLE TO STRING (schema drift!)",
        ),

        # Column 4: nameOrig — unchanged (STRING → StringTypeClass)
        _make_field("nameOrig",         StringTypeClass(), "Originator customer account identifier"),

        # Column 5: oldbalanceOrg — unchanged (DOUBLE → NumberTypeClass)
        _make_field("oldbalanceOrg",    NumberTypeClass(), "Originator account balance before the transaction"),

        # Column 6: newbalanceOrig — unchanged (DOUBLE → NumberTypeClass)
        _make_field("newbalanceOrig",   NumberTypeClass(), "Originator account balance after the transaction"),

        # Column 7: nameDest — unchanged (STRING → StringTypeClass)
        _make_field("nameDest",         StringTypeClass(), "Destination customer account identifier"),

        # Column 8: oldbalanceDest — unchanged (DOUBLE → NumberTypeClass)
        _make_field("oldbalanceDest",   NumberTypeClass(), "Destination account balance before the transaction"),

        # Column 9: newbalanceDest — unchanged (DOUBLE → NumberTypeClass)
        _make_field("newbalanceDest",   NumberTypeClass(), "Destination account balance after the transaction"),

        # Column 10: isFraud — unchanged (INT → NumberTypeClass)
        _make_field("isFraud",          NumberTypeClass(), "Ground truth fraud label: 1 = fraudulent, 0 = legitimate"),

        # Column 11: isFlaggedFraud — unchanged (INT → NumberTypeClass)
        _make_field("isFlaggedFraud",   NumberTypeClass(), "Flag set by a simple rule-based engine (large transfers > 200k)"),
    ]


# ===========================================================================
# Emit the drifted schema to DataHub
# ===========================================================================
def simulate_schema_drift(emitter: DatahubRestEmitter) -> None:
    """
    Re-emit the paysim_raw_transactions dataset with a drifted schema.

    This function:
      1. Constructs the same dataset URN as lineage_bootstrap.py
      2. Builds a new SchemaMetadataClass with the `amount` column type
         changed from DOUBLE to STRING
      3. Emits it to DataHub, which overwrites the previous schema metadata

    After this runs, DataHub will show the `amount` column as STRING instead
    of DOUBLE. Any downstream consumers (like the feature engineering pipeline
    or the ML model) that depend on `amount` being numeric will be impacted.
    """
    # Build the dataset URN — must match the one from lineage_bootstrap.py
    # so that we're updating the SAME entity.
    dataset_urn = str(DatasetUrn.create_from_ids(
        platform_id=PLATFORM,
        table_name=RAW_DATASET_NAME,
        env=ENV,
    ))

    logger.info(f"Target dataset URN: {dataset_urn}")

    # --- Announce the drift ---
    print()
    print("⚠️  Simulating schema drift: changing column 'amount' from DOUBLE to STRING")
    print()
    logger.info("Simulating schema drift: changing column amount from DOUBLE to STRING")

    # --- Build the drifted schema MCP ---
    # We re-emit the SchemaMetadataClass aspect for the same entity URN.
    # DataHub will treat this as an UPDATE to the existing schema.
    # The version is incremented to signal that this is a new version.
    drifted_schema_mcp = MetadataChangeProposalWrapper(
        entityUrn=dataset_urn,
        aspect=SchemaMetadataClass(
            schemaName=RAW_DATASET_NAME,
            platform=f"urn:li:dataPlatform:{PLATFORM}",
            # Increment the version to indicate a schema change.
            version=1,
            hash="",
            platformSchema=OtherSchemaClass(rawSchema=""),
            # Use the drifted fields — amount is now StringTypeClass.
            fields=_drifted_transaction_fields(),
        ),
    )

    # --- Emit to DataHub ---
    # This single emit call sends the new schema to DataHub GMS,
    # which will overwrite the previous schema for this dataset.
    emitter.emit(drifted_schema_mcp)

    logger.info(f"Schema drift emitted successfully for {dataset_urn}")

    # --- Confirmation messages ---
    print("─" * 60)
    print()
    print("🔴 Schema drift simulated!")
    print("   The 'amount' column type has been changed from DOUBLE to STRING")
    print("   in DataHub metadata.")
    print()
    print("─" * 60)
    print()
    print("📋 What happened:")
    print("   • The paysim_raw_transactions schema was re-emitted to DataHub")
    print("   • Column 'amount' type: DOUBLE → STRING")
    print("   • All other 10 columns remain unchanged")
    print()
    print("💥 Downstream impact (simulated scenario):")
    print("   • feature_engineering_table — amount_ratio computation will FAIL")
    print("     (cannot divide STRING by DOUBLE)")
    print("   • Fraud_Detection_ML_Model — input feature type mismatch")
    print("   • Any SQL queries casting amount to numeric may break")
    print()
    print("🔍 How to detect this in DataHub:")
    print("   • Check the schema change history in the dataset's 'Schema' tab")
    print("   • Set up schema change assertions/monitors")
    print("   • Use DataHub's impact analysis to see affected downstream entities")
    print()


# ===========================================================================
# Main entry point
# ===========================================================================
def main():
    """
    Main function for the schema drift simulation.

    Steps:
      1. Parse CLI arguments (optional --gms-url).
      2. Determine the GMS URL from CLI / env var / default.
      3. Connect to DataHub GMS.
      4. Emit the drifted schema.
      5. Print summary and next steps.
    """
    # --- Parse command-line arguments ---
    parser = argparse.ArgumentParser(
        description=(
            "Simulate a schema drift incident by changing the 'amount' column "
            "type from DOUBLE to STRING in the paysim_raw_transactions dataset."
        ),
    )
    parser.add_argument(
        "--gms-url",
        type=str,
        default=None,
        help="DataHub GMS URL (overrides DATAHUB_GMS_URL env var). Default: http://localhost:8080",
    )
    args = parser.parse_args()

    # Determine GMS URL: CLI arg takes priority > env var > default.
    gms_url = args.gms_url or os.environ.get("DATAHUB_GMS_URL", "http://localhost:8080")

    # --- Print banner ---
    print()
    print("=" * 60)
    print("⚠️  DataHub Schema Drift Simulator")
    print("=" * 60)
    print(f"📡 GMS URL : {gms_url}")
    print(f"📊 Dataset : {RAW_DATASET_NAME}")
    print(f"🔄 Change  : column 'amount' DOUBLE → STRING")
    print("=" * 60)

    # --- Initialize the DataHub REST emitter ---
    logger.info(f"Connecting to DataHub GMS at {gms_url}...")
    emitter = DatahubRestEmitter(gms_server=gms_url)

    # Test connection before proceeding.
    try:
        emitter.test_connection()
        print()
        print("✅ Connected to DataHub GMS")
    except Exception as e:
        print()
        print(f"❌ Failed to connect to DataHub GMS at {gms_url}")
        print(f"   Error: {e}")
        print()
        print("💡 Make sure DataHub is running. Set the URL via:")
        print("   export DATAHUB_GMS_URL=http://<host>:<port>")
        print("   or pass --gms-url <url>")
        sys.exit(1)

    # --- Run the schema drift simulation ---
    simulate_schema_drift(emitter)

    # --- Final message ---
    print("✅ Simulation complete. Check DataHub UI to observe the schema change.")
    print()


# ===========================================================================
# Entry point — standard Python idiom
# ===========================================================================
if __name__ == "__main__":
    main()
