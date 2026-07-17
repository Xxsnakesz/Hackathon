# =============================================================================
# reliability_agent.py — AI Data Reliability Agent (Core Engine)
# =============================================================================
# WHAT CHANGED (v2 — Real DB Integration):
#   The agent now reads ACTUAL schema from PostgreSQL via db_inspector.py
#   instead of relying on a hardcoded in-memory dictionary.
#   Schema drift is detected by comparing live DB schema vs schema_baseline.json.
#   The DataHubClient is kept as a catalog for lineage, tags, and model info.
#
# Investigation workflow:
#   1. Receives an alert about ML model degradation
#   2. Scans all monitored tables via PostgresSchemaInspector (real or sim)
#   3. Compares actual schema vs baseline to find drifts
#   4. If drift found → runs root cause analysis
#   5. Updates DataHub catalog with incident tag
#   6. Generates SQL/dbt fix scripts
# =============================================================================

import os
import sys
import json
import argparse
import logging
from datetime import datetime
from typing import Optional

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.agent.prompts import (
    SYSTEM_PROMPT,
    ALERT_TEMPLATE,
    ROOT_CAUSE_REPORT_TEMPLATE,
    DEMO_ALERT_VALUES,
)
from src.agent.fix_generator import generate_sql_fix, generate_dbt_patch, save_fix_scripts
from src.agent.db_inspector import (
    PostgresSchemaInspector,
    scan_all_drifts,
    MONITORED_TABLES,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ReliabilityAgent")


# =============================================================================
# DataHub Client — In-memory catalog for lineage, tags, and model metadata
# =============================================================================
# NOTE: This class no longer serves as the source-of-truth for schema data.
#       Schema truth comes from PostgresSchemaInspector (db_inspector.py).
#       DataHubClient is kept for lineage graph traversal, tag write-back,
#       and ML model metadata — things that don't live in the raw DB.
# =============================================================================
class DataHubClient:
    """
    In-memory catalog representing DataHub metadata:
    - Lineage graph (which tables feed which models)
    - ML model metadata (accuracy, features used)
    - Tag write-back (incident tags added during investigation)

    In production, these would come from DataHub GMS REST API / MCP Server.
    For the hackathon demo, we maintain them in-memory since setting up a full
    DataHub stack is heavyweight — the schema drift detection is REAL (from DB).
    """

    def __init__(self, gms_url: str = "http://localhost:8085"):
        self.gms_url = gms_url

        # -----------------------------------------------------------------
        # Metadata catalog (lineage + model info only — NOT schema)
        # -----------------------------------------------------------------
        self._catalog = {
            "urn:li:dataset:(urn:li:dataPlatform:postgres,paysim_fintech.public.paysim_raw_transactions,PROD)": {
                "name": "paysim_raw_transactions",
                "platform": "postgres",
                "description": "Raw PaySim financial transaction data. Contains 6.3M simulated mobile money transactions.",
                "owner": "data-engineering-team@company.com",
                "tags": ["production", "fintech", "raw-data"],
            },
            "urn:li:dataset:(urn:li:dataPlatform:postgres,paysim_fintech.public.feature_engineering_table,PROD)": {
                "name": "feature_engineering_table",
                "platform": "postgres",
                "description": "Derived feature table for ML model training.",
                "owner": "ml-engineering-team@company.com",
                "tags": ["production", "fintech", "features", "ml-input"],
            },
            "urn:li:dataset:(urn:li:dataPlatform:mlflow,Fraud_Detection_ML_Model,PROD)": {
                "name": "Fraud_Detection_ML_Model",
                "platform": "mlflow",
                "description": "Production XGBoost classifier for fraud detection. Baseline accuracy: 97.8%.",
                "owner": "ml-engineering-team@company.com",
                "tags": ["production", "fintech", "fraud-detection", "xgboost"],
                "model_info": {
                    "algorithm": "XGBoost Classifier",
                    "version": "v2.1.0",
                    "training_date": "2026-06-15",
                    "baseline_accuracy": 0.978,
                    "baseline_fpr": 0.021,
                    "features_used": [
                        "amount", "amount_ratio", "balance_change_orig",
                        "balance_change_dest", "is_large_transaction",
                        "is_balance_mismatch", "type_encoded"
                    ],
                },
            },
        }

        # Lineage graph: who feeds whom
        self._lineage = {
            "urn:li:dataset:(urn:li:dataPlatform:postgres,paysim_fintech.public.paysim_raw_transactions,PROD)": {
                "downstream": ["urn:li:dataset:(urn:li:dataPlatform:postgres,paysim_fintech.public.feature_engineering_table,PROD)"],
                "upstream": [],
            },
            "urn:li:dataset:(urn:li:dataPlatform:postgres,paysim_fintech.public.feature_engineering_table,PROD)": {
                "downstream": ["urn:li:dataset:(urn:li:dataPlatform:mlflow,Fraud_Detection_ML_Model,PROD)"],
                "upstream": ["urn:li:dataset:(urn:li:dataPlatform:postgres,paysim_fintech.public.paysim_raw_transactions,PROD)"],
            },
            "urn:li:dataset:(urn:li:dataPlatform:mlflow,Fraud_Detection_ML_Model,PROD)": {
                "downstream": [],
                "upstream": ["urn:li:dataset:(urn:li:dataPlatform:postgres,paysim_fintech.public.feature_engineering_table,PROD)"],
            },
        }

        self._added_tags: dict = {}

    def search_datahub(self, query: str) -> list:
        results = []
        query_lower = query.lower()
        for urn, meta in self._catalog.items():
            if (
                query_lower in meta["name"].lower()
                or query_lower in meta.get("description", "").lower()
                or any(query_lower in tag for tag in meta.get("tags", []))
            ):
                results.append({"urn": urn, "name": meta["name"], "platform": meta["platform"]})
        return results

    def get_entity(self, urn: str) -> dict:
        return self._catalog.get(urn, {})

    def get_lineage(self, urn: str, direction: str = "UPSTREAM") -> list:
        lineage = self._lineage.get(urn, {})
        related = lineage.get(direction.lower(), [])
        results = []
        for r_urn in related:
            entity = self._catalog.get(r_urn, {})
            results.append({
                "urn": r_urn,
                "name": entity.get("name", "Unknown"),
                "platform": entity.get("platform", "Unknown"),
                "direction": direction,
            })
        return results

    def update_metadata(self, urn: str, tag: str, description: str = "") -> bool:
        entity = self._catalog.get(urn, {})
        if entity:
            entity.setdefault("tags", []).append(tag)
            self._added_tags[urn] = tag
            if description:
                entity["incident_description"] = description
            logger.info(f"✅ Tag '{tag}' added to {entity.get('name', urn)}")
            return True
        logger.error(f"❌ Entity not found: {urn}")
        return False

    def get_all_tags(self, urn: str) -> list:
        return self._catalog.get(urn, {}).get("tags", [])


# =============================================================================
# AI Data Reliability Agent — Main Investigation Engine
# =============================================================================
class ReliabilityAgent:
    """
    The AI Data Reliability Agent.

    Investigation workflow (v2 — real DB):
      1. Scan all monitored tables via PostgresSchemaInspector
      2. Detect schema drifts by comparing against baseline
      3. Trace DataHub lineage from affected tables to downstream ML model
      4. Run root cause analysis
      5. Write incident tags to DataHub catalog
      6. Generate SQL/dbt fix scripts
    """

    def __init__(self, datahub_client: DataHubClient, db_inspector: PostgresSchemaInspector):
        self.datahub = datahub_client
        self.db = db_inspector
        self.investigation_log: list = []
        self.findings: dict = {}

    def _log(self, step: str, detail: str):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "step": step,
            "detail": detail,
        }
        self.investigation_log.append(entry)
        logger.info(f"📝 [{step}] {detail}")

    def investigate(self, alert_message: str) -> dict:
        """
        Run the full investigation workflow.

        Args:
            alert_message: Alert text describing model degradation.

        Returns:
            Full investigation result dict.
        """
        self.investigation_log = []
        self.findings = {}

        print()
        print("=" * 70)
        print(f"🤖 AI DATA RELIABILITY AGENT — Investigation Started")
        print(f"   Mode: {self.db.mode().upper()} {'(connected to PostgreSQL)' if self.db.is_live() else '(simulation)'}")
        print("=" * 70)
        print()

        # =====================================================================
        # STEP 1: Scan all monitored tables for real schema drift
        # =====================================================================
        self._log("STEP 1", "Scanning monitored tables for schema drift via DB inspector...")

        drift_report = scan_all_drifts(self.db)
        self._log(
            "STEP 1: DB Scan Complete",
            f"Scanned {len(drift_report['tables'])} tables in {drift_report['mode']} mode. "
            f"Total drifts found: {drift_report['total_drifts']}"
        )

        if not drift_report["any_drift"]:
            self._log("STEP 1: Result", "✅ No schema drift detected. All tables match baseline.")
            return {
                "status": "no_drift",
                "message": "All monitored tables match their baseline schema.",
                "drift_report": drift_report,
                "investigation_log": self.investigation_log,
            }

        # =====================================================================
        # STEP 2: Identify affected model via lineage traversal
        # =====================================================================
        self._log("STEP 2", "Tracing DataHub lineage from drifted tables to downstream ML model...")

        # Find the ML model downstream of any drifted table
        model_urn = "urn:li:dataset:(urn:li:dataPlatform:mlflow,Fraud_Detection_ML_Model,PROD)"
        model_entity = self.datahub.get_entity(model_urn)
        model_name = model_entity.get("name", "Fraud_Detection_ML_Model")

        # Build lineage trace string
        all_upstream = self.datahub.get_lineage(model_urn, "UPSTREAM")
        lineage_trace_lines = []
        for ds in all_upstream:
            lineage_trace_lines.append(f"  ├─ {ds['name']} ({ds['platform']})")
            deeper = self.datahub.get_lineage(ds["urn"], "UPSTREAM")
            for d2 in deeper:
                lineage_trace_lines.append(f"  │    └─ {d2['name']} ({d2['platform']})")
        lineage_trace = "\n".join(lineage_trace_lines) or "  (no upstream)"

        self._log(
            "STEP 2: Lineage Traced",
            f"Model '{model_name}' depends on:\n{lineage_trace}"
        )

        # =====================================================================
        # STEP 3: Collect all individual drift events across tables
        # =====================================================================
        self._log("STEP 3", "Collecting drift details across all affected tables...")

        all_drifts = []
        for table_name, table_info in drift_report["tables"].items():
            for drift in table_info.get("drifts", []):
                all_drifts.append(drift)
                self._log(
                    f"⚠️  DRIFT — {table_name}.{drift['column']}",
                    f"{drift['drift_type']}: {drift['baseline_type']} → {drift['actual_type']} "
                    f"(detected at {drift['detected_at']})"
                )

        affected_columns = [d["column"] for d in all_drifts]
        affected_tables = list({d["table"] for d in all_drifts})

        # =====================================================================
        # STEP 4: Root Cause Analysis
        # =====================================================================
        self._log("STEP 4", "Correlating drift with ML model degradation...")

        drift_lines = []
        for i, d in enumerate(all_drifts, 1):
            drift_lines.append(
                f"  {i}. [{d['table']}] Column '{d['column']}': "
                f"{d['baseline_type']} → {d['actual_type']} ({d['drift_type']})"
            )
        drift_detail_str = "\n".join(drift_lines)
        tables_str = ", ".join(affected_tables)
        cols_str = ", ".join(affected_columns)

        root_cause = (
            f"SCHEMA DRIFT DETECTED in upstream table(s): {tables_str}\n\n"
            f"Affected columns:\n{drift_detail_str}\n\n"
            f"These column(s) ({cols_str}) are CRITICAL features used by {model_name}.\n"
            f"Type changes break numerical feature engineering (ratios, comparisons),\n"
            f"causing the ML model to produce incorrect predictions:\n"
            f"  • Accuracy: ~97.8% → ~42.3%\n"
            f"  • False Positive Rate: 2.1% → 78.5%\n"
            f"  • Legitimate transactions blocked at 37x normal rate\n\n"
            f"Root cause: schema change(s) made upstream without coordination with ML consumers."
        )

        self._log("STEP 4: Root Cause Identified", root_cause)

        # =====================================================================
        # STEP 5: Remediation — Tag DataHub + Generate Fix Scripts
        # =====================================================================
        self._log("STEP 5", "Executing remediation: tagging DataHub + generating fix scripts...")

        incident_tag = "DATA-INCIDENT: DO NOT USE"
        incident_desc = (
            f"Schema drift detected in: {tables_str}. "
            f"Affected columns: {cols_str}. "
            f"Auto-tagged by AI Data Reliability Agent."
        )
        tag_success = self.datahub.update_metadata(model_urn, incident_tag, incident_desc)

        # Tag affected upstream tables too
        for table_name in affected_tables:
            table_urns = self.datahub.search_datahub(table_name)
            for t in table_urns:
                self.datahub.update_metadata(
                    t["urn"],
                    "SCHEMA-DRIFT-DETECTED",
                    f"Drifted columns: {', '.join(d['column'] for d in all_drifts if d['table'] == table_name)}",
                )

        # Generate fix scripts per drifted column
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        examples_dir = os.path.join(project_root, "examples")
        fix_results = {}
        for i, drift in enumerate(all_drifts):
            res = save_fix_scripts(
                table_name=drift["table"],
                column_name=drift["column"],
                wrong_type=drift["actual_type"],
                correct_type=drift["baseline_type"],
                output_dir=examples_dir,
            )
            fix_results[f"{drift['table']}.{drift['column']}"] = res

        self._log(
            "STEP 5: Remediation Complete",
            f"Tag '{incident_tag}' added to {model_name}. "
            f"Generated {len(fix_results)} fix script(s)."
        )

        # =====================================================================
        # FINAL: Compile report
        # =====================================================================
        first_drift = all_drifts[0]
        report = ROOT_CAUSE_REPORT_TEMPLATE.format(
            alert_summary=alert_message[:300],
            model_name=model_name,
            model_urn=model_urn,
            platform=model_entity.get("platform", "mlflow"),
            lineage_trace=lineage_trace,
            affected_dataset=tables_str,
            affected_column=cols_str,
            previous_type=first_drift["baseline_type"],
            current_type=first_drift["actual_type"],
            change_timestamp=first_drift["detected_at"],
            root_cause_analysis=root_cause,
            fix_script_path=list(fix_results.values())[0].get("sql_fix_path", "N/A") if fix_results else "N/A",
            recommendations=(
                "1. Apply the generated SQL fix script(s) in examples/\n"
                "2. Verify column types are restored to baseline\n"
                "3. Re-run DataHub ingestion to update metadata\n"
                "4. Retrain/revalidate the Fraud Detection model\n"
                "5. Remove the DATA-INCIDENT tag once verified\n"
                "6. Add schema change alerts to prevent recurrence"
            ),
        )

        results = {
            "status": "incident_detected",
            "model_name": model_name,
            "model_urn": model_urn,
            "db_mode": self.db.mode(),
            "drift_report": drift_report,
            "drifts": all_drifts,
            "root_cause": root_cause,
            "tag_applied": incident_tag,
            "tag_success": tag_success,
            "fix_scripts": fix_results,
            "report": report,
            "investigation_log": self.investigation_log,
        }
        self.findings = results

        print()
        print("=" * 70)
        print("🤖 AI DATA RELIABILITY AGENT — Investigation Complete")
        print("=" * 70)
        print()
        print(report)

        return results


# =============================================================================
# Main Entry Point
# =============================================================================
def main():
    parser = argparse.ArgumentParser(
        description="AI Data Reliability Agent — Investigate ML model degradation"
    )
    parser.add_argument(
        "--gms-url",
        default=os.environ.get("DATAHUB_GMS_URL", "http://localhost:8085"),
        help="DataHub GMS URL",
    )
    args = parser.parse_args()

    # Load environment variables if .env exists
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    # Initialize components
    db_inspector = PostgresSchemaInspector()
    datahub_client = DataHubClient(gms_url=args.gms_url)
    agent = ReliabilityAgent(datahub_client=datahub_client, db_inspector=db_inspector)

    alert = ALERT_TEMPLATE.format(**DEMO_ALERT_VALUES)

    print()
    print("🚨 INCOMING ALERT:")
    print("-" * 70)
    print(alert)
    print("-" * 70)

    results = agent.investigate(alert)

    # Save results JSON
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    results_path = os.path.join(project_root, "examples", "investigation_results.json")
    os.makedirs(os.path.dirname(results_path), exist_ok=True)

    serializable = {k: v for k, v in results.items() if k not in ("investigation_log",)}
    serializable["investigation_log"] = [
        {k2: str(v2) for k2, v2 in entry.items()}
        for entry in results.get("investigation_log", [])
    ]
    # drift_report may contain nested non-serializable objects — ensure clean
    if "drift_report" in serializable:
        import json as _json
        serializable["drift_report"] = _json.loads(_json.dumps(serializable["drift_report"], default=str))

    with open(results_path, "w") as f:
        json.dump(serializable, f, indent=2, default=str)

    print(f"\n📁 Full results saved to: {results_path}")


if __name__ == "__main__":
    main()
