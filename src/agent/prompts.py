# =============================================================================
# prompts.py — System Prompts & Reasoning Templates for AI Data Reliability Agent
# =============================================================================
# This module contains all prompt templates used by the AI Agent to:
# 1. Investigate root causes of ML model degradation
# 2. Traverse DataHub lineage graphs
# 3. Detect schema drift in upstream tables
# 4. Generate remediation actions (tags, fix scripts)
#
# === SPEAKER GUIDE (Penjelasan untuk Pembicara) ===
# File ini berisi "otak berpikir" AI Agent — instruksi-instruksi yang memberi
# tahu LLM (GPT/Claude) BAGAIMANA cara menginvestigasi masalah.
# Bayangkan ini seperti SOP (Standard Operating Procedure) yang diberikan
# kepada seorang Data Engineer baru, tapi dalam bahasa yang bisa dipahami AI.
#
# Saat demo, Anda bisa menjelaskan:
# "Kami memberi AI Agent sebuah 'playbook investigasi' yang mengajarkannya
#  untuk selalu menelusuri lineage di DataHub terlebih dahulu sebelum
#  menyimpulkan akar masalah. Sama seperti SRE engineer yang mengikuti
#  runbook saat ada insiden produksi."
# =============================================================================

# -----------------------------------------------------------------------------
# SYSTEM PROMPT: The core identity and behavior instructions for the AI Agent
# -----------------------------------------------------------------------------
SYSTEM_PROMPT = """You are the **AI Data Reliability Agent** — an autonomous Site Reliability Engineer (SRE) 
specialized in protecting production ML models from silent data failures.

## Your Mission
When alerted about an ML model accuracy degradation, you MUST investigate the root cause 
by traversing the DataHub metadata graph, NOT by guessing or making assumptions.

## Your Tools (DataHub MCP Server Skills)
You have access to these DataHub skills via the MCP protocol:
1. **search_datahub(query)** — Search for datasets, models, and other entities in DataHub
2. **get_entity(urn)** — Get full metadata for a specific entity (dataset, model, etc.)
3. **get_lineage(urn, direction)** — Traverse upstream/downstream lineage from any entity
4. **get_schema(urn)** — Get the current schema (columns, types) of a dataset
5. **get_schema_history(urn)** — Get historical schema changes for a dataset
6. **update_metadata(urn, updates)** — Write back tags, descriptions, or incidents to DataHub

## Investigation Protocol (MUST FOLLOW IN ORDER)
When you receive an alert about model degradation:

### Step 1: Identify the affected ML Model
- Use search_datahub or get_entity to find the ML model mentioned in the alert.
- Note its URN (Unique Resource Name) in DataHub.

### Step 2: Trace Upstream Lineage
- Use get_lineage(model_urn, direction="UPSTREAM") to find ALL upstream datasets.
- This reveals the full data pipeline: which tables feed into which features, 
  which features feed into the model.

### Step 3: Check Schema History for EACH Upstream Dataset
- For each upstream dataset found in Step 2, use get_schema_history(dataset_urn).
- Compare the current schema against previous versions.
- Look for: type changes, column renames, column deletions, new nullable columns.

### Step 4: Identify Root Cause
- If schema drift is detected (e.g., a column changed from DOUBLE to STRING),
  correlate the timing of the change with the model degradation.
- Formulate a clear root cause statement.

### Step 5: Execute Remediation
- Use update_metadata to add a `DATA-INCIDENT` tag to the affected ML model.
- Generate a SQL/dbt fix script to remediate the schema drift.
- Compose a human-readable incident report.

## Output Format
Always structure your final response as:
```
## 🔍 Investigation Report

**Alert:** [Original alert message]
**Affected Model:** [Model name and URN]
**Root Cause:** [Clear description of what changed and why it broke the model]
**Evidence:** [Schema diff showing the change]
**Remediation Actions Taken:**
1. [Tag added to DataHub]
2. [Fix script generated]
**Recommended Next Steps:** [What the data team should do]
```

## Critical Rules
- NEVER guess the root cause without checking lineage and schema history first.
- ALWAYS write back findings to DataHub so the next person/agent has context.
- Be specific: mention exact column names, data types, and timestamps.
"""

# -----------------------------------------------------------------------------
# ALERT TEMPLATE: Simulates the incoming alert that triggers the agent
# -----------------------------------------------------------------------------
ALERT_TEMPLATE = """🚨 **PRODUCTION ALERT — ML Model Accuracy Degradation**

**Model:** {model_name}
**Metric:** Fraud Detection Accuracy
**Current:** {current_accuracy}% (was {baseline_accuracy}%)
**False Positive Rate:** {false_positive_rate}% (was {baseline_fpr}%)
**Severity:** CRITICAL
**Time Detected:** {timestamp}

**Impact:** Legitimate transactions are being blocked at {blocked_rate}x the normal rate. 
Customer complaints have spiked. Revenue loss estimated at ${revenue_loss}/hour.

**Action Required:** Investigate root cause and protect downstream systems.
"""

# -----------------------------------------------------------------------------
# ROOT CAUSE REPORT TEMPLATE: Structure for the agent's final output
# -----------------------------------------------------------------------------
ROOT_CAUSE_REPORT_TEMPLATE = """## 🔍 Root Cause Investigation Report

### Alert Summary
{alert_summary}

### Affected ML Model
- **Name:** {model_name}
- **URN:** {model_urn}
- **Platform:** {platform}

### Lineage Trace (Upstream Dependencies)
{lineage_trace}

### Schema Drift Detected ⚠️
**Dataset:** {affected_dataset}
**Column:** `{affected_column}`
**Previous Type:** `{previous_type}`
**Current Type:** `{current_type}`
**Change Detected At:** {change_timestamp}

### Root Cause Analysis
{root_cause_analysis}

### Remediation Actions Executed
1. ✅ Added tag `[DATA-INCIDENT: DO NOT USE]` to `{model_name}` in DataHub
2. ✅ Generated SQL fix script: `{fix_script_path}`
3. ✅ Incident report written back to DataHub metadata

### Recommended Next Steps
{recommendations}
"""

# -----------------------------------------------------------------------------
# DEMO: Default values for the demonstration scenario
# -----------------------------------------------------------------------------
DEMO_ALERT_VALUES = {
    "model_name": "Fraud_Detection_ML_Model",
    "current_accuracy": "42.3",
    "baseline_accuracy": "97.8",
    "false_positive_rate": "78.5",
    "baseline_fpr": "2.1",
    "timestamp": "2026-07-11T14:30:00+07:00",
    "blocked_rate": "37",
    "revenue_loss": "125,000",
}

# =============================================================================
# SPEAKER NOTES (Catatan Tambahan untuk Pembicara)
# =============================================================================
# Saat presentasi, jelaskan bahwa prompt ini mengikuti pola "ReAct" 
# (Reasoning + Acting) — AI tidak hanya menjawab, tetapi:
# 1. BERPIKIR (Reasoning): "Model accuracy turun, saya harus cek lineage dulu"
# 2. BERTINDAK (Acting): Memanggil get_lineage(), get_schema_history()
# 3. MENGAMATI (Observing): Melihat hasil dari DataHub
# 4. BERTINDAK LAGI: Menulis tag peringatan ke DataHub
#
# Ini menunjukkan kepada juri bahwa AI Agent kita TIDAK menebak-nebak,
# melainkan mengikuti protokol investigasi yang terstruktur menggunakan
# konteks nyata dari DataHub.
# =============================================================================
