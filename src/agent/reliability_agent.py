# =============================================================================
# reliability_agent.py — AI Data Reliability Agent (v4 — Real DataHub)
# =============================================================================
# Investigation now uses REAL DataHub GMS for:
#   - Lineage traversal (GET /relationships from GMS)
#   - Entity metadata (GET /entities from GMS)
#   - Tag write-back (DatahubRestEmitter SDK emit MCP)
#
# Falls back to in-memory catalog if GMS unreachable.
# Schema drift detection remains fully real (PostgreSQL information_schema).
# =============================================================================

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.agent.prompts import (
    ALERT_TEMPLATE,
    ROOT_CAUSE_REPORT_TEMPLATE,
    DEMO_ALERT_VALUES,
)
from src.agent.fix_generator import save_fix_scripts
from src.agent.db_inspector import (
    PostgresSchemaInspector,
    scan_all_drifts,
)
from src.agent.datahub_gms_client import DatahubGmsClient
from src.agent.pipeline_discovery import (
    PipelineContext,
    discover_pipeline,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ReliabilityAgent")


def _bare_table_name(urn: str) -> str:
    """Strip URN wrapping to get the lowercase bare table name."""
    if "," not in urn:
        return urn.lower()
    middle = urn.split(",")[-2]
    return middle.split(".")[-1].lower()


# =============================================================================
# Backwards-compatible alias so Streamlit app import still works
# =============================================================================
class DataHubClient:
    """
    Thin wrapper kept for backwards compatibility with streamlit_app.py.
    All real DataHub operations now go through DatahubGmsClient.
    """
    def __init__(self, gms_url: str = None):
        self._gms = DatahubGmsClient(gms_url=gms_url)

    @property
    def gms_url(self):
        return self._gms.gms_url

    def is_live(self):
        return self._gms.is_live()

    def search_datahub(self, query: str) -> list:
        return self._gms.search(query)

    def get_entity(self, urn: str) -> dict:
        return self._gms.get_entity(urn)

    def get_lineage(self, urn: str, direction: str = "UPSTREAM") -> list:
        return self._gms.get_lineage(urn, direction)

    def update_metadata(self, urn: str, tag: str, description: str = "") -> bool:
        ok = self._gms.add_tag(urn, tag)
        if description:
            self._gms.emit_incident(urn, f"[{tag}]", description)
        return ok

    def get_all_tags(self, urn: str) -> list:
        return self._gms.get_all_tags(urn)


# =============================================================================
# AI Data Reliability Agent
# =============================================================================
class ReliabilityAgent:
    """
    7-step investigation agent using REAL DataHub GMS + real PostgreSQL.

    Data sources:
      - Schema drift    → PostgreSQL information_schema vs schema_baseline table
      - Schema history  → schema_change_log table (PostgreSQL audit trail)
      - Lineage         → DataHub GMS REST API (falls back to in-memory)
      - Tags write-back → DatahubRestEmitter SDK MCP (falls back to REST/in-memory)
      - Impact metrics  → COUNT queries on actual transaction data
    """

    def __init__(
        self,
        datahub_client: DataHubClient = None,
        db_inspector: PostgresSchemaInspector = None,
        gms_url: str = None,
        pipeline_context: PipelineContext = None,
        auto_discover: bool = True,
    ):
        if datahub_client is None:
            datahub_client = DataHubClient(gms_url=gms_url)

        self._gms: DatahubGmsClient = datahub_client._gms
        self.datahub = datahub_client

        # Discover pipeline from DataHub if not provided (context-based mode).
        if pipeline_context is None and auto_discover:
            try:
                pipeline_context = discover_pipeline(self._gms)
            except RuntimeError as exc:
                logger.error(f"Pipeline discovery failed: {exc}")
                raise

        self.pipeline_context = pipeline_context

        if db_inspector is None:
            db_inspector = PostgresSchemaInspector(pipeline_context=pipeline_context)
        else:
            # ensure the inspector knows about the same context
            db_inspector.pipeline_context = pipeline_context

        self.db = db_inspector
        self.investigation_log: list = []
        self.findings: dict = {}

    def _log(self, step: str, detail: str):
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "step": step,
            "detail": detail,
        }
        self.investigation_log.append(entry)
        logger.info(f"📝 [{step}] {detail[:220]}")

    # ─────────────────────────────────────────────────────────────────────
    # Core Investigation
    # ─────────────────────────────────────────────────────────────────────

    def investigate(self, alert_message: str) -> dict:
        """Full 7-step investigation. Returns structured result dict."""
        self.investigation_log = []
        self.findings = {}

        db_mode = self.db.mode().upper()
        gms_mode = "LIVE" if self._gms.is_live() else "FALLBACK"

        print()
        print("=" * 70)
        print(f"🤖 AI DATA RELIABILITY AGENT — Investigation Started")
        print(f"   PostgreSQL: {db_mode} | DataHub GMS: {gms_mode}")
        print("=" * 70)

        # ── STEP 1: Real DB Schema Scan ───────────────────────────────────
        self._log("STEP 1", f"Scanning monitored tables vs schema_baseline [{db_mode}]...")
        drift_report = scan_all_drifts(self.db)
        self._log(
            "STEP 1: Scan Complete",
            f"Scanned {len(drift_report['tables'])} tables. "
            f"Drifts: {drift_report['total_drifts']} total, "
            f"{drift_report['critical_drifts']} CRITICAL. "
            f"[Source: PostgreSQL information_schema]",
        )

        if not drift_report["any_drift"]:
            self._log("STEP 1: Result", "✅ No schema drift. All tables match baseline.")
            return {
                "status": "no_drift",
                "message": "All monitored tables match their baseline schema.",
                "drift_report": drift_report,
                "datahub_gms_mode": gms_mode,
                "db_mode": db_mode,
                "investigation_log": self.investigation_log,
            }

        # ── STEP 2: Real Audit Trail from schema_change_log ───────────────
        self._log("STEP 2", "Querying schema_change_log for real audit trail...")
        change_log = self.db.get_schema_change_log(limit=20)
        active_log = [
            e for e in change_log
            if not e.get("is_reverted", False)
            and e.get("column_name", "") != "__schema_init__"
            and e.get("severity", "INFO") != "INFO"
        ]
        if active_log:
            summary = "\n".join(
                f"  [{e.get('changed_at', '?')}] "
                f"{e.get('table_name')}.{e.get('column_name')}: "
                f"{e.get('old_type')} → {e.get('new_type')} "
                f"by {e.get('changed_by')} — {e.get('change_reason', 'N/A')}"
                for e in active_log[:5]
            )
            self._log("STEP 2: Audit Trail",
                      f"Found {len(active_log)} active change(s) [schema_change_log]:\n{summary}")
        else:
            self._log("STEP 2: Audit Trail",
                      "No entries in schema_change_log (drift may be external).")

        # ── STEP 3: Real DataHub Lineage Traversal (multi-model) ──────────
        self._log(
            "STEP 3",
            f"Traversing lineage graph via DataHub GMS [{gms_mode}] "
            f"for {len(self.pipeline_context.ml_models)} discovered model(s)... "
            f"URL: {self._gms.gms_url}",
        )

        lineage_lines: list[str] = []
        for model_spec in self.pipeline_context.ml_models:
            lineage_lines.append(f"◆ {model_spec.name} [{model_spec.platform}]")
            upstream_l1 = self._gms.get_lineage(model_spec.urn, "UPSTREAM")
            for l1 in upstream_l1:
                lineage_lines.append(f"  ├─ {l1['name']} ({l1['platform']})")
                upstream_l2 = self._gms.get_lineage(l1["urn"], "UPSTREAM")
                for l2 in upstream_l2:
                    lineage_lines.append(f"  │    └─ {l2['name']} ({l2['platform']})")
        lineage_trace = "\n".join(lineage_lines) or "  (no lineage discovered)"
        self._log(
            "STEP 3: Lineage",
            f"Discovered pipeline graph:\n{lineage_trace}",
        )

        # ── STEP 4: Collect & Enrich Drift Events ────────────────────────
        self._log("STEP 4", "Collecting drift events and enriching with audit log data...")
        all_drifts = []
        for table_name, info in drift_report["tables"].items():
            for d in info.get("drifts", []):
                log_match = next(
                    (e for e in active_log
                     if e.get("table_name") == table_name
                     and e.get("column_name", "").lower() == d["column"].lower()),
                    None,
                )
                d["changed_by"]    = log_match.get("changed_by", "unknown") if log_match else "unknown"
                d["change_reason"] = log_match.get("change_reason", "N/A") if log_match else "N/A"
                d["log_timestamp"] = log_match.get("changed_at", d["detected_at"]) if log_match else d["detected_at"]
                all_drifts.append(d)

                who = f" | by: {d['changed_by']}" if d["changed_by"] != "unknown" else ""
                self._log(
                    f"⚠️  [{d['severity']}] {table_name}.{d['column']}",
                    f"{d['drift_type']}: {d['baseline_type']} → {d['actual_type']}{who}",
                )

        affected_tables  = list({d["table"] for d in all_drifts})
        affected_columns = [d["column"] for d in all_drifts]
        critical_drifts  = [d for d in all_drifts if d.get("severity") == "CRITICAL"]

        # ── STEP 5: Real Impact Metrics from Transaction Data ─────────────
        self._log("STEP 5", "Computing impact metrics from actual transaction data...")
        impact_metrics = self.db.get_impact_metrics()
        table_stats    = self.db.get_table_stats()
        impact_summary = self._build_impact_summary(all_drifts, impact_metrics, table_stats)
        self._log("STEP 5: Impact", impact_summary)

        # ── STEP 6: Root Cause Analysis ───────────────────────────────────
        self._log("STEP 6", "Building root cause analysis...")
        drift_lines = []
        for i, d in enumerate(all_drifts, 1):
            who_str = f" (by: {d['changed_by']}, reason: {d['change_reason']})" if d["changed_by"] != "unknown" else ""
            drift_lines.append(
                f"  {i}. [{d['severity']}] [{d['table']}] `{d['column']}`: "
                f"{d['baseline_type']} → {d['actual_type']} ({d['drift_type']}){who_str}"
            )

        # Determine which discovered ML models are impacted by these drifts
        # (an ML model is impacted iff its upstream table set intersects affected_tables)
        impacted_models = []
        for m in self.pipeline_context.ml_models:
            model_upstream_names = {
                _bare_table_name(u) for u in m.upstream_tables
            }
            if model_upstream_names & set(affected_tables):
                impacted_models.append(m)

        impacted_names = [m.name for m in impacted_models] or ["(no downstream model)"]

        root_cause = (
            f"SCHEMA DRIFT DETECTED in: {', '.join(affected_tables)}\n\n"
            f"Impacted ML model(s): {', '.join(impacted_names)}\n\n"
            f"Affected columns ({len(all_drifts)} drift(s), {len(critical_drifts)} CRITICAL):\n"
            + "\n".join(drift_lines)
            + f"\n\n{impact_summary}\n\n"
            "Root cause: schema change(s) on source table(s) not coordinated with "
            "downstream ML consumers. Column type changes break numerical operations "
            "in downstream feature engineering, causing silent inference failures."
        )
        self._log("STEP 6: Root Cause", root_cause)

        # ── STEP 7: Remediation — Multi-Model DataHub Write-Back ──────────
        self._log(
            "STEP 7",
            f"Executing remediation via DataHub GMS [{gms_mode}]: "
            f"tagging {len(impacted_models)} impacted model(s) + generating fix scripts...",
        )

        incident_tag  = "DATA-INCIDENT: DO NOT USE"
        incident_desc = (
            f"Schema drift detected in: {', '.join(affected_tables)}. "
            f"Drifted columns: {', '.join(affected_columns)}. "
            f"Critical drifts: {len(critical_drifts)}. "
            f"Auto-remediated by AI Data Reliability Agent at "
            f"{datetime.now(timezone.utc).isoformat()}."
        )

        tag_results = {}
        for m in impacted_models:
            ok = self._gms.add_tag(m.urn, incident_tag)
            self._gms.emit_incident(m.urn, f"[INCIDENT] {incident_tag}", incident_desc)
            tag_results[m.urn] = ok

        tag_ok = all(tag_results.values()) if tag_results else False

        # Tag source tables directly using their URN from context (no fuzzy search)
        for tname in affected_tables:
            src = self.pipeline_context.source_tables.get(tname)
            if src:
                self._gms.add_tag(src.urn, "SCHEMA-DRIFT-DETECTED")

        # Generate SQL + dbt fix scripts
        examples_dir = Path(__file__).parent.parent.parent / "examples"
        fix_results = {}
        for d in all_drifts:
            if d["drift_type"] == "TYPE_CHANGE":
                res = save_fix_scripts(
                    table_name=d["table"],
                    column_name=d["column"],
                    wrong_type=d["actual_type"],
                    correct_type=d["baseline_type"],
                    output_dir=str(examples_dir),
                )
                fix_results[f"{d['table']}.{d['column']}"] = res

        self._log(
            "STEP 7: Remediation Complete",
            f"Tag '{incident_tag}' → {len(impacted_models)} model(s) via DataHub GMS [{gms_mode}]: "
            f"{', '.join(impacted_names)}. Generated {len(fix_results)} fix script(s).",
        )

        # ── Final Report ──────────────────────────────────────────────────
        first = all_drifts[0]
        primary_model = impacted_models[0] if impacted_models else None
        model_name = primary_model.name if primary_model else "(no model impacted)"
        model_urn = primary_model.urn if primary_model else "N/A"
        platform = primary_model.platform if primary_model else "N/A"

        report = ROOT_CAUSE_REPORT_TEMPLATE.format(
            alert_summary=alert_message[:300],
            model_name=model_name,
            model_urn=model_urn,
            platform=platform,
            lineage_trace=lineage_trace,
            affected_dataset=", ".join(affected_tables),
            affected_column=", ".join(affected_columns),
            previous_type=first["baseline_type"],
            current_type=first["actual_type"],
            change_timestamp=first.get("log_timestamp", first["detected_at"]),
            root_cause_analysis=root_cause,
            fix_script_path=list(fix_results.values())[0].get("sql_fix_path", "N/A") if fix_results else "N/A",
            recommendations=(
                "1. Apply generated SQL fix scripts from examples/\n"
                "2. Verify schema matches baseline in Schema Monitor tab\n"
                "3. Re-run pipeline registration to re-sync DataHub if source schema changed intentionally\n"
                "4. Retrain/revalidate impacted ML model(s)\n"
                "5. Remove DATA-INCIDENT tag from model(s) in DataHub UI once resolved\n"
                "6. Add schema change alerts/assertions to prevent recurrence"
            ),
        )

        results = {
            "status": "incident_detected",
            "impacted_models": [{"urn": m.urn, "name": m.name, "platform": m.platform} for m in impacted_models],
            "model_name": model_name,
            "model_urn": model_urn,
            "datahub_gms_mode": gms_mode,
            "db_mode": db_mode,
            "drift_report": drift_report,
            "drifts": all_drifts,
            "schema_change_log": active_log,
            "impact_metrics": impact_metrics,
            "table_stats": table_stats,
            "root_cause": root_cause,
            "tag_applied": incident_tag,
            "tag_success": tag_ok,
            "tag_results": tag_results,
            "fix_scripts": fix_results,
            "report": report,
            "investigation_log": self.investigation_log,
        }
        self.findings = results

        print()
        print("=" * 70)
        print("🤖 AI DATA RELIABILITY AGENT — Investigation Complete")
        print(f"   DataHub GMS: {gms_mode} | PostgreSQL: {db_mode}")
        print("=" * 70)
        print()
        print(report)
        return results

    def _build_impact_summary(self, drifts, impact_metrics, table_stats) -> str:
        total_rows   = table_stats.get("total_rows", 0)
        critical     = [d for d in drifts if d.get("severity") == "CRITICAL"]
        fraud_rows   = table_stats.get("fraud_rows", 0)

        if total_rows == 0:
            return (
                f"⚠️  Impact: {len(critical)} CRITICAL column(s) affected. "
                "Transaction table is empty — seed data to compute exact impact."
            )

        lines = [f"Impact on {total_rows:,} transactions ({fraud_rows:,} fraud cases):"]
        for d in drifts:
            col = d["column"].lower()
            cm  = impact_metrics.get("columns", {}).get(col, {})
            if cm and not cm.get("type_ok", True):
                corrupted   = cm.get("corrupted_count", "?")
                corrupt_pct = cm.get("corrupted_pct", "?")
                castable    = cm.get("castable_pct", "?")
                if isinstance(corrupted, int):
                    lines.append(
                        f"  • `{col}` ({d['baseline_type']} → {d['actual_type']}): "
                        f"{corrupted:,} rows affected ({corrupt_pct}% corrupted), "
                        f"{castable}% castable back"
                    )
                else:
                    lines.append(f"  • `{col}`: type mismatch detected")
            else:
                lines.append(
                    f"  • `{col}` ({d['baseline_type']} → {d['actual_type']}): "
                    f"CRITICAL feature — downstream ML computations FAIL with this type"
                )

        return "\n".join(lines)


# =============================================================================
# CLI Entry Point
# =============================================================================
def main():
    import argparse
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    parser = argparse.ArgumentParser(description="AI Data Reliability Agent")
    parser.add_argument("--gms-url", default=os.environ.get("DATAHUB_GMS_URL", "http://localhost:8085"))
    parser.add_argument("--ml-platforms", default=os.environ.get("ML_PLATFORMS"),
                        help="Comma-separated ML platform whitelist (e.g., mlflow,sagemaker)")
    parser.add_argument("--tag-filter", default=os.environ.get("ML_TAG_FILTER"),
                        help="Comma-separated tag filter for ML models (default: production)")
    args = parser.parse_args()

    datahub = DataHubClient(gms_url=args.gms_url)

    # Discover pipeline explicitly so CLI can print the context before scanning.
    ml_platforms = args.ml_platforms.split(",") if args.ml_platforms else None
    tag_filter   = args.tag_filter.split(",")   if args.tag_filter   else None
    try:
        ctx = discover_pipeline(datahub._gms, ml_platforms=ml_platforms, tag_filter=tag_filter)
    except RuntimeError as exc:
        print(f"\n❌ {exc}\n")
        sys.exit(2)

    print()
    print("=" * 70)
    print("🔎 Discovered Pipeline Context")
    print("=" * 70)
    print(ctx.summary())
    print()

    db_inspector = PostgresSchemaInspector(pipeline_context=ctx)
    agent = ReliabilityAgent(
        datahub_client=datahub,
        db_inspector=db_inspector,
        pipeline_context=ctx,
        auto_discover=False,   # already discovered above
    )

    alert   = ALERT_TEMPLATE.format(**DEMO_ALERT_VALUES)
    results = agent.investigate(alert)

    out_path = Path(__file__).parent.parent.parent / "examples" / "investigation_results.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(
            {k: v for k, v in results.items() if k not in ("investigation_log",)},
            f, indent=2, default=str,
        )
    print(f"\n📁 Results saved to: {out_path}")


if __name__ == "__main__":
    main()
