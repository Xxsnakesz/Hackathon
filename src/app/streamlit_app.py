# =============================================================================
# streamlit_app.py — AI Data Reliability Agent — Premium Dashboard (v2)
# =============================================================================
# Run with: streamlit run src/app/streamlit_app.py
# =============================================================================

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

from src.agent.db_inspector import (
    PostgresSchemaInspector,
    scan_all_drifts,
    reset_sim_state,
    MONITORED_TABLES,
    DRIFT_SCENARIOS,
    _load_baseline_json,
)
from src.agent.reliability_agent import DataHubClient, ReliabilityAgent
from src.agent.prompts import ALERT_TEMPLATE, DEMO_ALERT_VALUES
from src.agent.data_seeder import seed_transactions, check_data_loaded
from src.agent.pipeline_discovery import discover_pipeline, PipelineContext

# =============================================================================
# Page config
# =============================================================================
st.set_page_config(
    page_title="AI Data Reliability Agent — FinTech Fraud Detection",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# =============================================================================
# CSS — Dark Premium Glassmorphism
# =============================================================================
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap');

html, body, [class*="css"] { font-family:'Inter',sans-serif; }

.stApp {
    background: linear-gradient(135deg,#060d1a 0%,#0a1628 55%,#060d1a 100%);
    min-height:100vh;
}

/* ── Sidebar ── */
[data-testid="stSidebar"] {
    background:linear-gradient(180deg,#080f1e 0%,#0d1830 100%);
    border-right:1px solid rgba(96,165,250,.12);
}
[data-testid="stSidebar"] * { color:#e2e8f0; }

/* ── Hero ── */
.hero {
    background:linear-gradient(135deg,rgba(14,165,233,.12) 0%,rgba(139,92,246,.12) 100%);
    border:1px solid rgba(96,165,250,.2);
    border-radius:18px; padding:28px 36px; margin-bottom:24px;
    backdrop-filter:blur(14px);
}
.hero-title {
    font-size:2.1rem; font-weight:800;
    background:linear-gradient(90deg,#60a5fa,#a78bfa,#34d399);
    -webkit-background-clip:text; -webkit-text-fill-color:transparent;
    margin:0 0 6px;
}
.hero-sub { color:#94a3b8; font-size:.95rem; margin:0; }

/* ── Metric cards ── */
.mcard {
    background:rgba(255,255,255,.03);
    border:1px solid rgba(255,255,255,.07);
    border-radius:14px; padding:18px 22px; text-align:center;
    transition:border-color .2s,transform .2s;
}
.mcard:hover { border-color:rgba(96,165,250,.35); transform:translateY(-2px); }
.mval { font-size:2rem; font-weight:700; color:#60a5fa; margin:4px 0; }
.mlbl { font-size:.75rem; color:#94a3b8; text-transform:uppercase; letter-spacing:.06em; }

/* ── Alerts ── */
.alert-crit {
    background:rgba(127,29,29,.4); border:1px solid rgba(239,68,68,.45);
    border-left:4px solid #ef4444; border-radius:10px;
    padding:14px 18px; margin:10px 0; color:#fca5a5;
}
.alert-ok {
    background:rgba(6,78,59,.4); border:1px solid rgba(16,185,129,.45);
    border-left:4px solid #10b981; border-radius:10px;
    padding:14px 18px; margin:10px 0; color:#6ee7b7;
}
.alert-info {
    background:rgba(30,58,138,.4); border:1px solid rgba(59,130,246,.45);
    border-left:4px solid #3b82f6; border-radius:10px;
    padding:14px 18px; margin:10px 0; color:#93c5fd;
}
.alert-warn {
    background:rgba(120,53,15,.4); border:1px solid rgba(245,158,11,.45);
    border-left:4px solid #f59e0b; border-radius:10px;
    padding:14px 18px; margin:10px 0; color:#fde68a;
}

/* ── Pills ── */
.pill-ok      { background:#065f46; color:#6ee7b7; border:1px solid #059669; border-radius:20px; padding:3px 12px; font-size:.8rem; font-weight:600; }
.pill-drift   { background:#7f1d1d; color:#fca5a5; border:1px solid #dc2626; border-radius:20px; padding:3px 12px; font-size:.8rem; font-weight:600; }
.pill-crit    { background:#4c0519; color:#fda4af; border:1px solid #e11d48; border-radius:20px; padding:3px 12px; font-size:.8rem; font-weight:600; }
.pill-missing { background:#78350f; color:#fcd34d; border:1px solid #d97706; border-radius:20px; padding:3px 12px; font-size:.8rem; font-weight:600; }
.pill-live    { background:#064e3b; color:#6ee7b7; border:1px solid #059669; border-radius:20px; padding:3px 12px; font-size:.8rem; font-weight:600; }
.pill-sim     { background:#1e3a5f; color:#93c5fd; border:1px solid #3b82f6; border-radius:20px; padding:3px 12px; font-size:.8rem; font-weight:600; }
.pill-info    { background:#1e3a5f; color:#93c5fd; border:1px solid #3b82f6; border-radius:20px; padding:3px 12px; font-size:.8rem; font-weight:600; }
.pill-revert  { background:#065f46; color:#6ee7b7; border:1px solid #059669; border-radius:20px; padding:3px 12px; font-size:.8rem; font-weight:600; }

/* ── Schema table ── */
.stbl { width:100%; border-collapse:collapse; font-size:.85rem; }
.stbl th {
    background:rgba(255,255,255,.05); color:#94a3b8;
    padding:10px 14px; text-align:left; font-weight:500;
    border-bottom:1px solid rgba(255,255,255,.07);
    text-transform:uppercase; letter-spacing:.05em; font-size:.73rem;
}
.stbl td { padding:9px 14px; color:#cbd5e1; border-bottom:1px solid rgba(255,255,255,.04); }
.stbl tr:hover td { background:rgba(255,255,255,.025); }
.stbl .drow td { background:rgba(127,29,29,.2) !important; }
.stbl .crow td { background:rgba(76,5,25,.25) !important; }
.t-ok   { color:#6ee7b7; font-family:'JetBrains Mono',monospace; font-size:.82rem; }
.t-bad  { color:#f87171; font-family:'JetBrains Mono',monospace; font-size:.82rem; font-weight:600; }
.t-name { color:#e2e8f0; font-family:'JetBrains Mono',monospace; font-weight:500; }

/* ── Log box ── */
.logbox {
    background:#0a0f1a; border:1px solid rgba(255,255,255,.07);
    border-radius:10px; padding:16px;
    font-family:'JetBrains Mono',monospace; font-size:.78rem;
    color:#a3e635; max-height:380px; overflow-y:auto;
    white-space:pre-wrap; line-height:1.7;
}

/* ── Scenario card ── */
.scard {
    background:rgba(255,255,255,.025);
    border:1px solid rgba(255,255,255,.07);
    border-radius:14px; padding:18px 20px; margin-bottom:12px;
    transition:border-color .2s;
}
.scard:hover { border-color:rgba(96,165,250,.3); }
.scard-title { font-size:1rem; font-weight:700; color:#e2e8f0; margin-bottom:4px; }
.scard-desc  { font-size:.83rem; color:#94a3b8; margin-bottom:6px; }
.scard-impact { font-size:.8rem; color:#f87171; }

/* ── Timeline ── */
.tl-entry {
    border-left:2px solid rgba(255,255,255,.1);
    margin-left:12px; padding:8px 0 8px 18px; position:relative;
}
.tl-dot {
    width:10px; height:10px; border-radius:50%;
    position:absolute; left:-6px; top:12px;
}
.tl-dot-drift   { background:#ef4444; }
.tl-dot-revert  { background:#10b981; }
.tl-dot-info    { background:#3b82f6; }
.tl-time  { font-size:.75rem; color:#64748b; font-family:'JetBrains Mono',monospace; }
.tl-col   { color:#e2e8f0; font-weight:600; font-size:.88rem; }
.tl-by    { color:#94a3b8; font-size:.8rem; }
.tl-why   { color:#64748b; font-size:.78rem; font-style:italic; }

/* ── Buttons ── */
.stButton>button {
    background:linear-gradient(135deg,#0ea5e9,#6366f1)!important;
    color:#fff!important; border:none!important; border-radius:8px!important;
    font-weight:600!important; font-family:'Inter',sans-serif!important;
    transition:opacity .2s,transform .1s!important;
}
.stButton>button:hover { opacity:.88!important; transform:translateY(-1px)!important; }

/* ── Streamlit overrides ── */
.stTabs [data-baseweb="tab-list"] { background:rgba(255,255,255,.025); border-radius:10px; padding:4px; }
.stTabs [data-baseweb="tab"] { color:#94a3b8!important; border-radius:7px; }
.stTabs [aria-selected="true"] { background:rgba(96,165,250,.15)!important; color:#60a5fa!important; }
div[data-testid="stMarkdownContainer"] { color:#cbd5e1; }
.stSelectbox label,.stTextArea label,.stTextInput label { color:#94a3b8!important; }
.stTextArea textarea { background:#0a0f1a!important; color:#a3e635!important; font-family:'JetBrains Mono',monospace!important; border:1px solid rgba(255,255,255,.1)!important; }
.stTextInput input { background:#0a0f1a!important; color:#e2e8f0!important; border:1px solid rgba(255,255,255,.1)!important; }
.stDataFrame { background:transparent; }
</style>
""", unsafe_allow_html=True)

# =============================================================================
# Session State Helpers
# =============================================================================
def get_datahub() -> DataHubClient:
    if "datahub" not in st.session_state:
        st.session_state.datahub = DataHubClient()
    return st.session_state.datahub


def get_pipeline_context() -> "PipelineContext | None":
    """
    Discover pipeline from DataHub. Cached in session_state.
    Returns None if discovery fails (agent will still function on hardcoded fallback).
    """
    if "pipeline_context" in st.session_state:
        return st.session_state.pipeline_context
    try:
        gms = get_datahub()._gms
        ctx = discover_pipeline(gms)
        st.session_state.pipeline_context = ctx
        return ctx
    except RuntimeError as exc:
        st.session_state.pipeline_context = None
        st.session_state.pipeline_context_error = str(exc)
        return None


def rediscover_pipeline():
    """Force re-discovery. Call after registering new metadata."""
    st.session_state.pop("pipeline_context", None)
    st.session_state.pop("pipeline_context_error", None)
    st.session_state.pop("inspector", None)  # rebuild inspector with new context


def get_inspector() -> PostgresSchemaInspector:
    if "inspector" not in st.session_state:
        ctx = get_pipeline_context()
        st.session_state.inspector = PostgresSchemaInspector(pipeline_context=ctx)
    return st.session_state.inspector

def refresh_inspector():
    for k in ["inspector", "drift_cache", "last_results"]:
        if k in st.session_state:
            del st.session_state[k]

def get_drift_report(inspector):
    """Cached drift scan (recomputed on every full page load)."""
    return scan_all_drifts(inspector)

# =============================================================================
# Rendering Helpers
# =============================================================================
def render_schema_table(table_name: str, baseline: list, actual: list):
    baseline_map = {f["column"].lower(): f for f in baseline}
    actual_map   = {f["column"].lower(): f for f in actual}
    all_cols = list(dict.fromkeys(
        [f["column"].lower() for f in baseline] +
        [f["column"].lower() for f in actual]
    ))

    rows_html = ""
    for col in all_cols:
        base = baseline_map.get(col, {})
        act  = actual_map.get(col, {})
        base_type = base.get("type", "—")
        act_type  = act.get("type", "MISSING")
        is_critical = col in {
            "amount", "oldbalanceorg", "newbalanceorig",
            "oldbalancedest", "newbalancedest", "isfraud"
        }

        if act_type == "MISSING":
            status_html = '<span class="pill-missing">⚠ MISSING</span>'
            act_html    = '<span class="t-bad">MISSING</span>'
            row_cls     = "drow"
        elif act_type.lower() != base_type.lower():
            status_html = '<span class="pill-crit">⚠ DRIFTED</span>' if is_critical else '<span class="pill-drift">⚠ DRIFTED</span>'
            act_html    = f'<span class="t-bad">{act_type}</span>'
            row_cls     = "crow" if is_critical else "drow"
        else:
            status_html = '<span class="pill-ok">✓ OK</span>'
            act_html    = f'<span class="t-ok">{act_type}</span>'
            row_cls     = ""

        crit_badge = ' 🔑' if is_critical else ''
        rows_html += (
            f'<tr class="{row_cls}">'
            f'<td><span class="t-name">{col}{crit_badge}</span></td>'
            f'<td><span class="t-ok">{base_type}</span></td>'
            f'<td>{act_html}</td>'
            f'<td>{status_html}</td>'
            f'</tr>'
        )

    st.markdown(
        f'<table class="stbl"><thead><tr>'
        f'<th>Column</th><th>Baseline (Expected)</th><th>Actual (DB)</th><th>Status</th>'
        f'</tr></thead><tbody>{rows_html}</tbody></table>',
        unsafe_allow_html=True,
    )
    st.markdown('<div style="font-size:.73rem;color:#64748b;margin-top:6px">🔑 = Critical ML feature</div>', unsafe_allow_html=True)


def render_change_log_timeline(entries: list):
    if not entries:
        st.markdown('<div class="alert-ok">✅ No schema changes recorded yet.</div>', unsafe_allow_html=True)
        return

    for e in entries:
        is_reverted = e.get("is_reverted", False)
        col_name    = e.get("column_name", "?")
        severity    = e.get("severity", "INFO")

        if col_name == "__schema_init__" or severity == "INFO":
            dot_cls = "tl-dot-info"
            badge   = '<span class="pill-info">ℹ INIT</span>'
        elif is_reverted:
            dot_cls = "tl-dot-revert"
            badge   = '<span class="pill-revert">✓ REVERTED</span>'
        else:
            dot_cls = "tl-dot-drift"
            badge   = f'<span class="pill-crit">⚠ ACTIVE</span>'

        changed_at = e.get("changed_at", "?")
        if hasattr(changed_at, "isoformat"):
            changed_at = changed_at.isoformat()

        reverted_str = ""
        if is_reverted and e.get("reverted_at"):
            reverted_str = f' <span style="color:#10b981;font-size:.75rem">→ reverted at {e["reverted_at"]}</span>'

        st.markdown(
            f'<div class="tl-entry">'
            f'<div class="tl-dot {dot_cls}"></div>'
            f'<div class="tl-time">{changed_at}{reverted_str}</div>'
            f'<div class="tl-col">'
            f'<code>{e.get("table_name","?")}.{col_name}</code>: '
            f'<code>{e.get("old_type","?")}</code> → <code>{e.get("new_type","?")}</code>'
            f'  {badge}</div>'
            f'<div class="tl-by">By: {e.get("changed_by","unknown")} | Severity: {severity}</div>'
            f'<div class="tl-why">Reason: {e.get("change_reason","N/A")}</div>'
            f'{"<div style=\"color:#64748b;font-size:.75rem\">Note: " + str(e.get("notes","")) + "</div>" if e.get("notes") else ""}'
            f'</div>',
            unsafe_allow_html=True,
        )


# =============================================================================
# Sidebar
# =============================================================================
with st.sidebar:
    st.markdown("### 🤖 AI Data Reliability Agent")
    st.markdown('<div style="font-size:.75rem;color:#64748b">DataHub Agent Hackathon 2026</div>', unsafe_allow_html=True)
    st.markdown("---")

    inspector = get_inspector()
    mode = inspector.mode()

    if mode == "live":
        st.markdown('<span class="pill-live">🟢 Live PostgreSQL</span>', unsafe_allow_html=True)
        st.caption(f"Host: `{inspector.host}:{inspector.port}`")
        st.caption(f"DB: `{inspector.database}`")
    else:
        st.markdown('<span class="pill-sim">🔵 Simulation Mode</span>', unsafe_allow_html=True)
        st.caption("PostgreSQL unreachable — using stateful in-memory simulation.")

    st.markdown("---")

    if st.button("🔄 Reconnect DB"):
        refresh_inspector()
        st.rerun()

    # ── Discovered Pipeline (from DataHub) ──────────────────────────────
    st.markdown("---")
    st.markdown("**🕸️ Discovered Pipeline**")
    ctx = get_pipeline_context()
    if ctx is None:
        err = st.session_state.get("pipeline_context_error", "unknown")
        st.error("No pipeline discovered from DataHub.")
        with st.expander("Why?"):
            st.caption(err)
        st.caption(
            "Register at least one ML pipeline in DataHub, then click Rediscover. "
            "See `metadata/lineage_bootstrap.py` as reference."
        )
    else:
        st.caption(f"✅ {len(ctx.ml_models)} model(s), {len(ctx.source_tables)} table(s)")
        with st.expander("Models"):
            for m in ctx.ml_models:
                st.caption(f"• `{m.name}` [{m.platform}]")
        with st.expander("Source tables"):
            for t in ctx.source_tables.values():
                crit_n = len(ctx.critical_columns_for(t.table_name))
                st.caption(f"• `{t.table_name}` — {len(t.columns)} cols ({crit_n} critical)")
        st.caption(f"Filter: platform ∈ {ctx.ml_platforms_filter} · tag ∈ {ctx.tag_filter}")

    if st.button("🔄 Rediscover from DataHub"):
        rediscover_pipeline()
        st.rerun()

    # Data seeding section (live mode only)
    if inspector.is_live():
        st.markdown("---")
        st.markdown("**📦 Data Seeding**")
        data_status = check_data_loaded(inspector)
        if data_status.get("loaded"):
            st.caption(f"✅ {data_status['row_count']:,} rows loaded")
        else:
            st.caption("⚠️ Table is empty")
            if st.button("🌱 Seed Sample Data (50k rows)"):
                with st.spinner("Loading PaySim data into DB..."):
                    result = seed_transactions(inspector, max_rows=50000)
                if result.get("error"):
                    st.error(f"Failed: {result['error']}")
                elif result.get("skipped"):
                    st.info(result["message"])
                else:
                    st.success(f"✅ {result['rows_loaded']:,} rows loaded!")

    st.markdown("---")
    st.markdown("**Tech Stack**")
    for item in ["Python 3.10+", "PostgreSQL 15", "Streamlit", "psycopg2", "DataHub SDK"]:
        st.caption(f"• {item}")


# =============================================================================
# Hero Banner
# =============================================================================
inspector = get_inspector()
drift_report = get_drift_report(inspector)
total_tables    = len(drift_report["tables"])
total_drifts    = drift_report["total_drifts"]
critical_drifts = drift_report["critical_drifts"]
ok_tables       = sum(1 for t in drift_report["tables"].values() if t.get("status") == "OK")

st.markdown(
    f"""
    <div class="hero">
        <p class="hero-title">🤖 AI Data Reliability Agent</p>
        <p class="hero-sub">
            Autonomous schema drift detection &amp; remediation for FinTech ML pipelines
            &nbsp;•&nbsp; DataHub Agent Hackathon 2026
            &nbsp;•&nbsp; Mode: <strong>{"Live PostgreSQL" if inspector.is_live() else "Simulation"}</strong>
        </p>
    </div>
    """,
    unsafe_allow_html=True,
)

# Metric row
c1, c2, c3, c4, c5 = st.columns(5)
metrics = [
    (c1, "Tables Monitored",   str(total_tables),  "#60a5fa"),
    (c2, "Total Drifts",       str(total_drifts),  "#f87171" if total_drifts else "#6ee7b7"),
    (c3, "Critical Drifts",    str(critical_drifts),"#fda4af" if critical_drifts else "#6ee7b7"),
    (c4, "Tables Healthy",     str(ok_tables),     "#6ee7b7"),
    (c5, "Mode", "🟢 Live" if inspector.is_live() else "🔵 Sim", "#60a5fa"),
]
for col, label, val, color in metrics:
    with col:
        st.markdown(
            f'<div class="mcard"><div class="mlbl">{label}</div>'
            f'<div class="mval" style="color:{color}">{val}</div></div>',
            unsafe_allow_html=True,
        )

st.markdown("<br>", unsafe_allow_html=True)

# =============================================================================
# Tabs
# =============================================================================
tab_monitor, tab_investigate, tab_simulate, tab_history, tab_info = st.tabs([
    "📊 Schema Monitor",
    "🔍 Run Investigation",
    "💥 Simulate Incident",
    "📋 Change History",
    "🗺️ Lineage & Info",
])


# ─────────────────────────────────────────────────────────────────────────────
# TAB 1: Schema Monitor
# ─────────────────────────────────────────────────────────────────────────────
with tab_monitor:
    st.markdown("### 📊 Real-Time Schema Monitor")
    st.caption(
        f"Comparing actual DB schema vs `schema_baseline` table "
        f"• Last scan: {drift_report['scanned_at']}"
    )

    if drift_report["any_drift"]:
        st.markdown(
            f'<div class="alert-crit">🚨 <strong>SCHEMA DRIFT DETECTED</strong> — '
            f'{total_drifts} drift(s) found, {critical_drifts} CRITICAL. '
            f'Fraud Detection ML model may be producing incorrect predictions.</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="alert-ok">✅ <strong>All schemas match baseline.</strong> '
            'Pipeline is healthy — no drift detected.</div>',
            unsafe_allow_html=True,
        )

    colR, _ = st.columns([1, 6])
    with colR:
        if st.button("🔄 Refresh", key="refresh_monitor"):
            st.rerun()

    st.markdown("---")

    for table_name, info in drift_report["tables"].items():
        status  = info.get("status", "UNKNOWN")
        n_drift = len(info.get("drifts", []))
        n_crit  = sum(1 for d in info.get("drifts", []) if d.get("severity") == "CRITICAL")
        emoji   = "✅" if status == "OK" else ("🔴" if n_crit else "⚠️")

        label_parts = [f"{emoji} `{table_name}`"]
        if n_crit:
            label_parts.append(f"— {n_crit} CRITICAL drift(s)")
        elif n_drift:
            label_parts.append(f"— {n_drift} drift(s)")
        else:
            label_parts.append("— Clean ✓")

        with st.expander(" ".join(label_parts), expanded=(status == "DRIFTED")):
            baseline = info.get("baseline", [])
            actual   = info.get("actual", [])
            if not actual:
                st.warning(f"Table `{table_name}` not found in DB / simulation state.")
            else:
                render_schema_table(table_name, baseline, actual)

            for d in info.get("drifts", []):
                sev = d.get("severity", "MEDIUM")
                cls = "alert-crit" if sev == "CRITICAL" else "alert-warn"
                st.markdown(
                    f'<div class="{cls}">'
                    f'<strong>[{sev}] {d["drift_type"]}</strong> — '
                    f'Column <code>{d["column"]}</code>: '
                    f'expected <code>{d["baseline_type"]}</code>, '
                    f'found <code>{d["actual_type"]}</code>'
                    f'</div>',
                    unsafe_allow_html=True,
                )


# ─────────────────────────────────────────────────────────────────────────────
# TAB 2: Run Investigation
# ─────────────────────────────────────────────────────────────────────────────
with tab_investigate:
    st.markdown("### 🔍 Run AI Investigation")
    st.caption(
        "The agent scans the real DB schema, reads the audit trail from `schema_change_log`, "
        "computes impact from actual transaction data, and generates fix scripts."
    )

    default_alert = ALERT_TEMPLATE.format(**DEMO_ALERT_VALUES)
    alert_msg = st.text_area("Alert Message", value=default_alert, height=170, key="alert_txt")

    if st.button("🔍 Run Investigation", key="btn_investigate"):
        with st.spinner("🤖 AI Agent investigating..."):
            fresh_insp = PostgresSchemaInspector()
            datahub    = get_datahub()
            agent      = ReliabilityAgent(datahub_client=datahub, db_inspector=fresh_insp)

            prog = st.progress(0, text="Step 1: Scanning DB schema vs baseline...")
            time.sleep(0.3)
            prog.progress(15, text="Step 2: Reading schema_change_log audit trail...")
            time.sleep(0.3)
            prog.progress(30, text="Step 3: Tracing DataHub lineage graph...")
            time.sleep(0.3)
            prog.progress(50, text="Step 4: Collecting drift events...")
            time.sleep(0.2)
            prog.progress(65, text="Step 5: Computing impact from transaction data...")
            results = agent.investigate(alert_msg)
            prog.progress(85, text="Step 6: Root cause analysis...")
            time.sleep(0.2)
            prog.progress(100, text="Step 7: ✅ Remediation complete!")
            time.sleep(0.4)
            prog.empty()

        st.session_state["last_results"] = results

    if "last_results" in st.session_state:
        results = st.session_state["last_results"]
        status  = results.get("status", "")

        if status == "no_drift":
            st.markdown(
                '<div class="alert-ok">✅ <strong>No drift detected.</strong> '
                'All schemas match baseline — ML model should be operating normally.</div>',
                unsafe_allow_html=True,
            )
        else:
            drifts = results.get("drifts", [])
            n_crit = sum(1 for d in drifts if d.get("severity") == "CRITICAL")
            st.markdown(
                f'<div class="alert-crit">🚨 <strong>Incident Detected!</strong> '
                f'{len(drifts)} drift(s) found, {n_crit} CRITICAL. '
                f'ML model reliability compromised.</div>',
                unsafe_allow_html=True,
            )

            r_col1, r_col2 = st.columns(2)

            # Left: Drift table + root cause
            with r_col1:
                st.markdown("#### ⚠️ Detected Drifts")
                import pandas as pd
                df_drifts = pd.DataFrame([
                    {
                        "Table": d["table"],
                        "Column": d["column"],
                        "Baseline": d["baseline_type"],
                        "Actual": d["actual_type"],
                        "Severity": d.get("severity","?"),
                        "Changed By": d.get("changed_by","unknown"),
                    }
                    for d in drifts
                ])
                st.dataframe(df_drifts, use_container_width=True, hide_index=True)

                with st.expander("🔎 Root Cause Analysis", expanded=True):
                    st.markdown(f"```\n{results.get('root_cause','')}\n```")

            # Right: Impact metrics + audit log from DB
            with r_col2:
                st.markdown("#### 📊 Impact Metrics (from real data)")
                impact = results.get("impact_metrics", {})
                ts = results.get("table_stats", {})

                total_rows = ts.get("total_rows", 0)
                if total_rows:
                    st.markdown(
                        f'<div class="alert-warn">'
                        f'<strong>{total_rows:,}</strong> transactions in DB<br>'
                        f'Fraud cases: <strong>{ts.get("fraud_rows", "?"):,}</strong> '
                        f'({ts.get("fraud_rate_pct", "?"):.4f}%)'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                    # Per-column impact
                    for col, cm in impact.get("columns", {}).items():
                        if not cm.get("type_ok", True):
                            corrupted = cm.get("corrupted_count", "?")
                            pct = cm.get("corrupted_pct", "?")
                            label = f"`{col}`: {corrupted:,} rows affected ({pct}%)" if isinstance(corrupted, int) else f"`{col}`: type mismatch"
                            st.markdown(f'<div class="alert-crit">💀 {label}</div>', unsafe_allow_html=True)
                else:
                    st.markdown(
                        '<div class="alert-warn">⚠️ No transaction data in DB — '
                        'seed data to compute real impact metrics.</div>',
                        unsafe_allow_html=True,
                    )

                st.markdown("#### 📋 Audit Trail (schema_change_log)")
                log_entries = results.get("schema_change_log", [])
                if log_entries:
                    for e in log_entries[:5]:
                        t  = e.get("changed_at", "?")
                        if hasattr(t, "isoformat"):
                            t = t.isoformat()
                        st.markdown(
                            f'<div style="padding:6px 0;border-bottom:1px solid rgba(255,255,255,.05)">'
                            f'<span style="color:#f87171;font-family:monospace;font-size:.8rem">'
                            f'[{t[:19]}]</span> '
                            f'<code>{e.get("table_name","?")}.{e.get("column_name","?")}</code>: '
                            f'<code>{e.get("old_type","?")}</code> → <code>{e.get("new_type","?")}</code><br>'
                            f'<span style="color:#94a3b8;font-size:.78rem">By: {e.get("changed_by","?")}</span>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )
                else:
                    st.caption("No audit log entries found (drift may be from outside this system).")

            st.markdown("---")

            # Fix scripts
            fix_scripts = results.get("fix_scripts", {})
            if fix_scripts:
                st.markdown("#### 🔧 Generated Fix Scripts")
                for key, fs in fix_scripts.items():
                    with st.expander(f"📄 Fix: `{key}`", expanded=False):
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

            # DataHub tag
            tag = results.get("tag_applied", "")
            if tag:
                st.markdown(
                    f'<div class="alert-info">🏷️ Tag applied to DataHub: '
                    f'<code>{tag}</code> → <code>Fraud_Detection_ML_Model</code></div>',
                    unsafe_allow_html=True,
                )

            # Full report
            with st.expander("📄 Full Investigation Report"):
                st.markdown(results.get("report", ""))

            # Investigation log
            with st.expander("🖥️ Agent Execution Log"):
                log_text = "\n".join(
                    f"[{e['timestamp']}] [{e['step']}]\n  {e['detail']}\n"
                    for e in results.get("investigation_log", [])
                )
                st.markdown(f'<div class="logbox">{log_text}</div>', unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# TAB 3: Simulate Incident
# ─────────────────────────────────────────────────────────────────────────────
with tab_simulate:
    st.markdown("### 💥 Simulate Schema Drift Incident")
    inspector = get_inspector()
    mode_label = "Live PostgreSQL" if inspector.is_live() else "Simulation State"

    st.markdown(
        f'<div class="alert-info">ℹ️ Mode: <strong>{mode_label}</strong> — '
        f'Changes will be applied to {"the real PostgreSQL database via ALTER TABLE" if inspector.is_live() else "the in-memory simulation state"}. '
        f'Every change is recorded in <code>schema_change_log</code>.</div>',
        unsafe_allow_html=True,
    )

    # ── Preset Scenarios ──────────────────────────────────────────────────
    st.markdown("#### 🎭 Preset Drift Scenarios")
    st.caption("These simulate real-world mistakes engineers make in FinTech pipelines.")

    sc_cols = st.columns(3)
    for (sc_key, sc), col in zip(DRIFT_SCENARIOS.items(), sc_cols):
        with col:
            st.markdown(
                f'<div class="scard">'
                f'<div class="scard-title">{sc["emoji"]} Scenario {sc_key}: {sc["name"]}</div>'
                f'<div class="scard-desc">{sc["description"]}</div>'
                f'<div class="scard-impact">💀 {sc["impact"]}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
            with st.expander(f"Configure Scenario {sc_key}"):
                who = st.text_input(f"Changed by", value=sc["changed_by_default"], key=f"who_{sc_key}")
                why = st.text_area(f"Reason", value=sc["reason_default"], height=80, key=f"why_{sc_key}")
                if st.button(f"{sc['emoji']} Apply Scenario {sc_key}", key=f"apply_{sc_key}"):
                    results_msgs = []
                    all_ok = True
                    for drift in sc["drifts"]:
                        ok, msg = inspector.apply_drift_to_db(
                            table=drift["table"],
                            column=drift["column"],
                            new_pg_type=drift["new_type"],
                            changed_by=who,
                            reason=why,
                            severity="CRITICAL",
                        )
                        results_msgs.append(msg)
                        if not ok:
                            all_ok = False

                    if all_ok:
                        st.success(f"✅ Scenario {sc_key} applied!\n" + "\n".join(results_msgs))
                        st.info("👉 Go to **Schema Monitor** to see the drift, then **Run Investigation**.")
                    else:
                        st.error("❌ One or more changes failed:\n" + "\n".join(results_msgs))
                    time.sleep(0.5)
                    st.rerun()

    st.markdown("---")

    # ── Custom Drift ─────────────────────────────────────────────────────
    st.markdown("#### 🛠️ Custom Drift")
    # Table/column options come from the discovered pipeline (falls back to JSON).
    # Views are filtered out — they can't be ALTERed independently.
    ctx = get_pipeline_context()
    monitored_only = set(inspector.monitored_tables())
    if ctx is not None and ctx.source_tables:
        table_options = [t for t in ctx.source_tables.keys() if t in monitored_only]
        if not table_options:
            table_options = list(ctx.source_tables.keys())  # last-resort fallback
        def _cols_for(t):
            spec = ctx.source_tables.get(t)
            return [c.column for c in spec.columns] if spec else []
        def _base_type_for(t, col):
            spec = ctx.source_tables.get(t)
            if not spec:
                return "double precision"
            return next((c.baseline_type for c in spec.columns if c.column == col), "double precision")
    else:
        baseline_all = _load_baseline_json()
        table_options = MONITORED_TABLES
        def _cols_for(t):
            return [f["column"] for f in baseline_all.get(t, [])]
        def _base_type_for(t, col):
            return next((f["type"] for f in baseline_all.get(t, []) if f["column"] == col), "double precision")

    cc1, cc2, cc3 = st.columns([2, 2, 2])
    with cc1:
        drift_table = st.selectbox("Table", table_options, key="cust_table")
    with cc2:
        drift_col = st.selectbox("Column", _cols_for(drift_table), key="cust_col")
    with cc3:
        base_type = _base_type_for(drift_table, drift_col)
        bad_types = {
            "double precision": ["character varying", "text", "varchar(50)"],
            "integer":          ["character varying", "text", "boolean"],
            "character varying":["integer", "double precision"],
        }
        type_opts = bad_types.get(base_type.lower(), ["character varying", "text"])
        drift_new_type = st.selectbox(f"New type (baseline: `{base_type}`)", type_opts, key="cust_type")

    cw1, cw2 = st.columns(2)
    with cw1:
        custom_who = st.text_input("Changed by", value="engineer_name", key="cust_who")
    with cw2:
        custom_why = st.text_input("Reason", value="Manual change — enter reason here", key="cust_why")

    if st.button("💥 Apply Custom Drift", key="apply_custom"):
        ok, msg = inspector.apply_drift_to_db(
            table=drift_table, column=drift_col, new_pg_type=drift_new_type,
            changed_by=custom_who, reason=custom_why, severity="HIGH",
        )
        if ok:
            st.success(f"✅ {msg}")
            st.info("👉 Head to **Schema Monitor** → **Run Investigation**")
        else:
            st.error(f"❌ {msg}")
        time.sleep(0.5)
        st.rerun()

    st.markdown("---")

    # ── Reset ─────────────────────────────────────────────────────────────
    st.markdown("#### 🔄 Reset to Baseline")
    st.caption("Restore all columns back to their baseline types and mark changes as reverted.")

    if st.button("🔄 Reset ALL to Baseline", key="btn_reset"):
        with st.spinner("Restoring all columns..."):
            results_r = inspector.restore_all_to_baseline()

        if results_r["restored"]:
            st.success(f"✅ Restored: {', '.join(results_r['restored'])}")
        if results_r["failed"]:
            st.error(f"❌ Failed: {', '.join(results_r['failed'])}")
        if results_r["skipped"]:
            st.warning(f"⏭️ Skipped: {', '.join(results_r['skipped'])}")
        if not any(results_r.values()):
            st.info("ℹ️ No drifted columns found — schema is already at baseline.")
        time.sleep(0.5)
        st.rerun()

    # Current drift status quick view
    st.markdown("##### Current Drift Status")
    current = scan_all_drifts(inspector)
    for tname, tinfo in current["tables"].items():
        s = tinfo.get("status", "?")
        icon = "✅" if s == "OK" else "🔴"
        nd = len(tinfo.get("drifts", []))
        nc = sum(1 for d in tinfo.get("drifts", []) if d.get("severity") == "CRITICAL")
        detail = f"{nc} CRITICAL, {nd-nc} other" if nc else (f"{nd} drift(s)" if nd else "Clean")
        st.markdown(f"{icon} **`{tname}`** — {detail}")


# ─────────────────────────────────────────────────────────────────────────────
# TAB 4: Change History
# ─────────────────────────────────────────────────────────────────────────────
with tab_history:
    st.markdown("### 📋 Schema Change History")
    st.caption(
        "Audit trail from `schema_change_log` table — every drift, reset, and baseline registration."
    )

    inspector = get_inspector()
    log_entries = inspector.get_schema_change_log(limit=50)

    h_col1, h_col2 = st.columns([1, 5])
    with h_col1:
        if st.button("🔄 Refresh Log", key="refresh_log"):
            st.rerun()

    if not log_entries:
        st.markdown(
            '<div class="alert-info">ℹ️ No schema changes recorded yet. '
            'Apply a drift scenario to see entries here.</div>',
            unsafe_allow_html=True,
        )
    else:
        # Summary counts
        active  = sum(1 for e in log_entries if not e.get("is_reverted") and e.get("severity") not in ("INFO", None))
        reverted = sum(1 for e in log_entries if e.get("is_reverted"))

        sc1, sc2, sc3 = st.columns(3)
        with sc1:
            sc1.markdown(
                f'<div class="mcard"><div class="mlbl">Total Entries</div>'
                f'<div class="mval">{len(log_entries)}</div></div>',
                unsafe_allow_html=True,
            )
        with sc2:
            sc2.markdown(
                f'<div class="mcard"><div class="mlbl">Active Drifts</div>'
                f'<div class="mval" style="color:{"#f87171" if active else "#6ee7b7"}">{active}</div></div>',
                unsafe_allow_html=True,
            )
        with sc3:
            sc3.markdown(
                f'<div class="mcard"><div class="mlbl">Reverted</div>'
                f'<div class="mval" style="color:#6ee7b7">{reverted}</div></div>',
                unsafe_allow_html=True,
            )

        st.markdown("<br>", unsafe_allow_html=True)

        # DataFrame view
        import pandas as pd
        df_log = pd.DataFrame([
            {
                "ID":          e.get("id", ""),
                "Table":       e.get("table_name", ""),
                "Column":      e.get("column_name", ""),
                "Old Type":    e.get("old_type", ""),
                "New Type":    e.get("new_type", ""),
                "Changed By":  e.get("changed_by", ""),
                "Severity":    e.get("severity", ""),
                "Status":      "✅ Reverted" if e.get("is_reverted") else ("ℹ Init" if e.get("severity") == "INFO" else "🔴 Active"),
                "Changed At":  str(e.get("changed_at", "")),
                "Reason":      e.get("change_reason", ""),
            }
            for e in log_entries
        ])
        st.dataframe(df_log, use_container_width=True, hide_index=True)

        st.markdown("---")
        st.markdown("#### 🕐 Timeline View")
        render_change_log_timeline(log_entries)


# ─────────────────────────────────────────────────────────────────────────────
# TAB 5: Lineage & Info
# ─────────────────────────────────────────────────────────────────────────────
with tab_info:
    st.markdown("### 🗺️ Data Pipeline Lineage")

    lineage_ascii = """
┌──────────────────────────────────────────────┐
│  paysim_raw_transactions                     │
│  PostgreSQL — SOURCE OF TRUTH                │
│  11 columns | ~50k sample rows (PaySim)     │
│  🔑 amount, oldbalanceOrg, isFraud, ...      │
└────────────────────┬─────────────────────────┘
                     │  ETL / PostgreSQL VIEW
                     ▼
┌──────────────────────────────────────────────┐
│  feature_engineering_table                   │
│  PostgreSQL VIEW — Derived Features          │
│  16 columns (11 original + 5 computed)       │
│  🔑 amount_ratio, balance_change_orig, ...   │
└────────────────────┬─────────────────────────┘
                     │  ML Features → Model
                     ▼
┌──────────────────────────────────────────────┐
│  Fraud_Detection_ML_Model                    │
│  XGBoost Classifier v2.1 (MLflow)           │
│  Baseline Accuracy: 97.8%                   │
│  False Positive Rate: 2.1%                  │
└──────────────────────────────────────────────┘
    """
    st.markdown(
        f'<div style="background:#0a0f1a;border:1px solid rgba(96,165,250,.2);'
        f'border-radius:12px;padding:20px;font-family:\'JetBrains Mono\',monospace;'
        f'font-size:.85rem;color:#93c5fd;line-height:1.9;white-space:pre">{lineage_ascii}</div>',
        unsafe_allow_html=True,
    )

    st.markdown("---")
    li1, li2 = st.columns(2)

    with li1:
        st.markdown("#### 🔑 Critical ML Features")
        features = [
            ("amount",            "Transaction amount — primary fraud signal"),
            ("amount_ratio",      "amount / oldbalanceOrg — transaction-to-balance ratio"),
            ("balance_change_orig","oldbalanceOrg - newbalanceOrig — sender balance delta"),
            ("balance_change_dest","newbalanceDest - oldbalanceDest — recipient balance delta"),
            ("is_large_transaction","1 if amount > 200,000 — rule-based flag"),
            ("is_balance_mismatch","1 if balance math is inconsistent — fraud signal"),
            ("isfraud",           "Target variable — 0=legit, 1=fraud"),
        ]
        for col, desc in features:
            st.markdown(
                f'<div style="padding:7px 0;border-bottom:1px solid rgba(255,255,255,.04)">'
                f'<code style="color:#60a5fa">{col}</code> '
                f'<span style="color:#94a3b8;font-size:.8rem">— {desc}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

    with li2:
        st.markdown("#### ⚙️ Agent Capabilities (v3)")
        caps = [
            ("🔍 Real DB Schema Scan",     "Reads `information_schema.columns` directly"),
            ("📊 Baseline Comparison",     "Compares vs `schema_baseline` table in DB"),
            ("📋 Audit Trail",             "Reads/writes `schema_change_log` — who, when, why"),
            ("🗺️ Lineage Traversal",       "Follows DataHub lineage graph (Python catalog)"),
            ("💀 Real Impact Metrics",     "COUNT queries on actual transaction data"),
            ("🏷️ Tag Write-Back",          "Tags ML model with DATA-INCIDENT in DataHub"),
            ("🔧 Fix Generation",          "ALTER TABLE SQL + dbt CAST patch"),
            ("💥 3 Preset Scenarios",      "Realistic engineer mistakes (ETL, pipeline, DBA)"),
            ("🔄 Auto-Restore",            "One-click restore all columns to baseline"),
        ]
        for cap, desc in caps:
            st.markdown(
                f'<div style="padding:7px 0;border-bottom:1px solid rgba(255,255,255,.04)">'
                f'<strong style="color:#60a5fa">{cap}</strong> '
                f'<span style="color:#64748b;font-size:.8rem">{desc}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # Live DB table stats
    if inspector.is_live():
        st.markdown("---")
        st.markdown("#### 📊 Live Transaction Data Stats")
        ts = inspector.get_table_stats()
        if ts.get("total_rows", 0):
            sc1, sc2, sc3, sc4 = st.columns(4)
            for col, label, val in [
                (sc1, "Total Transactions", f"{ts['total_rows']:,}"),
                (sc2, "Fraud Cases",        f"{ts.get('fraud_rows', 0):,}"),
                (sc3, "Fraud Rate",         f"{ts.get('fraud_rate_pct', 0):.4f}%"),
                (sc4, "Avg Amount",         f"${ts.get('avg_amount', 0):,.2f}"),
            ]:
                with col:
                    col.markdown(
                        f'<div class="mcard"><div class="mlbl">{label}</div>'
                        f'<div class="mval" style="font-size:1.3rem">{val}</div></div>',
                        unsafe_allow_html=True,
                    )

            if ts.get("transaction_types"):
                st.markdown("**Transaction Type Breakdown:**")
                import pandas as pd
                df_types = pd.DataFrame(
                    [(k, v) for k, v in ts["transaction_types"].items()],
                    columns=["Type", "Count"],
                ).sort_values("Count", ascending=False)
                st.bar_chart(df_types.set_index("Type"))
        else:
            st.markdown(
                '<div class="alert-warn">⚠️ No transaction data loaded. '
                'Use sidebar → 🌱 Seed Sample Data.</div>',
                unsafe_allow_html=True,
            )
