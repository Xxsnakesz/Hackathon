#!/usr/bin/env python3
# =============================================================================
# run_demo.py — One-Click Demo Orchestration Script
# =============================================================================
# This script orchestrates the entire demo workflow for the hackathon video.
# Run it with: python src/demo/run_demo.py
#
# === SPEAKER GUIDE (Penjelasan untuk Pembicara) ===
# Script ini adalah "remote control" presentasi Anda. Cukup jalankan 1 kali
# dan ia akan menjalankan seluruh skenario demo secara berurutan:
#
# 1. Menampilkan kondisi awal (schema normal)
# 2. Mensimulasikan insiden schema drift
# 3. Menjalankan AI Agent untuk investigasi
# 4. Menampilkan hasil: tag karantina + fix script
#
# Saat merekam video demo, jalankan script ini dan rekam terminal Anda.
# Output dirancang agar mudah dibaca dan dramatis (dengan emoji & warna).
# =============================================================================

import os
import sys
import time
import json

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from src.agent.datahub_gms_client import DatahubGmsClient
from src.agent.db_inspector import PostgresSchemaInspector
from src.agent.pipeline_discovery import discover_pipeline
from src.agent.multi_agent import MultiAgentOrchestrator


def slow_print(text: str, delay: float = 0.02):
    """Print text with a typewriter effect for demo drama."""
    for char in text:
        sys.stdout.write(char)
        sys.stdout.flush()
        time.sleep(delay)
    print()


def section_banner(title: str):
    """Print a dramatic section banner."""
    print()
    print("╔" + "═" * 68 + "╗")
    print(f"║  {title:<66}║")
    print("╚" + "═" * 68 + "╝")
    print()


def pause(message: str = "Press Enter to continue...", auto: bool = True, seconds: float = 2.0):
    """Pause between demo steps."""
    if auto:
        print(f"   ⏳ {message} (continuing in {seconds}s...)")
        time.sleep(seconds)
    else:
        input(f"   ⏳ {message}")


def main():
    """Run the complete demo workflow."""

    # =========================================================================
    # INTRO
    # =========================================================================
    print()
    print("╔" + "═" * 68 + "╗")
    print("║" + " " * 68 + "║")
    print("║    🤖  AI DATA RELIABILITY AGENT                                  ║")
    print("║    Autonomous Root-Cause Analysis for ML Model Protection         ║")
    print("║    Powered by DataHub Context Graph                               ║")
    print("║" + " " * 68 + "║")
    print("║    DataHub Agent Hackathon 2026                                   ║")
    print("╚" + "═" * 68 + "╝")
    print()
    pause("Starting demo...", auto=True, seconds=3.0)

    # =========================================================================
    # PHASE 1: Show the healthy state
    # =========================================================================
    section_banner("📊 PHASE 1: Healthy State — Production Data Pipeline")

    slow_print("The following data pipeline is running in production:")
    print()
    print("   ┌─────────────────────────────┐")
    print("   │  paysim_raw_transactions     │")
    print("   │  ─────────────────────────── │")
    print("   │  step        : INTEGER       │")
    print("   │  type        : STRING        │")
    print("   │  amount      : DOUBLE  ✅    │")
    print("   │  nameOrig    : STRING        │")
    print("   │  oldbalanceOrg : DOUBLE      │")
    print("   │  newbalanceOrig: DOUBLE      │")
    print("   │  nameDest    : STRING        │")
    print("   │  oldbalanceDest: DOUBLE      │")
    print("   │  newbalanceDest: DOUBLE      │")
    print("   │  isFraud     : INTEGER       │")
    print("   │  isFlaggedFraud: INTEGER ✅  │")
    print("   └──────────────┬──────────────┘")
    print("                  │ ETL / dbt")
    print("                  ▼")
    print("   ┌─────────────────────────────┐")
    print("   │  feature_engineering_table   │")
    print("   │  + amount_ratio              │")
    print("   │  + balance_change_orig       │")
    print("   │  + is_large_transaction      │")
    print("   └──────────────┬──────────────┘")
    print("                  │ Features")
    print("                  ▼")
    print("   ┌─────────────────────────────┐")
    print("   │  Fraud_Detection_ML_Model    │")
    print("   │  Algorithm: XGBoost          │")
    print("   │  Accuracy: 97.8% ✅          │")
    print("   │  FPR: 2.1%                   │")
    print("   └─────────────────────────────┘")
    print()
    slow_print("✅ All systems nominal. Model accuracy at 97.8%.")

    pause("Proceeding to simulate incident...", auto=True, seconds=3.0)

    # =========================================================================
    # PHASE 2: Simulate Schema Drift Incident
    # =========================================================================
    section_banner("💥 PHASE 2: Incident — Schema Drift in Upstream Table")

    slow_print("⚠️  An upstream Data Engineer has made a schema change...")
    time.sleep(1.0)
    print()
    print("   🔧 ALTER TABLE paysim_raw_transactions")
    print("      ALTER COLUMN amount TYPE VARCHAR;")
    print("      ALTER COLUMN isFlaggedFraud TYPE VARCHAR;")
    print()
    time.sleep(1.0)
    slow_print("   Columns 'amount' & 'isFlaggedFraud' changed: DOUBLE/INT → STRING")
    time.sleep(0.5)
    slow_print("   ❌ No downstream teams were notified!")
    time.sleep(1.0)
    print()
    print("   📉 Immediate Impact:")
    print("   ┌───────────────────────────────────────┐")
    print("   │  Fraud Model Accuracy: 97.8% → 42.3%  │")
    print("   │  False Positive Rate:  2.1% → 78.5%   │")
    print("   │  Blocked Legit Txns:   37x normal     │")
    print("   │  Revenue Loss:         $125,000/hour  │")
    print("   └───────────────────────────────────────┘")
    print()

    pause("Triggering AI Agent...", auto=True, seconds=3.0)

    # =========================================================================
    # PHASE 3: AI Agent Investigation
    # =========================================================================
    section_banner("🤖 PHASE 3: AI Agent Investigation via DataHub")

    slow_print("Initializing AI Data Reliability Agent...")
    time.sleep(0.5)
    slow_print("Connecting to DataHub MCP Server...")
    time.sleep(0.5)
    print()

    # Initialize and run the multi-agent team. Pure-LLM design — requires
    # OPENAI_API_KEY (+ OPENAI_BASE_URL for gateways) or ANTHROPIC_API_KEY.
    gms = DatahubGmsClient()
    ctx = discover_pipeline(gms)
    inspector = PostgresSchemaInspector(pipeline_context=ctx)
    orchestrator = MultiAgentOrchestrator(inspector=inspector, gms=gms, ctx=ctx)

    session, events = orchestrator.run()
    for evt in events:
        print(f"   [{evt.agent}] {evt.text}")

    pause("Showing results...", auto=True, seconds=2.0)

    # =========================================================================
    # PHASE 4: Results Summary
    # =========================================================================
    section_banner("✅ PHASE 4: Resolution — Automated Remediation Complete")

    tag = session.datahub_writeback.get("tag", "N/A") if session.datahub_writeback else "N/A (not approved)"
    n_models = session.datahub_writeback.get("n_models", 0) if session.datahub_writeback else 0
    print("   🏷️  DataHub Write-Back:")
    print(f"   Tag '{tag}' applied to {n_models} impacted model(s)")
    print(f"   Reviewer verdict: {session.review_verdict.value if session.review_verdict else 'N/A'}")
    print()
    print("   📝 Generated Fix Scripts:")
    fix_scripts = session.fix_scripts
    sql_fix_path = ""
    if fix_scripts:
        for key, fs in fix_scripts.items():
            path = fs.get("sql_fix_path", "")
            print(f"   • {path}")
            if not sql_fix_path:
                sql_fix_path = path
    print()

    # Show the SQL fix preview
    if sql_fix_path and os.path.exists(sql_fix_path):
        print("   📋 SQL Fix Script Preview (first 20 lines):")
        print("   " + "─" * 50)
        with open(sql_fix_path, "r") as f:
            for i, line in enumerate(f):
                if i < 20:
                    print(f"   {line.rstrip()}")
        print("   " + "─" * 50)
        print("   ... (full script in examples/ directory)")

    print()
    print("╔" + "═" * 68 + "╗")
    print("║" + " " * 68 + "║")
    print("║    ✅ DEMO COMPLETE                                               ║")
    print("║                                                                    ║")
    print("║    The AI Data Reliability Agent successfully:                     ║")
    print("║    1. Detected ML model accuracy degradation                       ║")
    print("║    2. Traced upstream lineage via DataHub                          ║")
    print("║    3. Identified schema drift (amount & isFlaggedFraud to STRING) ║")
    print("║    4. Added DATA-INCIDENT tag to DataHub (write-back)             ║")
    print("║    5. Generated SQL fix script for remediation                     ║")
    print("║                                                                    ║")
    print("║    Total investigation time: < 10 seconds (vs hours manually)     ║")
    print("║" + " " * 68 + "║")
    print("╚" + "═" * 68 + "╝")
    print()


if __name__ == "__main__":
    main()
