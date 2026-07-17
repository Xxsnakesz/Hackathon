#!/usr/bin/env python3
"""
lineage_bootstrap.py — DataHub Metadata Lineage Bootstrap
==========================================================

This script uses the DataHub Python SDK (datahub-rest-emitter) to register
datasets, an ML model, lineage relationships, tags, and glossary descriptions
into a running DataHub instance.

Requirements (pip install):
    datahub[rest]
    # or individually:
    # acryl-datahub
    # datahub-rest-emitter

Usage:
    python lineage_bootstrap.py
    python lineage_bootstrap.py --gms-url http://datahub-gms:8080
    DATAHUB_GMS_URL=http://my-gms:8080 python lineage_bootstrap.py
"""

import argparse
import logging
import os
import sys
import time

# ---------------------------------------------------------------------------
# DataHub SDK imports
# ---------------------------------------------------------------------------
# DatahubRestEmitter is the main class that sends metadata events (MCPs)
# to the DataHub GMS (Generalized Metadata Service) REST endpoint.
from datahub.emitter.rest_emitter import DatahubRestEmitter

# MCP (MetadataChangeProposalWrapper) is the modern way to emit metadata.
# Each MCP represents a single metadata "aspect" change for a single entity.
from datahub.emitter.mcp import MetadataChangeProposalWrapper

# Schema-related classes for describing dataset columns and their types.
from datahub.metadata.schema_classes import (
    SchemaMetadataClass,          # Top-level schema container for a dataset
    SchemaFieldClass,             # Represents a single column/field
    SchemaFieldDataTypeClass,     # Wrapper around the actual type union
    NumberTypeClass,              # Numeric types (INT, DOUBLE, etc.)
    StringTypeClass,              # String/text types
    SchemalessClass,              # Schemaless marker (used as platformSchema)
    OtherSchemaClass,             # Generic schema representation
)

# Dataset-related classes for properties and lineage.
from datahub.metadata.schema_classes import (
    DatasetPropertiesClass,       # Key-value properties for a dataset
    UpstreamClass,                # Represents one upstream dataset in lineage
    UpstreamLineageClass,         # Container for all upstream lineage edges
    DatasetLineageTypeClass,      # (unused but available for lineage typing)
)

# ML Model-related classes for registering ML entities.
from datahub.metadata.schema_classes import (
    MLModelPropertiesClass,       # Properties for an ML model entity
)

# Tag-related classes for attaching tags to entities.
from datahub.metadata.schema_classes import (
    GlobalTagsClass,              # Container for a list of tag associations
    TagAssociationClass,          # Associates a single tag URN with an entity
)

# Institutional memory / documentation classes for adding descriptions
# to columns (editableSchemaMetadata) or entities.
from datahub.metadata.schema_classes import (
    EditableSchemaMetadataClass,           # Editable schema-level metadata
    EditableSchemaFieldInfoClass,          # Editable info for a single field
)

# Status class to mark entities as active (not soft-deleted).
from datahub.metadata.schema_classes import StatusClass

# Audit stamp for recording who/when metadata was created.
from datahub.metadata.schema_classes import AuditStampClass

# ---------------------------------------------------------------------------
# URN builder utilities — these create properly formatted DataHub URNs.
# ---------------------------------------------------------------------------
from datahub.metadata.urns import DatasetUrn, TagUrn

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
# Constants
# ===========================================================================

# The data platform where our datasets live (PostgreSQL).
PLATFORM = "postgres"

# Default DataHub environment (PROD).
ENV = "PROD"

# Dataset names as they appear in the data platform.
RAW_DATASET_NAME = "paysim_raw_transactions"
FEATURE_DATASET_NAME = "feature_engineering_table"

# ML Model name.
ML_MODEL_NAME = "Fraud_Detection_ML_Model"

# Tags to attach to the ML model.
MODEL_TAGS = ["production", "fintech", "fraud-detection"]


# ===========================================================================
# Helper: Build a SchemaFieldClass for a single column
# ===========================================================================
def _make_field(field_path: str, field_type, description: str = "") -> SchemaFieldClass:
    """
    Create a SchemaFieldClass instance for a single column.

    Args:
        field_path:  The column name (e.g. 'amount').
        field_type:  An instance of a DataHub type class (NumberTypeClass or StringTypeClass).
        description: Optional human-readable description of the column.

    Returns:
        A fully populated SchemaFieldClass.
    """
    return SchemaFieldClass(
        fieldPath=field_path,
        # nativeDataType is a free-text string shown in the UI for the raw type.
        nativeDataType=field_path,
        # type wraps the actual union type (NumberTypeClass, StringTypeClass, etc.)
        type=SchemaFieldDataTypeClass(type=field_type),
        description=description,
    )


# ===========================================================================
# Step 1 & 2: Define the schemas for both datasets
# ===========================================================================

def _raw_transaction_fields() -> list:
    """
    Return the 11 columns of the raw PaySim transactions dataset.

    Column definitions:
        step              INT     — Time step of the simulation (1 step = 1 hour)
        type              STRING  — Transaction type (PAYMENT, TRANSFER, CASH_OUT, etc.)
        amount            DOUBLE  — Transaction amount in local currency
        nameOrig          STRING  — Originator account identifier
        oldbalanceOrg     DOUBLE  — Originator balance before the transaction
        newbalanceOrig    DOUBLE  — Originator balance after the transaction
        nameDest          STRING  — Destination account identifier
        oldbalanceDest    DOUBLE  — Destination balance before the transaction
        newbalanceDest    DOUBLE  — Destination balance after the transaction
        isFraud           INT     — Binary label: 1 if transaction is fraudulent
        isFlaggedFraud    INT     — Binary label: 1 if flagged by simple rule engine
    """
    return [
        _make_field("step",             NumberTypeClass(), "Time step of the simulation (1 step = 1 hour of real time)"),
        _make_field("type",             StringTypeClass(), "Transaction type: PAYMENT, TRANSFER, CASH_OUT, DEBIT, CASH_IN"),
        _make_field("amount",           NumberTypeClass(), "Transaction amount in the local currency. Critical feature for fraud detection."),
        _make_field("nameOrig",         StringTypeClass(), "Originator customer account identifier"),
        _make_field("oldbalanceOrg",    NumberTypeClass(), "Originator account balance before the transaction"),
        _make_field("newbalanceOrig",   NumberTypeClass(), "Originator account balance after the transaction"),
        _make_field("nameDest",         StringTypeClass(), "Destination customer account identifier"),
        _make_field("oldbalanceDest",   NumberTypeClass(), "Destination account balance before the transaction"),
        _make_field("newbalanceDest",   NumberTypeClass(), "Destination account balance after the transaction"),
        _make_field("isFraud",          NumberTypeClass(), "Ground truth fraud label: 1 = fraudulent, 0 = legitimate. Target variable for ML model."),
        _make_field("isFlaggedFraud",   NumberTypeClass(), "Flag set by a simple rule-based engine (large transfers > 200k)"),
    ]


def _feature_table_fields() -> list:
    """
    Return all columns for the feature engineering table.

    This includes the original 11 raw columns PLUS 5 computed feature columns
    that are derived during the feature engineering pipeline:
        amount_ratio           — Ratio of amount to originator's old balance
        balance_change_orig    — Change in originator balance (new - old)
        balance_change_dest    — Change in destination balance (new - old)
        is_large_transaction   — Binary flag: 1 if amount > threshold
        is_balance_mismatch    — Binary flag: 1 if balance changes are inconsistent
    """
    # Start with all 11 original columns.
    fields = _raw_transaction_fields()

    # Append the 5 computed feature columns.
    fields.extend([
        _make_field("amount_ratio",          NumberTypeClass(), "Ratio of transaction amount to originator's old balance (amount / oldbalanceOrg)"),
        _make_field("balance_change_orig",   NumberTypeClass(), "Change in originator balance: newbalanceOrig - oldbalanceOrg"),
        _make_field("balance_change_dest",   NumberTypeClass(), "Change in destination balance: newbalanceDest - oldbalanceDest"),
        _make_field("is_large_transaction",  NumberTypeClass(), "Binary flag: 1 if the transaction amount exceeds a defined threshold"),
        _make_field("is_balance_mismatch",   NumberTypeClass(), "Binary flag: 1 if the balance changes are inconsistent with the transaction amount"),
    ])
    return fields


# ===========================================================================
# Step 1: Register the raw transactions dataset
# ===========================================================================
def register_raw_dataset(emitter: DatahubRestEmitter) -> str:
    """
    Register the paysim_raw_transactions dataset in DataHub.

    This emits three MCPs:
      1. DatasetProperties — name, description, custom properties.
      2. SchemaMetadata   — the 11-column schema definition.
      3. Status           — marks the entity as active (not removed).

    Returns:
        The dataset URN string.
    """
    # Build the fully qualified dataset URN.
    # Format: urn:li:dataset:(urn:li:dataPlatform:postgres,paysim_raw_transactions,PROD)
    dataset_urn = str(DatasetUrn.create_from_ids(
        platform_id=PLATFORM,
        table_name=RAW_DATASET_NAME,
        env=ENV,
    ))

    print(f"📦 Registering raw dataset: {RAW_DATASET_NAME}")
    logger.info(f"Dataset URN: {dataset_urn}")

    # --- MCP 1: Dataset Properties ---
    # DatasetPropertiesClass holds general metadata like name, description,
    # and arbitrary key-value custom properties shown in the DataHub UI.
    properties_mcp = MetadataChangeProposalWrapper(
        entityUrn=dataset_urn,
        aspect=DatasetPropertiesClass(
            name=RAW_DATASET_NAME,
            description=(
                "Raw PaySim financial transaction dataset. Contains simulated mobile money "
                "transactions with fraud labels. This is the primary source table ingested "
                "from the PostgreSQL operational database."
            ),
            customProperties={
                "source_system": "PostgreSQL",
                "data_classification": "PII-containing",
                "update_frequency": "daily",
                "row_count_approx": "6_362_620",
                "pipeline": "fraud-detection-v2",
            },
        ),
    )

    # --- MCP 2: Schema Metadata ---
    # SchemaMetadataClass describes the full schema (columns) of the dataset.
    # The hash is used for change detection — we use a simple static hash here.
    schema_mcp = MetadataChangeProposalWrapper(
        entityUrn=dataset_urn,
        aspect=SchemaMetadataClass(
            schemaName=RAW_DATASET_NAME,
            platform=f"urn:li:dataPlatform:{PLATFORM}",
            version=0,
            hash="",
            # platformSchema is required; OtherSchemaClass is a catch-all.
            platformSchema=OtherSchemaClass(rawSchema=""),
            fields=_raw_transaction_fields(),
        ),
    )

    # --- MCP 3: Status ---
    # Setting removed=False marks this entity as active in DataHub.
    status_mcp = MetadataChangeProposalWrapper(
        entityUrn=dataset_urn,
        aspect=StatusClass(removed=False),
    )

    # Emit all three MCPs to DataHub GMS.
    for mcp in [properties_mcp, schema_mcp, status_mcp]:
        emitter.emit(mcp)

    print(f"  ✅ Raw dataset registered successfully with 11 columns")
    logger.info(f"Emitted properties, schema, and status for {RAW_DATASET_NAME}")

    return dataset_urn


# ===========================================================================
# Step 2: Register the feature engineering dataset
# ===========================================================================
def register_feature_dataset(emitter: DatahubRestEmitter) -> str:
    """
    Register the feature_engineering_table dataset in DataHub.

    This table contains the original 11 columns from paysim_raw_transactions
    plus 5 additional computed feature columns used by the ML model.

    Returns:
        The dataset URN string.
    """
    dataset_urn = str(DatasetUrn.create_from_ids(
        platform_id=PLATFORM,
        table_name=FEATURE_DATASET_NAME,
        env=ENV,
    ))

    print(f"📦 Registering feature dataset: {FEATURE_DATASET_NAME}")
    logger.info(f"Dataset URN: {dataset_urn}")

    # --- MCP 1: Dataset Properties ---
    properties_mcp = MetadataChangeProposalWrapper(
        entityUrn=dataset_urn,
        aspect=DatasetPropertiesClass(
            name=FEATURE_DATASET_NAME,
            description=(
                "Feature-engineered dataset derived from paysim_raw_transactions. "
                "Includes the original 11 columns plus 5 computed feature columns "
                "(amount_ratio, balance_change_orig, balance_change_dest, "
                "is_large_transaction, is_balance_mismatch) used as input features "
                "for the Fraud Detection ML Model."
            ),
            customProperties={
                "source_table": RAW_DATASET_NAME,
                "feature_count": "16",
                "pipeline_stage": "feature_engineering",
                "transformation_engine": "pandas",
            },
        ),
    )

    # --- MCP 2: Schema Metadata (11 original + 5 computed = 16 columns) ---
    schema_mcp = MetadataChangeProposalWrapper(
        entityUrn=dataset_urn,
        aspect=SchemaMetadataClass(
            schemaName=FEATURE_DATASET_NAME,
            platform=f"urn:li:dataPlatform:{PLATFORM}",
            version=0,
            hash="",
            platformSchema=OtherSchemaClass(rawSchema=""),
            fields=_feature_table_fields(),
        ),
    )

    # --- MCP 3: Status ---
    status_mcp = MetadataChangeProposalWrapper(
        entityUrn=dataset_urn,
        aspect=StatusClass(removed=False),
    )

    for mcp in [properties_mcp, schema_mcp, status_mcp]:
        emitter.emit(mcp)

    print(f"  ✅ Feature dataset registered successfully with 16 columns (11 original + 5 computed)")
    logger.info(f"Emitted properties, schema, and status for {FEATURE_DATASET_NAME}")

    return dataset_urn


# ===========================================================================
# Step 3: Register the ML Model entity
# ===========================================================================
def register_ml_model(emitter: DatahubRestEmitter) -> str:
    """
    Register the Fraud_Detection_ML_Model as a dataset in DataHub.
    (Modeled as a dataset to natively support upstreamLineage in the UI).
    """
    # Dataset URN format for ML Model
    model_urn = f"urn:li:dataset:(urn:li:dataPlatform:mlflow,{ML_MODEL_NAME},{ENV})"

    print(f"🤖 Registering ML model: {ML_MODEL_NAME}")
    logger.info(f"ML Model URN: {model_urn}")

    # --- MCP 1: Dataset Properties ---
    properties_mcp = MetadataChangeProposalWrapper(
        entityUrn=model_urn,
        aspect=DatasetPropertiesClass(
            name=ML_MODEL_NAME,
            description="Production fraud detection model for PaySim financial transactions (Registered as Dataset for Lineage)",
            customProperties={
                "type": "XGBoost Classifier",
                "framework": "XGBoost",
                "model_version": "2.0",
                "training_dataset": FEATURE_DATASET_NAME,
                "accuracy": "0.9987",
                "f1_score": "0.82",
                "deployment_status": "production",
            },
        ),
    )

    # --- MCP 2: Status ---
    status_mcp = MetadataChangeProposalWrapper(
        entityUrn=model_urn,
        aspect=StatusClass(removed=False),
    )

    for mcp in [properties_mcp, status_mcp]:
        emitter.emit(mcp)

    print(f"  ✅ ML Model registered as dataset (for lineage support)")
    logger.info(f"Emitted properties and status for {ML_MODEL_NAME}")

    return model_urn


# ===========================================================================
# Step 4: Create lineage edges
# ===========================================================================
def create_lineage(
    emitter: DatahubRestEmitter,
    raw_dataset_urn: str,
    feature_dataset_urn: str,
    model_urn: str,
) -> None:
    """
    Create lineage edges between entities in DataHub.

    Lineage in DataHub is stored on the DOWNSTREAM entity as an
    UpstreamLineageClass aspect that lists all its upstream dependencies.

    We create two lineage edges:
      1. paysim_raw_transactions  →  feature_engineering_table
         (raw data flows into the feature engineering pipeline)
      2. feature_engineering_table  →  Fraud_Detection_ML_Model
         (features are consumed by the ML model for training/inference)
    """
    print("🔗 Creating lineage edges...")

    # --- Lineage Edge 1: raw → feature ---
    # The feature_engineering_table has paysim_raw_transactions as its upstream.
    # We attach this lineage to the DOWNSTREAM entity (feature table).
    feature_lineage_mcp = MetadataChangeProposalWrapper(
        entityUrn=feature_dataset_urn,
        aspect=UpstreamLineageClass(
            upstreams=[
                UpstreamClass(
                    dataset=raw_dataset_urn,
                    type="TRANSFORMED",  # The data is transformed, not just copied.
                )
            ]
        ),
    )
    emitter.emit(feature_lineage_mcp)
    print(f"  ✅ Lineage: {RAW_DATASET_NAME} → {FEATURE_DATASET_NAME}")
    logger.info(f"Lineage edge created: {raw_dataset_urn} → {feature_dataset_urn}")

    # --- Lineage Edge 2: feature → ML model ---
    # For ML model lineage, we emit an UpstreamLineage on the model entity.
    # Note: DataHub also supports MLModel-specific lineage via mlModelFactors,
    # but UpstreamLineage is the most common and visual approach.
    model_lineage_mcp = MetadataChangeProposalWrapper(
        entityUrn=model_urn,
        aspect=UpstreamLineageClass(
            upstreams=[
                UpstreamClass(
                    dataset=feature_dataset_urn,
                    type="TRANSFORMED",
                )
            ]
        ),
    )
    emitter.emit(model_lineage_mcp)
    print(f"  ✅ Lineage: {FEATURE_DATASET_NAME} → {ML_MODEL_NAME}")
    logger.info(f"Lineage edge created: {feature_dataset_urn} → {model_urn}")


# ===========================================================================
# Step 5: Add tags to the ML model
# ===========================================================================
def add_model_tags(emitter: DatahubRestEmitter, model_urn: str) -> None:
    """
    Attach tags to the ML model entity.

    Tags in DataHub are standalone entities identified by URNs. When we
    associate a tag with another entity, DataHub auto-creates the tag if
    it doesn't already exist.

    We add three tags: 'production', 'fintech', 'fraud-detection'.
    """
    print("🏷️  Adding tags to ML model...")

    # Build TagAssociationClass instances for each tag.
    # TagUrn creates URNs in the format: urn:li:tag:<tagName>
    tag_associations = [
        TagAssociationClass(tag=str(TagUrn(tag_name)))
        for tag_name in MODEL_TAGS
    ]

    # GlobalTagsClass is the aspect that holds all tag associations for an entity.
    tags_mcp = MetadataChangeProposalWrapper(
        entityUrn=model_urn,
        aspect=GlobalTagsClass(tags=tag_associations),
    )
    emitter.emit(tags_mcp)

    tag_list = ", ".join(MODEL_TAGS)
    print(f"  ✅ Tags added: [{tag_list}]")
    logger.info(f"Added tags {MODEL_TAGS} to {model_urn}")


# ===========================================================================
# Step 6: Add descriptions to key columns
# ===========================================================================
def add_column_descriptions(emitter: DatahubRestEmitter, raw_dataset_urn: str) -> None:
    """
    Add rich editable descriptions / glossary information to key columns
    of the raw dataset.

    We use EditableSchemaMetadataClass to add detailed descriptions to the
    `amount` and `isFraud` columns. These descriptions appear in the DataHub
    UI alongside the schema and can be edited by data stewards.

    Note: This is separate from the description set in SchemaFieldClass.
    EditableSchemaMetadata allows post-hoc annotations without re-emitting
    the full schema.
    """
    print("📝 Adding glossary descriptions to key columns...")

    # Build editable field info for the two most important columns.
    editable_fields = [
        EditableSchemaFieldInfoClass(
            fieldPath="amount",
            description=(
                "💰 GLOSSARY: Transaction Amount — The monetary value of the financial "
                "transaction in local currency units. This is the single most important "
                "feature for fraud detection. Large amounts (>200,000) are automatically "
                "flagged by the rule engine. The ML model uses this along with balance "
                "ratios to detect anomalous patterns. Data type: DOUBLE (float64). "
                "Range: 0.0 to 92,445,516.64 in the training dataset."
            ),
        ),
        EditableSchemaFieldInfoClass(
            fieldPath="isFraud",
            description=(
                "🚨 GLOSSARY: Fraud Label (Ground Truth) — Binary classification target "
                "variable for the ML model. Value 1 indicates the transaction is confirmed "
                "fraudulent; value 0 indicates legitimate. In the PaySim dataset, only ~0.13% "
                "of transactions are fraudulent, making this a highly imbalanced classification "
                "problem. Only TRANSFER and CASH_OUT transaction types contain fraud cases. "
                "Data type: INT (0 or 1)."
            ),
        ),
    ]

    editable_schema_mcp = MetadataChangeProposalWrapper(
        entityUrn=raw_dataset_urn,
        aspect=EditableSchemaMetadataClass(
            editableSchemaFieldInfo=editable_fields,
        ),
    )
    emitter.emit(editable_schema_mcp)

    print("  ✅ Glossary descriptions added to columns: amount, isFraud")
    logger.info(f"Added editable column descriptions to {raw_dataset_urn}")


# ===========================================================================
# Main orchestrator
# ===========================================================================
def main():
    """
    Main entry point. Orchestrates the full metadata bootstrap process:
      1. Connect to DataHub GMS
      2. Register the raw dataset (paysim_raw_transactions)
      3. Register the feature dataset (feature_engineering_table)
      4. Register the ML model (Fraud_Detection_ML_Model)
      5. Create lineage edges between all three entities
      6. Add tags to the ML model
      7. Add glossary descriptions to key columns
    """
    # --- Parse command-line arguments ---
    parser = argparse.ArgumentParser(
        description="Bootstrap DataHub metadata: datasets, ML model, lineage, tags, and glossary terms.",
    )
    parser.add_argument(
        "--gms-url",
        type=str,
        default=None,
        help="DataHub GMS URL (overrides DATAHUB_GMS_URL env var). Default: http://localhost:8085",
    )
    args = parser.parse_args()

    # Determine the GMS URL: CLI arg > env var > default.
    gms_url = args.gms_url or os.environ.get("DATAHUB_GMS_URL", "http://localhost:8085")

    # --- Print banner ---
    print()
    print("=" * 70)
    print("🚀 DataHub Metadata Lineage Bootstrap")
    print("=" * 70)
    print(f"📡 GMS URL : {gms_url}")
    print(f"📊 Platform: {PLATFORM}")
    print(f"🌍 Environment: {ENV}")
    print("=" * 70)
    print()

    # --- Initialize the DataHub REST emitter ---
    # The emitter handles HTTP communication with the DataHub GMS REST API.
    # All metadata is sent as MetadataChangeProposal (MCP) events.
    logger.info(f"Connecting to DataHub GMS at {gms_url}...")
    emitter = DatahubRestEmitter(gms_server=gms_url)

    # Test the connection to ensure GMS is reachable.
    try:
        emitter.test_connection()
        print("✅ Successfully connected to DataHub GMS!")
        print()
    except Exception as e:
        print(f"❌ Failed to connect to DataHub GMS at {gms_url}")
        print(f"   Error: {e}")
        print()
        print("💡 Make sure DataHub is running and the GMS URL is correct.")
        print("   You can set it via: export DATAHUB_GMS_URL=http://<host>:<port>")
        sys.exit(1)

    start_time = time.time()

    # --- Step 1: Register the raw transactions dataset ---
    print("─" * 50)
    print("📋 STEP 1: Register raw transactions dataset")
    print("─" * 50)
    raw_dataset_urn = register_raw_dataset(emitter)
    print()

    # --- Step 2: Register the feature engineering dataset ---
    print("─" * 50)
    print("📋 STEP 2: Register feature engineering dataset")
    print("─" * 50)
    feature_dataset_urn = register_feature_dataset(emitter)
    print()

    # --- Step 3: Register the ML model ---
    print("─" * 50)
    print("📋 STEP 3: Register ML model")
    print("─" * 50)
    model_urn = register_ml_model(emitter)
    print()

    # --- Step 4: Create lineage edges ---
    print("─" * 50)
    print("📋 STEP 4: Create lineage edges")
    print("─" * 50)
    create_lineage(emitter, raw_dataset_urn, feature_dataset_urn, model_urn)
    print()

    # --- Step 5: Add tags to the ML model ---
    print("─" * 50)
    print("📋 STEP 5: Add tags to ML model")
    print("─" * 50)
    add_model_tags(emitter, model_urn)
    print()

    # --- Step 6: Add glossary descriptions to key columns ---
    print("─" * 50)
    print("📋 STEP 6: Add glossary descriptions to key columns")
    print("─" * 50)
    add_column_descriptions(emitter, raw_dataset_urn)
    print()

    elapsed = time.time() - start_time

    # --- Summary ---
    print("=" * 70)
    print("🎉 Metadata Bootstrap Complete!")
    print("=" * 70)
    print()
    print("📊 Entities registered:")
    print(f"   • Dataset: {RAW_DATASET_NAME} (11 columns)")
    print(f"   • Dataset: {FEATURE_DATASET_NAME} (16 columns)")
    print(f"   • ML Model: {ML_MODEL_NAME} (XGBoost Classifier)")
    print()
    print("🔗 Lineage edges created:")
    print(f"   • {RAW_DATASET_NAME} → {FEATURE_DATASET_NAME}")
    print(f"   • {FEATURE_DATASET_NAME} → {ML_MODEL_NAME}")
    print()
    print("🏷️  Tags added to ML model:")
    print(f"   • {', '.join(MODEL_TAGS)}")
    print()
    print("📝 Column glossary descriptions added:")
    print(f"   • amount — Transaction amount description & data range")
    print(f"   • isFraud — Fraud label description & class distribution")
    print()
    print(f"⏱️  Total time: {elapsed:.2f}s")
    print()
    print("💡 View your lineage graph at:")
    print(f"   {gms_url.replace(':8080', ':9002')}/dataset/{raw_dataset_urn}/Lineage")
    print()


# ===========================================================================
# Entry point
# ===========================================================================
if __name__ == "__main__":
    main()
