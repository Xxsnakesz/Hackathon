# =============================================================================
# streamlit_app.py — AI Data Reliability Agent — Frontend Dashboard
# =============================================================================
# Run with: streamlit run src/app/streamlit_app.py
# =============================================================================

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import streamlit as st

# ---------------------------------------------------------------------------
# Path setup — allow imports from project root
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load .env if available
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

from src.agent.db_inspector import (
    PostgresSchemaInspector,
    scan_all_drifts,
    reset_sim_state,
    apply_sim_drift,
    MONITORED_TABLES,
    _load_baseline,
)
from src.agent.reliability_agent import DataHubClient, ReliabilityAgent
from src.agent.prompts import ALERT_TEMPLATE, DEMO_ALERT_VALUES
from src.agent.fix_generator import save_fix_scripts

# =============================================================================
# Page config
# =============================================================================
st.set_page_config(
    page_title="AI Data Reliability Agent",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# =============================================================================
# Custom CSS — dark premium glassmorphism theme
# =============================================================================
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

    /* ─── Global ─── */
    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }
    .stApp {
        background: linear-gradient(135deg, #0a0e1a 0%, #0d1528 50%, #0a0e1a 100%);
        min-height: 100vh;
    }

    /* ─── Sidebar ─── */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0d1528 0%, #111827 100%);
        border-right: 1px solid rgba(99,179,237,0.15);
    }
    [data-testid="stSidebar"] * { color: #e2e8f0; }

    /* ─── Main header ─── */
    .hero-banner {
        background: linear-gradient(135deg, rgba(14,165,233,0.15) 0%, rgba(139,92,246,0.15) 100%);
        border: 1px solid rgba(99,179,237,0.25);
        border-radius: 16px;
        padding: 28px 36px;
        margin-bottom: 24px;
        backdrop-filter: blur(12px);
    }
    .hero-title {
        font-size: 2rem; font-weight: 700;
        background: linear-gradient(90deg, #63b3ed, #a78bfa);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        margin: 0 0 6px 0;
    }
    .hero-subtitle { color: #94a3b8; font-size: 0.95rem; margin: 0; }

    /* ─── Metric cards ─── */
    .metric-card {
        background: rgba(255,255,255,0.03);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 12px;
        padding: 20px 24px;
        text-align: center;
        transition: border-color 0.2s, transform 0.2s;
    }
    .metric-card:hover { border-color: rgba(99,179,237,0.4); transform: translateY(-2px); }
    .metric-value { font-size: 2rem; font-weight: 700; color: #63b3ed; margin: 4px 0; }
    .metric-label { font-size: 0.8rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; }

    /* ─── Status pills ─── */
    .pill-ok      { background:#065f46; color:#6ee7b7; border:1px solid #059669; border-radius:20px; padding:3px 12px; font-size:0.82rem; font-weight:600; }
    .pill-drift   { background:#7f1d1d; color:#fca5a5; border:1px solid #dc2626; border-radius:20px; padding:3px 12px; font-size:0.82rem; font-weight:600; }
    .pill-missing { background:#78350f; color:#fcd34d; border:1px solid #d97706; border-radius:20px; padding:3px 12px; font-size:0.82rem; font-weight:600; }
    .pill-sim     { background:#1e3a5f; color:#93c5fd; border:1px solid #3b82f6; border-radius:20px; padding:3px 12px; font-size:0.82rem; font-weight:600; }
    .pill-live    { background:#064e3b; color:#6ee7b7; border:1px solid #059669; border-radius:20px; padding:3px 12px; font-size:0.82rem; font-weight:600; }

    /* ─── Section cards ─── */
    .section-card {
        background: rgba(255,255,255,0.025);
        border: 1px solid rgba(255,255,255,0.07);
        border-radius: 14px;
        padding: 24px;
        margin-bottom: 20px;
    }
    .section-title {
        font-size: 1.05rem; font-weight: 600; color: #e2e8f0;
        margin-bottom: 16px; display: flex; align-items: center; gap: 8px;
    }

    /* ─── Alert boxes ─── */
    .alert-critical {
        background: rgba(127,29,29,0.4);
        border: 1px solid rgba(239,68,68,0.5);
        border-left: 4px solid #ef4444;
        border-radius: 10px; padding: 16px 20px; margin: 12px 0;
        color: #fca5a5;
    }
    .alert-ok {
        background: rgba(6,78,59,0.4);
        border: 1px solid rgba(16,185,129,0.5);
        border-left: 4px solid #10b981;
        border-radius: 10px; padding: 16px 20px; margin: 12px 0;
        color: #6ee7b7;
    }
    .alert-info {
        background: rgba(30,58,138,0.4);
        border: 1px solid rgba(59,130,246,0.5);
        border-left: 4px solid #3b82f6;
        border-radius: 10px; padding: 16px 20px; margin: 12px 0;
        color: #93c5fd;
    }

    /* ─── Schema table ─── */
    .schema-table { width: 100%; border-collapse: collapse; font-size: 0.875rem; }
    .schema-table th {
        background: rgba(255,255,255,0.06); color: #94a3b8;
        padding: 10px 14px; text-align: left; font-weight: 500;
        border-bottom: 1px solid rgba(255,255,255,0.08);
        text-transform: uppercase; letter-spacing: 0.05em; font-size: 0.75rem;
    }
    .schema-table td {
        padding: 9px 14px; color: #cbd5e1;
        border-bottom: 1px solid rgba(255,255,255,0.04);
    }
    .schema-table tr:hover td { background: rgba(255,255,255,0.03); }
    .schema-table .drift-row td { background: rgba(127,29,29,0.25) !important; }
    .col-ok    { color: #6ee7b7; font-family: 'JetBrains Mono', monospace; font-size: 0.82rem; }
    .col-drift { color: #f87171; font-family: 'JetBrains Mono', monospace; font-size: 0.82rem; }
    .col-name  { color: #e2e8f0; font-family: 'JetBrains Mono', monospace; font-weight: 500; }

    /* ─── Log output ─── */
    .log-box {
        background: #0d1117; border: 1px solid rgba(255,255,255,0.08);
        border-radius: 10px; padding: 16px;
        font-family: 'JetBrains Mono', monospace; font-size: 0.78rem;
        color: #a3e635; max-height: 380px; overflow-y: auto;
        white-space: pre-wrap; line-height: 1.6;
    }

    /* ─── Lineage ─── */
    .lineage-box {
        background: #0d1117; border: 1px solid rgba(99,179,237,0.2);
        border-radius: 10px; padding: 20px;
        font-family: 'JetBrains Mono', monospace; font-size: 0.85rem;
        color: #93c5fd; line-height: 2;
    }

    /* ─── Buttons ─── */
    .stButton > button {
        background: linear-gradient(135deg, #0ea5e9, #6366f1) !important;
        color: white !important; border: none !important;
        border-radius: 8px !important; font-weight: 600 !important;
        font-family: 'Inter', sans-serif !important;
        transition: opacity 0.2s, transform 0.1s !important;
    }
    .stButton > button:hover { opacity: 0.88 !important; transform: translateY(-1px) !important; }

    /* ─── Streamlit overrides ─── */
    .stTabs [data-baseweb="tab-list"] { background: rgba(255,255,255,0.03); border-radius: 10px; padding: 4px; }
    .stTabs [data-baseweb="tab"] { color: #94a3b8 !important; border-radius: 7px; }
    .stTabs [aria-selected="true"] { background: rgba(99,179,237,0.18) !important; color: #63b3ed !important; }
    div[data-testid="stMarkdownContainer"] { color: #cbd5e1; }
    .stSelectbox label, .stTextArea label { color: #94a3b8 !important; }
    .stTextArea textarea { background: #0d1117 !important; color: #a3e635 !important; font-family: 'JetBrains Mono', monospace !important; border: 1px solid rgba(255,255,255,0.1) !important; }
    </style>
    """,
    unsafe_allow_html=True,
)


# =============================================================================
# Session state helpers
# =============================================================================
def get_inspector() -> PostgresSchemaInspector:
    """Return a cached PostgresSchemaInspector stored in session state."""
    if "inspector" not in st.session_state:
        st.session_state.inspector = PostgresSchemaInspector()
    return st.session_state.inspector


def get_datahub_client() -> DataHubClient:
    if "datahub" not in st.session_state:
        st.session_state.datahub = DataHubClient()
    return st.session_state.datahub


def refresh_inspector():
    """Force reconnect (drop cached inspector)."""
    if "inspector" in st.session_state:
        del st.session_state.inspector


# =============================================================================
# Helper renderers
# =============================================================================
def _type_badge(col: str, baseline_map: dict, actual_map: dict) -> tuple[str, str, str]:
    """Return (baseline_html, actual_html, status_html) for a column."""
    base = baseline_map.get(col, {}).get("type", "—")
    act  = actual_map.get(col, {}).get("type", "MISSING")
    if act == "MISSING":
        return (
            f'<span class="col-ok">{base}</span>',
            f'<span class="col-drift">MISSING</span>',
            '<span class="pill-missing">⚠ MISSING</span>',
        )
    if act.lower() != base.lower():
        return (
            f'<span class="col-ok">{base}</span>',
            f'<span class="col-drift">{act}</span>',
            '<span class="pill-drift">⚠ DRIFTED</span>',
        )
    return (
        f'<span class="col-ok">{base}</span>',
        f'<span class="col-ok">{act}</span>',
        '<span class="pill-ok">✓ OK</span>',
    )


def render_schema_table(table_name: str, baseline: list, actual: list):
    """Render a pretty HTML schema comparison table."""
    baseline_map = {f["column"].lower(): f for f in baseline}
    actual_map   = {f["column"].lower(): f for f in actual}
    all_cols = list(dict.fromkeys(
        [f["column"].lower() for f in baseline] +
        [f["column"].lower() for f in actual]
    ))

    rows = ""
    for col in all_cols:
        base_html, act_html, status_html = _type_badge(col, baseline_map, actual_map)
        is_drift = "drift-row" if "DRIFTED" in status_html or "MISSING" in status_html else ""
        rows += (
            f'<tr class="{is_drift}">'
            f'<td><span class="col-name">{col}</span></td>'
            f'<td>{base_html}</td>'
            f'<td>{act_html}</td>'
            f'<td>{status_html}</td>'
            f'</tr>'
        )

    html = f"""
    <table class="schema-table">
        <thead><tr>
            <th>Column</th>
            <th>Expected (Baseline)</th>
            <th>Actual (DB / Sim)</th>
            <th>Status</th>
        </tr></thead>
        <tbody>{rows}</tbody>
    </table>
    """
    st.markdown(html, unsafe_allow_html=True)


def render_lineage():
    """Render the data pipeline lineage as ASCII art."""
    lineage = """
┌─────────────────────────────────────┐
│    paysim_raw_transactions          │
│    (PostgreSQL — source of truth)   │
└──────────────┬──────────────────────┘
               │  ETL / View
               ▼
┌─────────────────────────────────────┐
│    feature_engineering_table        │
│    (Derived PostgreSQL View)        │
└──────────────┬──────────────────────┘
               │  ML Features
               ▼
┌─────────────────────────────────────┐
│    Fraud_Detection_ML_Model         │
│    XGBoost v2.1  •  Acc: 97.8%     │
└─────────────────────────────────────┘
    """
    st.markdown(f'<div class="lineage-box">{lineage}</div>', unsafe_allow_html=True)


# =============================================================================
# Sidebar
# =============================================================================
with st.sidebar:
    st.markdown("### 🤖 AI Data Reliability Agent")
    st.markdown("---")

    inspector = get_inspector()
    mode = inspector.mode()

    if mode == "live":
        st.markdown('<span class="pill-live">🟢 Live PostgreSQL</span>', unsafe_allow_html=True)
        st.caption(f"Connected to `{inspector.host}:{inspector.port}/{inspector.database}`")
    else:
        st.markdown('<span class="pill-sim">🔵 Simulation Mode</span>', unsafe_allow_html=True)
        st.caption("PostgreSQL not reachable — using in-memory simulation.")

    st.markdown("---")

    if st.button("🔄 Reconnect DB"):
        refresh_inspector()
        st.rerun()

    st.markdown("---")
    st.markdown("**DataHub Agent Hackathon 2026**")
    st.caption("Category: Production ML Agents")
    st.markdown("---")
    st.markdown("**Tech Stack**")
    st.caption("Python • PostgreSQL • Streamlit")
    st.caption("DataHub • psycopg2 • XGBoost")


# =============================================================================
# Hero banner
# =============================================================================
st.markdown(
    """
    <div class="hero-banner">
        <p class="hero-title">🤖 AI Data Reliability Agent</p>
        <p class="hero-subtitle">
            Autonomous schema drift detection for FinTech ML pipelines •
            DataHub Agent Hackathon 2026
        </p>
    </div>
    """,
    unsafe_allow_html=True,
)

# =============================================================================
# Live metrics row
# =============================================================================
inspector = get_inspector()
drift_report = scan_all_drifts(inspector)
total_tables = len(drift_report["tables"])
total_drifts = drift_report["total_drifts"]
ok_tables = sum(1 for t in drift_report["tables"].values() if t.get("status") == "OK")

c1, c2, c3, c4 = st.columns(4)
with c1:
    st.markdown(
        f'<div class="metric-card"><div class="metric-label">Tables Monitored</div>'
        f'<div class="metric-value">{total_tables}</div></div>',
        unsafe_allow_html=True,
    )
with c2:
    color = "#6ee7b7" if total_drifts == 0 else "#f87171"
    st.markdown(
        f'<div class="metric-card"><div class="metric-label">Drifts Detected</div>'
        f'<div class="metric-value" style="color:{color}">{total_drifts}</div></div>',
        unsafe_allow_html=True,
    )
with c3:
    st.markdown(
        f'<div class="metric-card"><div class="metric-label">Tables Healthy</div>'
        f'<div class="metric-value" style="color:#6ee7b7">{ok_tables}</div></div>',
        unsafe_allow_html=True,
    )
with c4:
    mode_label = "🟢 Live DB" if inspector.is_live() else "🔵 Simulation"
    st.markdown(
        f'<div class="metric-card"><div class="metric-label">Agent Mode</div>'
        f'<div class="metric-value" style="font-size:1.1rem">{mode_label}</div></div>',
        unsafe_allow_html=True,
    )

st.markdown("<br>", unsafe_allow_html=True)

# =============================================================================
# Main tabs
# =============================================================================
tab_monitor, tab_investigate, tab_simulate, tab_lineage = st.tabs([
    "📊 Schema Monitor",
    "🔍 Run Investigation",
    "💥 Simulate Incident",
    "🗺️ Lineage & Info",
])


# ─────────────────────────────────────────────────────────────────────────────
# TAB 1: Schema Monitor
# ─────────────────────────────────────────────────────────────────────────────
with tab_monitor:
    st.markdown("### 📊 Real-Time Schema Monitor")
    st.caption(
        f"Comparing DB schema against `schema_baseline.json` • "
        f"Last scan: {drift_report['scanned_at']}"
    )

    # Overall status banner
    if drift_report["any_drift"]:
        st.markdown(
            f'<div class="alert-critical">⚠️ <strong>SCHEMA DRIFT DETECTED</strong> — '
            f'{total_drifts} column(s) differ from baseline. '
            f'ML model may be producing incorrect predictions.</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="alert-ok">✅ <strong>All schemas match baseline.</strong> '
            'Pipeline is healthy.</div>',
            unsafe_allow_html=True,
        )

    col_refresh = st.columns([1, 5])
    with col_refresh[0]:
        if st.button("🔄 Refresh Scan"):
            st.rerun()

    st.markdown("---")

    # Per-table breakdown
    for table_name, info in drift_report["tables"].items():
        status = info.get("status", "UNKNOWN")
        emoji = "✅" if status == "OK" else ("⚠️" if status == "DRIFTED" else "❓")
        n_drifts = len(info.get("drifts", []))
        label = f"{emoji} `{table_name}` — {n_drifts} drift(s)" if n_drifts else f"{emoji} `{table_name}` — Clean"

        with st.expander(label, expanded=(status == "DRIFTED")):
            baseline = info.get("baseline", [])
            actual   = info.get("actual", [])

            if not actual:
                st.warning("Table not found in DB / simulation state.")
            else:
                render_schema_table(table_name, baseline, actual)

            if info.get("drifts"):
                st.markdown("<br>**Drift Details:**", unsafe_allow_html=True)
                for d in info["drifts"]:
                    st.markdown(
                        f'<div class="alert-critical">'
                        f'<strong>{d["drift_type"]}</strong> — '
                        f'Column <code>{d["column"]}</code>: '
                        f'expected <code>{d["baseline_type"]}</code>, '
                        f'got <code>{d["actual_type"]}</code>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )


# ─────────────────────────────────────────────────────────────────────────────
# TAB 2: Run Investigation
# ─────────────────────────────────────────────────────────────────────────────
with tab_investigate:
    st.markdown("### 🔍 Run AI Investigation")
    st.caption("The agent scans the actual DB schema, traces lineage, and generates fix scripts.")

    default_alert = ALERT_TEMPLATE.format(**DEMO_ALERT_VALUES)
    alert_msg = st.text_area(
        "Alert Message",
        value=default_alert,
        height=180,
        key="alert_input",
    )

    run_btn = st.button("🔍 Run Investigation", key="run_investigation")

    if run_btn:
        with st.spinner("🤖 Agent is investigating..."):
            # Re-scan from fresh inspector
            fresh_inspector = PostgresSchemaInspector()
            datahub = get_datahub_client()
            agent = ReliabilityAgent(datahub_client=datahub, db_inspector=fresh_inspector)

            progress = st.progress(0, text="Step 1: Scanning DB schema...")
            time.sleep(0.4)
            progress.progress(20, text="Step 2: Comparing against baseline...")
            time.sleep(0.4)
            progress.progress(40, text="Step 3: Tracing DataHub lineage...")
            time.sleep(0.4)
            progress.progress(60, text="Step 4: Root cause analysis...")
            results = agent.investigate(alert_msg)
            progress.progress(80, text="Step 5: Generating fix scripts...")
            time.sleep(0.3)
            progress.progress(100, text="✅ Investigation complete!")
            time.sleep(0.4)
            progress.empty()

        st.session_state["last_results"] = results

    # Display results if available
    if "last_results" in st.session_state:
        results = st.session_state["last_results"]
        status = results.get("status", "")

        if status == "no_drift":
            st.markdown(
                '<div class="alert-ok">✅ <strong>No drift detected.</strong> '
                'All schemas match baseline — ML model should be healthy.</div>',
                unsafe_allow_html=True,
            )
        else:
            drifts = results.get("drifts", [])
            st.markdown(
                f'<div class="alert-critical">🚨 <strong>Incident Detected!</strong> '
                f'{len(drifts)} schema drift(s) found.</div>',
                unsafe_allow_html=True,
            )

            # Investigation log
            with st.expander("📜 Investigation Log", expanded=False):
                log_entries = results.get("investigation_log", [])
                log_text = "\n".join(
                    f"[{e['timestamp']}] [{e['step']}]\n  {e['detail']}\n"
                    for e in log_entries
                )
                st.markdown(f'<div class="log-box">{log_text}</div>', unsafe_allow_html=True)

            # Root cause
            with st.expander("🔎 Root Cause Analysis", expanded=True):
                st.markdown(f"```\n{results.get('root_cause', '')}\n```")

            # Drift table
            with st.expander("⚠️ Drift Details", expanded=True):
                cols_h = ["Table", "Column", "Baseline Type", "Actual Type", "Drift Type", "Detected At"]
                rows_d = [
                    [d["table"], d["column"], d["baseline_type"], d["actual_type"], d["drift_type"], d["detected_at"]]
                    for d in drifts
                ]
                import pandas as pd
                df = pd.DataFrame(rows_d, columns=cols_h)
                st.dataframe(df, use_container_width=True, hide_index=True)

            # Fix scripts
            fix_scripts = results.get("fix_scripts", {})
            if fix_scripts:
                with st.expander("🔧 Generated Fix Scripts", expanded=True):
                    for key, fs in fix_scripts.items():
                        st.markdown(f"**{key}**")
                        sql_path = fs.get("sql_fix_path", "")
                        if sql_path and os.path.exists(sql_path):
                            with open(sql_path) as f:
                                sql_content = f.read()
                            st.code(sql_content, language="sql")
                            st.download_button(
                                label=f"⬇️ Download {os.path.basename(sql_path)}",
                                data=sql_content,
                                file_name=os.path.basename(sql_path),
                                mime="text/plain",
                                key=f"dl_{key}",
                            )

            # DataHub tags
            tag = results.get("tag_applied", "")
            if tag:
                st.markdown(
                    f'<div class="alert-info">🏷️ <strong>Tag applied to DataHub catalog:</strong> '
                    f'<code>{tag}</code> → Fraud_Detection_ML_Model</div>',
                    unsafe_allow_html=True,
                )

            # Full report
            with st.expander("📄 Full Investigation Report"):
                st.markdown(results.get("report", ""))


# ─────────────────────────────────────────────────────────────────────────────
# TAB 3: Simulate Incident
# ─────────────────────────────────────────────────────────────────────────────
with tab_simulate:
    st.markdown("### 💥 Simulate Schema Drift Incident")
    inspector = get_inspector()
    mode_label = "Live PostgreSQL" if inspector.is_live() else "Simulation State"

    st.markdown(
        f'<div class="alert-info">ℹ️ Mode: <strong>{mode_label}</strong> — '
        f'Changes will be applied to {"the real PostgreSQL database" if inspector.is_live() else "the in-memory simulation state"}.'
        f'</div>',
        unsafe_allow_html=True,
    )

    st.markdown("---")

    col_l, col_r = st.columns(2)

    with col_l:
        st.markdown("#### 💣 Apply Schema Drift")
        st.caption("Simulate an engineer accidentally changing a column type.")

        drift_table = st.selectbox("Table", MONITORED_TABLES, key="drift_table")

        baseline_all = _load_baseline()
        table_cols = [f["column"] for f in baseline_all.get(drift_table, [])]
        drift_col = st.selectbox("Column to drift", table_cols, key="drift_col")

        # Suggest bad types depending on current baseline type
        baseline_type = next(
            (f["type"] for f in baseline_all.get(drift_table, []) if f["column"] == drift_col),
            "unknown"
        )
        # Suggest types that would BREAK the pipeline
        bad_type_options = {
            "double precision": ["character varying", "text", "varchar(255)"],
            "integer": ["character varying", "text", "boolean"],
            "character varying": ["integer", "double precision"],
        }
        suggested_bad = bad_type_options.get(baseline_type.lower(), ["character varying", "text"])
        drift_new_type = st.selectbox("New (wrong) type", suggested_bad, key="drift_new_type")
        st.caption(f"Current baseline type: `{baseline_type}`")

        if st.button("💥 Apply Drift", key="apply_drift"):
            with st.spinner(f"Applying drift to {drift_table}.{drift_col}..."):
                success = inspector.apply_drift_to_db(drift_table, drift_col, drift_new_type)
            if success:
                st.success(f"✅ Column `{drift_col}` in `{drift_table}` changed to `{drift_new_type}`")
                st.info("👉 Go to **Schema Monitor** tab to see the drift detected, then **Run Investigation**!")
                time.sleep(0.5)
                st.rerun()
            else:
                st.error(f"❌ Failed to apply drift. Check logs.")

    with col_r:
        st.markdown("#### 🔄 Reset to Baseline")
        st.caption("Restore all columns back to their original (healthy) types.")

        if st.button("🔄 Reset All to Baseline", key="reset_baseline"):
            if not inspector.is_live():
                reset_sim_state()
                st.success("✅ Simulation state reset to baseline (all columns healthy).")
            else:
                # For live DB: restore each drifted column
                current_report = scan_all_drifts(inspector)
                restored = 0
                failed = 0
                for table_name, info in current_report["tables"].items():
                    for drift in info.get("drifts", []):
                        if drift["drift_type"] == "TYPE_CHANGE":
                            ok = inspector.restore_column_to_baseline(table_name, drift["column"])
                            if ok:
                                restored += 1
                            else:
                                failed += 1
                if restored > 0:
                    st.success(f"✅ Restored {restored} column(s) to baseline.")
                if failed > 0:
                    st.error(f"❌ {failed} column(s) could not be restored. Check logs.")
                if restored == 0 and failed == 0:
                    st.info("ℹ️ No drifted columns found — schema is already at baseline.")
            time.sleep(0.5)
            st.rerun()

        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("#### 📋 Current Drift Status")
        current_report = scan_all_drifts(inspector)
        for tname, tinfo in current_report["tables"].items():
            s = tinfo.get("status", "?")
            icon = "✅" if s == "OK" else "⚠️"
            dcount = len(tinfo.get("drifts", []))
            st.markdown(f"{icon} `{tname}` — {dcount} drift(s)" if dcount else f"{icon} `{tname}` — Clean")


# ─────────────────────────────────────────────────────────────────────────────
# TAB 4: Lineage & Info
# ─────────────────────────────────────────────────────────────────────────────
with tab_lineage:
    st.markdown("### 🗺️ Data Pipeline Lineage")
    st.caption("How data flows from raw transactions to the fraud detection model.")

    render_lineage()

    st.markdown("<br>", unsafe_allow_html=True)
    col_a, col_b = st.columns(2)

    with col_a:
        st.markdown("#### 📌 Monitored Entities")
        entities = [
            ("paysim_raw_transactions", "PostgreSQL", "Raw Data", "Source of schema drift"),
            ("feature_engineering_table", "PostgreSQL View", "ML Features", "Downstream of raw table"),
            ("Fraud_Detection_ML_Model", "MLflow / XGBoost", "ML Model", "Final consumer — accuracy 97.8%"),
        ]
        for name, platform, role, note in entities:
            st.markdown(
                f"""
                <div class="section-card" style="margin-bottom:10px;padding:14px 18px">
                    <div style="font-weight:600;color:#e2e8f0;font-size:0.9rem">🗄 {name}</div>
                    <div style="color:#94a3b8;font-size:0.8rem">Platform: {platform} • {role}</div>
                    <div style="color:#64748b;font-size:0.78rem">{note}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    with col_b:
        st.markdown("#### ⚙️ Agent Capabilities")
        caps = [
            ("🔍 Real DB Schema Scan", "Queries `information_schema.columns` directly"),
            ("📊 Drift Detection", "Compares actual vs baseline type per column"),
            ("🗺️ Lineage Traversal", "Follows DataHub lineage graph upstream"),
            ("🏷️ Tag Write-Back", "Tags ML model with DATA-INCIDENT in DataHub catalog"),
            ("🔧 Fix Generation", "Auto-generates SQL ALTER TABLE + dbt CAST patches"),
            ("💥 Incident Simulation", "ALTER TABLE or in-memory state mutation"),
            ("🔄 Auto-Restore", "Restores columns to baseline with one click"),
        ]
        for cap, desc in caps:
            st.markdown(
                f"""
                <div style="padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.05)">
                    <span style="color:#63b3ed;font-weight:600">{cap}</span>
                    <span style="color:#64748b;font-size:0.82rem;margin-left:8px">{desc}</span>
                </div>
                """,
                unsafe_allow_html=True,
            )

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("#### 🏆 DataHub Integration")
    datahub_features = {
        "Lineage Graph": "Agent traverses upstream lineage from ML model to source tables",
        "MCP Server": "DataHub skills: search, get_lineage, get_entity, update_metadata",
        "Metadata Write-Back": "Agent adds DATA-INCIDENT tags and incident descriptions",
        "Schema History": "Baseline JSON acts as schema version v1; DB state is current version",
        "Dataset Registration": "Full schema registration via lineage_bootstrap.py",
        "ML Model Entity": "ML model registered with features, accuracy, training metadata",
    }
    import pandas as pd
    df_dh = pd.DataFrame(
        [(k, v) for k, v in datahub_features.items()],
        columns=["DataHub Feature", "How We Use It"],
    )
    st.dataframe(df_dh, use_container_width=True, hide_index=True)
