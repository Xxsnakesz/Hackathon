# 🤖 AI Data Reliability Agent

> **DataHub Agent Hackathon 2026** — Category: Production ML Agents

**In one sentence:** an autonomous agent that watches your production ML pipelines through DataHub, catches when someone accidentally breaks the data feeding those models, and posts an incident report back into DataHub so the next person (or agent) knows what happened.

---

## 🎯 The Problem (In Plain English)

Imagine a bank uses a fraud detection AI model. The AI's job is to look at each transaction and say "this looks fine" or "this looks like fraud." The AI learned to do this from a specific set of columns in a database — things like `amount`, `sender balance`, `is_fraud`, and so on.

Now imagine a database engineer, working on an unrelated task, accidentally changes the `amount` column from a **number** to a **piece of text**. They didn't mean any harm — they were doing an ETL refactor and made a small mistake during migration.

Here's the scary part: **the AI doesn't crash.** It keeps running. But behind the scenes, every mathematical operation involving `amount` silently produces garbage:

| What should happen | What actually happens |
|---|---|
| Fraud detection accuracy: 97.8% | Accuracy plummets — model can't tell fraud from legit |
| False positive rate: 2.1% | Rate spikes — real customers get their cards blocked |
| Business impact | Estimated $125,000 lost per hour of undetected drift |

By the time anyone notices, hours or days have passed. Then engineers spend the rest of the day trying to figure out **which upstream table caused it**, working through spreadsheets and Slack messages.

**This project fixes that.**

---

## ✨ What This Project Does

An AI agent that runs in the background and does five things automatically:

1. **Watches** the actual production databases for schema changes (not summaries — the real thing).
2. **Detects** the moment a change happens, whether it's meaningful (e.g., number → text) or harmless.
3. **Traces** the change through DataHub's lineage graph to figure out which ML models are affected downstream.
4. **Calculates** the real-world impact — literally counts how many rows are affected, whether the data can be salvaged, and what breaks.
5. **Reports back into DataHub** by attaching an incident tag to the affected ML model so the next person (or downstream tool) sees the warning immediately. It also generates ready-to-use SQL/dbt fix scripts.

All this shows up in a live web dashboard so a human can watch it happen.

---

## 🎬 Try the Demo (Step by Step)

Once the app is running (see [Quick Start](#-quick-start) below), you'll see a dashboard with 5 tabs. Here's the demo flow, told like a story:

### Act 1 — Everything looks normal

Open the **📊 Schema Monitor** tab. Every column is green ✅. The AI pipeline is healthy.

### Act 2 — Something breaks

Open the **💥 Simulate Incident** tab and pick **Scenario A: "ETL Refactor Gone Wrong."** This simulates an engineer accidentally changing the `amount` column from a number to text. Type in your name as "engineer" and click **Apply**.

Behind the scenes, this actually runs `ALTER TABLE` on a real PostgreSQL database and logs who did it in an audit trail.

### Act 3 — The agent notices

Go back to **📊 Schema Monitor**. The `amount` column is now red ❌. The system spotted the change in real time.

Open **📋 Change History**. You can see exactly what happened, when, and who did it.

### Act 4 — The agent investigates

Go to **🔍 Run Investigation** and click the big button.

The agent:
- Reads the audit trail
- Traces which ML model depends on this data (through DataHub's lineage graph)
- Counts how many transactions are affected (from real data — 50,000 rows)
- Attaches a **🚨 DATA-INCIDENT: DO NOT USE** tag to the ML model in DataHub
- Generates a downloadable SQL fix

### Act 5 — Proof in DataHub

Open DataHub's own web UI (usually at `http://<your-server>:9002`). Search for the fraud model. You'll see the **DATA-INCIDENT** tag now attached — the agent posted it there. Anyone else on the team who checks the model in DataHub will see the warning immediately.

### Act 6 — Cleanup

Back in the Streamlit app, click **Reset ALL to Baseline** to restore the schema. Everything goes back to green.

---

## 🚀 Quick Start

**You'll need:**
- Docker & Docker Compose
- Python 3.10+
- ~8 GB RAM available (DataHub is a big platform)

**Steps:**

```bash
# 1. Clone the repo
git clone https://github.com/Xxsnakesz/Hackathon.git
cd Hackathon

# 2. Copy the environment template (defaults work out of the box)
cp .env.example .env

# 3. Start everything (Postgres + full DataHub stack)
docker compose up -d

# 4. Wait ~5 minutes for DataHub to boot, then install Python deps
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 5. Load sample data and register the demo pipeline in DataHub
python3 src/agent/data_seeder.py
python3 metadata/lineage_bootstrap.py

# 6. Launch the dashboard
streamlit run src/app/streamlit_app.py --server.port 8501 --server.address 0.0.0.0
```

Then open two browser tabs:
- `http://<your-server>:8501` — the agent's dashboard
- `http://<your-server>:9002` — DataHub's UI (login `datahub`/`datahub`)

---

## 🎥 Demo Video

_[3-minute walkthrough — link will be added here]_

---

## 🕸️ Why This Isn't Just for PaySim

The agent isn't hardcoded to any specific pipeline. On startup, it looks at DataHub and asks:

> *"Which ML models here are marked as production? For each one, which database tables feed into it? For each table, which columns are marked as important?"*

It then monitors **whatever it finds**. If your team registers a completely different pipeline in DataHub tomorrow — a churn prediction model, a recommendation system, whatever — the agent will start protecting it automatically. Just click **Rediscover from DataHub** in the sidebar.

That's the whole design idea: DataHub is the source of truth about *what to protect*, and the agent is the muscle that *actually protects it*.

For this demo we use the PaySim fintech transaction dataset, but that's just a stand-in.

---

## 🏗️ How This Uses DataHub (Reading + Writing)

Most projects use DataHub as a passive catalog — they read from it. This project also **writes back**, which is what turns it from a viewer into an agent.

**What it reads from DataHub:**

| DataHub aspect | Why the agent reads it |
|---|---|
| `schemaMetadata` | To know what the "correct" schema of each source table looks like |
| `globalTags` | To decide which ML models are production-critical, and which columns are business-critical |
| `ownership` | To identify who to notify when their data breaks |
| `upstreamLineage` | To trace which tables feed which models (multi-hop) |
| `datasetProperties` | To get human-readable names and descriptions |
| `editableSchemaMetadata` | To find field-level tags like "PII" or "critical-feature" |

**What it writes back to DataHub:**

| DataHub aspect | What the agent writes |
|---|---|
| `globalTags` | `DATA-INCIDENT: DO NOT USE` tag on every affected ML model |
| `globalTags` | `SCHEMA-DRIFT-DETECTED` tag on every affected source table |
| `datasetProperties` | Full incident description with what drifted, when, and by whom |

All writes go through DataHub's official Python SDK (`DatahubRestEmitter`) as MetadataChangeProposal (MCP) events — the same mechanism DataHub itself uses internally.

---

## 🏆 What Makes This Original

DataHub already does a lot out of the box. Here's what this project adds that DataHub itself doesn't provide:

- **Automatic pipeline discovery** — no config file needed, works on any DataHub instance with registered ML pipelines
- **Context-based severity** — the agent decides "how bad is this drift?" using tags, lineage position, and glossary descriptions from DataHub, not a hardcoded list
- **Audit trail with human context** — a `schema_change_log` table records not just *what* changed but *who* changed it and *why*
- **Real impact numbers, not estimates** — the agent runs actual `COUNT` queries to say "37,204 rows are now corrupted, 4.2% can be salvaged"
- **Auto-generated fixes** — downloadable SQL `ALTER TABLE` scripts and dbt CAST patches ready to review and merge
- **Multi-model aware** — one drift can affect several downstream ML models; the agent tags every one of them

---

## 🏆 Judging Criteria Alignment

| Criterion | How this project addresses it |
|---|---|
| **Use of DataHub** | Reads 6 DataHub aspects to build its map of what to protect; writes back 2 aspects to report incidents. Full round-trip — DataHub feeds decisions in, agent posts results back out. |
| **Technical Execution** | End-to-end working on real infrastructure — real PostgreSQL introspection, real DataHub API calls, real MCP write-back via SDK. Graceful fallback when services aren't reachable. |
| **Originality** | Everything listed in the "What Makes This Original" section above — none of it exists in DataHub out of the box. |
| **Real-World Usefulness** | Silent ML failures from schema drift are a daily pain point in MLOps. This project addresses it with zero configuration — just register the pipeline in DataHub and the agent starts protecting it. |
| **Submission Quality** | Streamlit dashboard for visual demo, this README explains everything, sample outputs in `examples/` folder so judges can review artifacts without running the code. Apache-2.0 licensed. |
| **Bonus: Open-Source Contribution** | `datahub_skills/schema_drift_detector.yaml` defines a reusable DataHub Agent skill spec, prepared for future upstream contribution to `datahub-project/datahub`. |

---

## 📁 Project Structure

```
├── README.md                             # This file
├── LICENSE                               # Apache 2.0
├── docker-compose.yml                    # PostgreSQL + full DataHub stack
├── requirements.txt                      # Python dependencies
├── .env.example                          # Configuration template
│
├── data/
│   └── sample_transactions.csv           # PaySim demo dataset (~50k rows)
│
├── sql/
│   ├── init.sql                          # Sets up tables and audit log
│   ├── seed_data.sql                     # Indexes and health views
│   └── drift_scenarios.sql               # Reference SQL for drift simulations
│
├── metadata/
│   ├── lineage_bootstrap.py              # Registers demo pipeline in DataHub
│   ├── simulate_schema_drift.py          # Alternative drift trigger
│   └── ingest_postgres.yaml              # DataHub auto-ingestion config
│
├── datahub_skills/
│   └── schema_drift_detector.yaml        # Reusable skill spec (bonus contribution)
│
├── src/
│   ├── agent/
│   │   ├── pipeline_discovery.py         # ★ Reads DataHub to discover what to protect
│   │   ├── reliability_agent.py          # ★ The main agent — investigates and remediates
│   │   ├── datahub_gms_client.py         # DataHub API client (read + write)
│   │   ├── db_inspector.py               # PostgreSQL schema inspection + audit trail
│   │   ├── data_seeder.py                # Loads sample data
│   │   ├── fix_generator.py              # Generates SQL/dbt fix scripts
│   │   └── prompts.py                    # Alert and report templates
│   │
│   └── app/
│       └── streamlit_app.py              # ★ The web dashboard
│
└── examples/
    ├── generated_fix_amount.sql          # Sample generated SQL fix
    ├── generated_dbt_fix_amount.sql      # Sample generated dbt patch
    └── investigation_results.json        # Sample investigation output
```

---

## 🧱 Under the Hood (For the Technically Curious)

If you'd like the architectural picture:

```
                    ┌─────────────────────────────┐
                    │  PostgreSQL (Ground Truth)  │
                    │   Production data + audit   │
                    └──────────┬──────────────────┘
                               │ query for real state
                               ▼
┌──────────────────┐    ┌──────────────────────┐
│    DataHub GMS   │◄──►│  Reliability Agent   │
│  (metadata graph)│    │  (Python module)     │
└──────────────────┘    └──────────┬───────────┘
   ▲   │                           │
   │   │ reads aspects for context │
   │   │                           ▼
   │   │           ┌─────────────────────────┐
   │   │           │  Streamlit Dashboard    │
   │   │           │  (control plane)        │
   │   │           └─────────────────────────┘
   │   │
   │   │ writes DATA-INCIDENT tag +
   │   │ incident description via
   │   │ DatahubRestEmitter MCP
   │   ▼
   └───────────
```

**Docker services (all part of the DataHub reference architecture):**

| Service | Role |
|---|---|
| `datahub-gms` | Metadata REST API (the agent talks to this) |
| `datahub-frontend` | DataHub's web UI |
| `datahub-mysql` | Storage for metadata |
| `datahub-elasticsearch` | Search + graph index |
| `datahub-broker` (Kafka) | Async event pipeline for metadata changes |
| `datahub-zookeeper` | Kafka coordination |
| `datahub-schema-registry` | Schema validation for Kafka messages |
| `datahub-actions` | Event processor for DataHub agent skills |
| Various `*-setup` and `datahub-upgrade` | Run once at startup to create indices/schemas |
| `paysim-postgres` | Application database with the sample data |

This is not overkill — it's the minimum DataHub itself requires for production-grade operation. The point is that the agent runs against **real DataHub**, not a mock.

---

## 📄 License

Apache License 2.0 — see [LICENSE](LICENSE).

---

## 🙏 Credits

Built for the **DataHub Agent Hackathon 2026**. Uses the [PaySim synthetic fintech dataset](https://www.kaggle.com/datasets/ealaxi/paysim1) as the demo pipeline.
