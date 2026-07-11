# 🤖 AI Data Reliability Agent — Fintech Fraud Detection

> **DataHub Agent Hackathon 2026** | Category: Production ML Agents / Agents That Do Real Work

An autonomous AI Agent that protects production ML models from silent data failures. When upstream schema changes break the Fraud Detection ML Model, the agent uses **DataHub's lineage graph and MCP Server** to trace the root cause, tag the affected model with `[DATA-INCIDENT: DO NOT USE]`, and generate SQL fix scripts — all in under 10 seconds.

---

## 🎯 Problem Statement

In FinTech companies, **Fraud Detection ML Models** depend on upstream data tables containing millions of financial transactions. When an upstream engineer silently changes a column type (e.g., `amount` from `DOUBLE` to `STRING`), the ML model doesn't crash — it silently produces **wrong predictions**:

- ❌ False Positive Rate spikes from 2.1% to 78.5%
- ❌ Legitimate transactions get blocked at 37x the normal rate
- ❌ Revenue loss: $125,000/hour
- ❌ No error messages — the model just "quietly breaks"

Without DataHub, it takes **hours or days** for MLOps teams to manually trace which upstream table changed.

## 💡 Our Solution

The **AI Data Reliability Agent** acts as an autonomous SRE that:

1. **Receives alerts** about ML model accuracy degradation
2. **Traces upstream lineage** via DataHub MCP Server to find all source tables
3. **Compares schema history** to detect column type changes (schema drift)
4. **Identifies the root cause** in seconds (e.g., `amount` changed from `DOUBLE` to `STRING`)
5. **Writes back to DataHub** — adds a `[DATA-INCIDENT: DO NOT USE]` tag to the ML model
6. **Generates SQL fix scripts** that engineers can apply immediately

```
paysim_raw_transactions (amount: DOUBLE → STRING ⚠️)
        │
        ▼ ETL / dbt
feature_engineering_table
        │
        ▼ Features
Fraud_Detection_ML_Model 🏷️ [DATA-INCIDENT: DO NOT USE]
```

---

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────┐
│                     DataHub Context Graph                 │
│  ┌─────────────┐  ┌───────────────┐  ┌───────────────┐  │
│  │ Raw Table   │──│ Feature Table │──│ ML Model      │  │
│  │ (Postgres)  │  │ (Derived)     │  │ (XGBoost)     │  │
│  └─────────────┘  └───────────────┘  └───────────────┘  │
│         ▲ Schema History    ▲ Lineage      ▲ Tags       │
└─────────┼───────────────────┼──────────────┼────────────┘
          │                   │              │
    ┌─────┴───────────────────┴──────────────┴─────┐
    │          AI Data Reliability Agent            │
    │  ┌──────────┐ ┌──────────┐ ┌──────────────┐  │
    │  │ Lineage  │ │ Schema   │ │ Write-Back   │  │
    │  │ Tracer   │ │ Analyzer │ │ + Fix Gen    │  │
    │  └──────────┘ └──────────┘ └──────────────┘  │
    └──────────────────────────────────────────────┘
```

### Tech Stack
- **DataHub** (v0.14.0) — Metadata engine, lineage graph, MCP Server
- **PostgreSQL** 15 — Database hosting PaySim transaction data
- **Python** 3.10+ — Agent logic, DataHub SDK integration
- **Docker Compose** — Full infrastructure orchestration
- **PaySim Dataset** — 6.3M synthetic financial transactions (Kaggle)

---

## 🚀 Quick Start

### Prerequisites
- Docker & Docker Compose
- Python 3.10+
- Git

### 1. Clone & Setup

```bash
git clone https://github.com/YOUR_USERNAME/ai-data-reliability-agent.git
cd ai-data-reliability-agent

# Copy environment configuration
cp .env.example .env
# Edit .env with your API keys (optional for simulation mode)
```

### 2. Start Infrastructure

```bash
# Start PostgreSQL + DataHub stack
docker-compose up -d

# Wait for DataHub to be ready (~2-3 minutes)
# Check: http://localhost:9002 (DataHub UI)
```

### 3. Bootstrap Metadata & Lineage

```bash
# Install Python dependencies
pip install -r requirements.txt

# Register schemas and lineage in DataHub
python metadata/lineage_bootstrap.py

# Verify in DataHub UI: you should see the lineage graph
```

### 4. Run the Demo

```bash
# Option A: Full orchestrated demo (recommended for video recording)
python src/demo/run_demo.py

# Option B: Run just the agent investigation
python -m src.agent.reliability_agent
```

### 5. Simulate Schema Drift (for manual testing)

```bash
# Simulate an upstream engineer changing the 'amount' column type
python metadata/simulate_schema_drift.py

# Then re-run the agent to see it detect and respond to the drift
python -m src.agent.reliability_agent
```

---

## 📁 Project Structure

```
├── README.md                      # This file
├── LICENSE                        # Apache 2.0 License
├── docker-compose.yml             # PostgreSQL + DataHub infrastructure
├── requirements.txt               # Python dependencies
├── .env.example                   # Environment configuration template
├── data/
│   └── sample_transactions.csv   # Subset of PaySim dataset (1000 rows)
├── sql/
│   └── init.sql                  # PostgreSQL schema initialization
├── metadata/
│   ├── lineage_bootstrap.py      # Register schemas & lineage in DataHub
│   └── simulate_schema_drift.py  # Simulate schema drift incident
├── src/
│   ├── agent/
│   │   ├── reliability_agent.py  # Core AI Agent engine
│   │   ├── prompts.py            # System prompts & reasoning templates
│   │   └── fix_generator.py      # SQL/dbt fix script generator
│   └── demo/
│       └── run_demo.py           # One-click demo orchestration
└── examples/
    ├── generated_fix_amount.sql  # Sample SQL fix output
    └── investigation_results.json # Sample investigation output
```

---

## 🎬 Demo Video

> 📺 [Watch the 3-minute demo on YouTube](YOUR_YOUTUBE_LINK)

The demo shows:
1. **Healthy State** — Pipeline running normally with 97.8% model accuracy
2. **Incident** — Schema drift: `amount` column changes from `DOUBLE` to `STRING`
3. **AI Investigation** — Agent traces lineage, detects drift, identifies root cause
4. **Resolution** — Agent adds `DATA-INCIDENT` tag to DataHub + generates fix script

---

## 🏆 DataHub Integration Highlights

| DataHub Feature | How We Use It |
| :--- | :--- |
| **Lineage Graph** | Agent traverses upstream lineage from ML model to find source tables |
| **Schema History** | Agent compares schema versions to detect column type changes |
| **MCP Server** | Agent uses DataHub skills (search, get_lineage, get_schema_history) |
| **Metadata Write-Back** | Agent adds `DATA-INCIDENT` tags and incident descriptions |
| **Dataset Registration** | Full schema registration for raw + feature tables |
| **ML Model Entity** | ML model registered with features, accuracy, training metadata |

---

## 📊 Dataset

This project uses the **PaySim Synthetic Financial Dataset** from Kaggle:
- **6.3 million** simulated mobile money transactions
- **11 columns**: step, type, amount, nameOrig, oldbalanceOrg, newbalanceOrig, nameDest, oldbalanceDest, newbalanceDest, isFraud, isFlaggedFraud
- **5 transaction types**: PAYMENT, TRANSFER, CASH_OUT, DEBIT, CASH_IN
- Realistic fraud patterns for ML model training

---

## 📄 License

This project is licensed under the **Apache License 2.0** — see the [LICENSE](LICENSE) file for details.

---

## 👥 Team

Built for the **DataHub Agent Hackathon 2026**.

---

*Built with ❤️ using DataHub, the open-source metadata platform that gives AI agents the context they need to do real work.*
