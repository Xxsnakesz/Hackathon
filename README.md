# 🤖 AI Data Reliability Agent — FinTech Fraud Detection

> **DataHub Agent Hackathon 2026** | Category: Production ML Agents

An autonomous AI Agent that protects production Fraud Detection ML Models from silent data failures. When an upstream engineer accidentally changes a column type in PostgreSQL, the agent:

1. **Detects** the schema drift via real PostgreSQL introspection
2. **Traces lineage** upstream using the **DataHub GMS REST API**
3. **Writes back** a `[DATA-INCIDENT: DO NOT USE]` tag to the affected ML model via **DatahubRestEmitter MCP**
4. **Computes real impact** from actual transaction data (row-level COUNT queries)
5. **Generates SQL + dbt fix scripts** for immediate remediation

All of this is visualized in a real-time **Streamlit dashboard** with schema comparison, audit trail timeline, and drift simulation tools.

---

## 🎯 Problem: Silent ML Failures in FinTech

In FinTech companies, Fraud Detection ML Models depend on upstream PostgreSQL tables. When an engineer silently changes a column type — say, `amount` from `DOUBLE PRECISION` to `VARCHAR` during an ETL refactor — the model doesn't crash. It **silently produces wrong predictions**:

| Metric | Before Drift | After Drift |
|--------|---|---|
| Fraud Detection Accuracy | 97.8% | Degraded |
| False Positive Rate | 2.1% | Spiked |
| Legitimate Txns Blocked | Normal | 37× higher |

Without DataHub's lineage graph, it takes **hours or days** to trace which upstream table caused the degradation.

---

## 💡 Solution Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                      DataHub Context Graph                       │
│  ┌─────────────────┐  ┌──────────────────┐  ┌───────────────┐   │
│  │ paysim_raw_     │→ │ feature_         │→ │ Fraud_        │   │
│  │ transactions    │  │ engineering_     │  │ Detection_    │   │
│  │ (PostgreSQL)    │  │ table (VIEW)     │  │ ML_Model      │   │
│  └─────────────────┘  └──────────────────┘  └───────────────┘   │
│   ▲ Schema drift here      ▲ Lineage edge       ▲ Tag write-back │
└───┼────────────────────────┼────────────────────┼────────────────┘
    │                        │                    │
┌───┼────────────────────────┼────────────────────┼────────────────┐
│   │   AI Data Reliability Agent (v4)            │                │
│   │                                             │                │
│   ├─ DB Inspector → information_schema.columns  │                │
│   ├─ Schema Change Log → audit trail (who/when/why)             │
│   ├─ DataHub GMS Client → GET /relationships (lineage)          │
│   ├─ DatahubRestEmitter → emit GlobalTags MCP (write-back)      │
│   ├─ Impact Metrics → COUNT queries on 50k real transactions     │
│   └─ Fix Generator → SQL ALTER TABLE + dbt CAST patch           │
└───────────────────────────────────────────────────────────────────┘
```

---

## 🕸️ Dynamic Pipeline Discovery (Context-Based Agent)

The agent is **not hardcoded to PaySim**. On startup it reads the DataHub graph and discovers what to protect:

1. **Find protected ML models** — search all entities where `platform ∈ {mlflow, sagemaker, kubeflow, vertex-ai, tensorflow, pytorch}` **AND** `tag = production`. Both lists are configurable via env vars `ML_PLATFORMS` and `ML_TAG_FILTER`.
2. **Traverse lineage upstream** from each model (multi-hop) to collect every source table on `platform ∈ {postgres, mysql, snowflake, bigquery, redshift}` (configurable via `SOURCE_PLATFORMS`).
3. **Baseline schema per table** comes from DataHub's `schemaMetadata` aspect — the exact column names and types that were registered when the pipeline was set up.
4. **Per-column criticality is derived from context**, not hardcoded:

| Signal from DataHub | Result |
|---|---|
| Field tagged `ml-target` / `target-variable` | **CRITICAL** |
| Field tagged `critical-feature` / `ml-input` | **CRITICAL** |
| Numeric column on an active ML lineage path | **CRITICAL** |
| Field tagged `pii` | **HIGH** |
| Field has curated glossary description | **HIGH** |
| Everything else | MEDIUM |

The agent then monitors those tables via live PostgreSQL `information_schema.columns`, detects drift against the DataHub-registered baseline, and writes incident tags back to **every** ML model whose upstream lineage is affected — not just one.

**Zero configuration for a new pipeline.** Register any dataset + ML model + lineage in DataHub, click **Rediscover from DataHub** in the sidebar, and the agent starts protecting it. PaySim is included as an example pipeline you can swap out.

**If DataHub has no registered ML model, the agent refuses to boot** — it prints a message telling you to register a pipeline first. There's no silent fallback that would hide misconfiguration.

Reference implementation: [`src/agent/pipeline_discovery.py`](src/agent/pipeline_discovery.py).

---

## 🏗️ How DataHub is Used

This project uses DataHub **beyond reading metadata** — it actively writes back to the graph:

| DataHub Feature | Implementation | Code |
|---|---|---|
| **Pipeline Discovery (READ)** | Agent searches DataHub for ML models by platform + tag, then traverses lineage to collect source tables — **no hardcoded pipeline names** | `pipeline_discovery.py` |
| **Schema Metadata (READ)** | Agent reads `schemaMetadata` aspect to build baseline for each source table (columns + types) | `datahub_gms_client.py:get_schema_metadata` |
| **Ownership (READ)** | Agent reads `ownership` aspect for context ("who owns this drifted column") | `datahub_gms_client.py:get_ownership` |
| **Field-Level Tags (READ)** | Agent reads `editableSchemaMetadata` and `globalTags` on fields to derive per-column criticality | `datahub_gms_client.py:get_field_tags` |
| **Lineage Graph (READ)** | Multi-hop upstream traversal via `GET /relationships` to find every source table feeding each ML model | `datahub_gms_client.py:get_lineage` |
| **Entity Metadata (READ)** | `GET /entities/{urn}` to get model name, tags, description | `datahub_gms_client.py` |
| **MCP Write-Back (WRITE)** | Agent emits `GlobalTagsClass` MCP via `DatahubRestEmitter` SDK to tag **every impacted** ML model with `DATA-INCIDENT` | `datahub_gms_client.py:add_tag` |
| **Incident Emit (WRITE)** | Agent emits `DatasetPropertiesClass` MCP with incident description to each impacted model | `datahub_gms_client.py:emit_incident` |
| **Source Table Flag (WRITE)** | Source tables that drifted get `SCHEMA-DRIFT-DETECTED` tag | `reliability_agent.py` |
| **Schema Registration** | `lineage_bootstrap.py` registers demo pipeline (datasets, ML model, lineage, tags) via DataHub REST emitter — replaceable | `lineage_bootstrap.py` |
| **Auto-Ingestion** | `ingest_postgres.yaml` runs the DataHub PostgreSQL source connector with column profiling | `ingest_postgres.yaml` |
| **Custom Skill (Bonus)** | `schema_drift_detector.yaml` defines a reusable DataHub Agent skill for open-source contribution | `datahub_skills/` |

**Graceful Fallback**: When DataHub GMS is not running, all operations fall back to an in-memory catalog (same lineage graph, same logic). The agent clearly logs `[GMS: LIVE]` vs `[GMS: FALLBACK]` throughout the investigation.

---

## 📁 Project Structure

```
├── README.md                          # This file
├── LICENSE                            # Apache 2.0
├── docker-compose.yml                 # PostgreSQL 15 + full DataHub stack
├── requirements.txt                   # Python dependencies
├── .env.example                       # Environment config template
│
├── data/
│   └── sample_transactions.csv        # PaySim dataset subset (~50k rows, 9MB)
│
├── sql/
│   ├── init.sql                       # DB init: tables + schema_change_log + schema_baseline
│   ├── seed_data.sql                  # Index and materialized view creation
│   └── drift_scenarios.sql            # Preset drift functions (Scenario A/B/C + reset)
│
├── metadata/
│   ├── lineage_bootstrap.py           # Register datasets, ML model, lineage in DataHub
│   ├── simulate_schema_drift.py       # Emit drifted schema to DataHub (for DataHub UI demo)
│   └── ingest_postgres.yaml           # DataHub auto-ingestion config with column profiling
│
├── datahub_skills/
│   └── schema_drift_detector.yaml     # Reusable DataHub Agent skill (open-source contribution)
│
├── src/
│   ├── agent/
│   │   ├── datahub_gms_client.py      # ★ Real DataHub GMS REST client + write-back
│   │   ├── reliability_agent.py       # ★ Core AI Agent — 7-step investigation
│   │   ├── db_inspector.py            # Real PostgreSQL schema inspector + audit trail
│   │   ├── data_seeder.py             # Load PaySim CSV into PostgreSQL
│   │   ├── fix_generator.py           # SQL ALTER TABLE + dbt CAST patch generator
│   │   ├── prompts.py                 # Investigation prompts and alert templates
│   │   └── schema_baseline.json       # Baseline schema reference (for simulation mode)
│   │
│   └── app/
│       └── streamlit_app.py           # ★ Real-time monitoring dashboard (5 tabs)
│
└── examples/
    ├── generated_fix_amount.sql        # Sample generated SQL fix script
    ├── generated_dbt_fix_amount.sql    # Sample generated dbt patch
    └── investigation_results.json      # Sample investigation output
```

---

## 🚀 Quick Start

### Prerequisites
- Docker & Docker Compose
- Python 3.10+

### 1. Clone & Configure

```bash
git clone https://github.com/YOUR_USERNAME/ai-data-reliability-agent.git
cd ai-data-reliability-agent

cp .env.example .env
# Edit .env if needed (defaults work out of the box)
```

### 2. Start Infrastructure

```bash
# Start PostgreSQL + full DataHub stack (GMS, Frontend, Kafka, ES, MySQL)
docker-compose up -d

# PostgreSQL is ready in ~15 seconds
# DataHub UI is ready in ~3-5 minutes
# Check: http://localhost:9002  (DataHub UI — admin/admin)
# Check: http://localhost:8085/config  (DataHub GMS health)
```

### 3. Install Python Dependencies

```bash
pip install -r requirements.txt
```

### 4. Initialize Database & Register Metadata

```bash
# Initialize DB schema (tables, schema_change_log, schema_baseline)
# This happens automatically when Docker starts (via init.sql)

# Seed 50k real PaySim transactions into PostgreSQL
python src/agent/data_seeder.py

# Register datasets, ML model, and lineage in DataHub
python metadata/lineage_bootstrap.py

# (Optional) Run DataHub auto-ingestion with column profiling
datahub ingest -c metadata/ingest_postgres.yaml
```

### 5. Launch the Streamlit Dashboard

```bash
streamlit run src/app/streamlit_app.py --server.port 8501
# Open: http://localhost:8501
```

### 6. Run the Agent (CLI)

```bash
# Run full investigation from command line
python -m src.agent.reliability_agent

# Or with a specific DataHub GMS URL
python -m src.agent.reliability_agent --gms-url http://localhost:8085
```

---

## 🎬 Demo Walkthrough

### Full Demo Flow

1. **Launch** `streamlit run src/app/streamlit_app.py`
2. **Schema Monitor** tab → all columns green ✅
3. **Simulate Incident** tab → Choose Scenario A: "ETL Refactor Gone Wrong"
   - Enter engineer name (e.g. `bambang_engineer`) and reason
   - Click **Apply** → `ALTER TABLE paysim_raw_transactions ALTER COLUMN amount TYPE VARCHAR`
   - This also inserts into `schema_change_log` with timestamp, who, and why
4. **Schema Monitor** → `amount` column turns red ❌ (detected in real-time)
5. **Change History** tab → Timeline shows the change with full audit info
6. **Run Investigation** tab → Click "Run Investigation"
   - Agent reads audit log from DB
   - Traverses lineage from DataHub GMS (or fallback)
   - Computes impact from actual row counts
   - Writes `DATA-INCIDENT` tag back to DataHub
   - Generates downloadable SQL fix script
7. **Reset** → Click "Reset ALL to Baseline" → all columns green again

### Simulating Schema Drift in DataHub UI

```bash
# Emit drifted schema metadata to DataHub (visible in DataHub Schema tab)
python metadata/simulate_schema_drift.py

# Then run the agent to detect & respond
python -m src.agent.reliability_agent
```

---

## 📊 Dataset

**PaySim Synthetic Financial Dataset** (Kaggle):
- **6.3 million** simulated mobile money transactions (full CSV included)
- **11 columns**: step, type, amount, nameOrig, oldbalanceOrg, newbalanceOrig, nameDest, oldbalanceDest, newbalanceDest, isFraud, isFlaggedFraud
- ~0.13% fraud rate — highly imbalanced (realistic for FinTech)
- Demo uses a 50k-row sample for fast seeding

---

## 🔧 Key Technical Decisions

| Decision | Rationale |
|---|---|
| Real PostgreSQL introspection | Agent queries `information_schema.columns` directly — no hardcoded schema data |
| `schema_change_log` table | Every `ALTER TABLE` is recorded with who/when/why — provides real audit trail for investigation |
| DataHub GMS REST client | Agent makes real HTTP calls to GMS for lineage; graceful fallback if GMS not running |
| `DatahubRestEmitter` write-back | Tags are emitted as MCP (MetadataChangeProposalWrapper) via SDK — proper DataHub integration |
| Feature Engineering as PostgreSQL VIEW | Ensures schema consistency — if raw table drifts, VIEW computation fails predictably |
| Port 5433 (not 5432) | docker-compose maps host:5433 → container:5432 — external connections must use 5433 |

---

## 🏆 Judging Criteria Alignment

| Criterion | Our Approach |
|---|---|
| **Use of DataHub** | Reads **6 aspects** (`schemaMetadata`, `globalTags`, `ownership`, `editableSchemaMetadata`, `upstreamLineage`, `datasetProperties`) to build a context-based pipeline map. Writes back **2 aspects** (`globalTags`, `datasetProperties`) via `DatahubRestEmitter` MCP to every impacted model. Full round-trip: DataHub → agent decisions → DataHub. |
| **Technical Execution** | End-to-end working on real infrastructure: PostgreSQL introspection, DataHub GMS REST calls, MCP write-back via SDK, downloadable SQL/dbt fix scripts. Graceful fallback when GMS/DB down. Bug-free stack (v0.14.0.2). |
| **Originality** | Context-based severity from DataHub tags/glossary/lineage (not hardcoded); auto-discovered multi-model pipeline (works for any DataHub-registered ML pipeline); `schema_change_log` audit trail with who/why; real impact metrics from live COUNT queries; auto-generated remediation scripts. None of these exist in DataHub out of the box. |
| **Real-World Usefulness** | Silent ML failures from upstream schema drift = daily pain for MLOps teams. **Zero config**: point it at any DataHub instance with a registered ML pipeline, it works. PaySim is included as demo, not as constraint. |
| **Submission Quality** | Streamlit control plane with dynamic discovery UI, this README with architecture diagrams and criterion mapping, working end-to-end demo, `examples/` folder with sample generated artifacts. Apache-2.0 licensed. |
| **Bonus: Open-Source** | `datahub_skills/schema_drift_detector.yaml` — reusable DataHub Agent skill spec designed for upstream contribution to `datahub-project/datahub` |

---

## 📄 License

Apache License 2.0 — see [LICENSE](LICENSE)

---

*Built for the DataHub Agent Hackathon 2026. Uses DataHub as the operational backbone for lineage traversal and metadata write-back, not just as a passive catalog.*
