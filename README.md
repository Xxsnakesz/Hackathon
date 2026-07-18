# 🤖 AI Data Reliability Agent

### The bug that doesn't crash your model — it just makes it wrong, silently, for hours.

> **DataHub Agent Hackathon 2026** — Category: Production ML Agents

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB)](requirements.txt)
[![Built on DataHub](https://img.shields.io/badge/Built%20on-DataHub-1890FF)](https://datahubproject.io)
[![LangGraph Multi-Agent](https://img.shields.io/badge/Orchestration-LangGraph-6E56CF)](src/agent/multi_agent/orchestrator.py)
[![Pure LLM Reasoning](https://img.shields.io/badge/Reasoning-Pure%20LLM-C4B5FD)](src/agent/multi_agent/llm.py)

**In one sentence:** a 5-agent LangGraph team that reads DataHub's lineage graph to know exactly what to protect, catches the schema change that silently poisons your fraud model, reasons about root cause and business impact like a senior SRE, picks a repair strategy from a vetted playbook, votes on whether it's safe to ship — and only then writes the incident back into DataHub so the next person (or agent) inherits the knowledge.

**60-second pitch for judges:**

| If you only read one row per section, read these | |
|---|---|
| 🎯 **The problem** | A schema drift model-poisoning bug is invisible — no stack trace, no crash, just a model quietly making worse decisions. Teams find out from a business metric days later. |
| 🧠 **The idea** | DataHub already knows the whole pipeline graph (what feeds what, who owns it, what's critical). This agent turns that graph into an active immune system instead of a passive catalog — read the graph, reason over it, act on it, write the outcome back. |
| ⚙️ **The technical bet** | Not "an LLM wrapper around SQL." A **LangGraph state machine**, 5 specialised agents with hard tool-permission boundaries, a safety gate that can *veto* the LLM's own output, and a fixed playbook of remediation strategies the LLM selects from — never freehand SQL. |
| 🔁 **The DataHub round-trip** | Reads 6 aspects to build its map, writes back a first-class native `Incident` entity (not just a tag) — gated so nothing reaches DataHub until a Reviewer agent signs off. |
| 🏁 **Try it in one click** | Streamlit tab **🧠 Multi-Agent Team** → *Dispatch Team* → watch 5 agents reason live, in order, with a visible safety veto if the fix is unsafe. |

**Jump to:** [The Problem](#-the-problem-in-plain-english) · [What It Does](#-what-this-project-does) · [Demo Walkthrough](#-try-the-demo-step-by-step) · [Quick Start](#-quick-start) · [DataHub Read/Write](#-how-this-uses-datahub-reading-writing) · [What's Original](#-what-makes-this-original) · [Multi-Agent Team](#-multi-agent-investigation-team) · [Why the LLM Doesn't Write SQL](#-why-the-llm-doesnt-generate-sql-directly) · [Judging Criteria](#-judging-criteria-alignment)

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

The same investigation is *also* available as a **5-agent team** (Detector → RootCauseAnalyst → ImpactAssessor ‖ FixAuthor → Reviewer) with a **safety gate**: DataHub only gets tagged after the Reviewer approves the generated fix. See [Multi-Agent Team](#-multi-agent-investigation-team) below.

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

### Act 4 — The agent team investigates

Open the **🧠 Multi-Agent Team** tab and click **Dispatch Team**. Watch five specialised agents take turns:

1. **🔍 Detector** scans the schema and reads the audit log.
2. **🕵️ RootCauseAnalyst** traces the DataHub lineage and pulls owner info.
3. **📊 ImpactAssessor** counts corrupted rows and estimates hourly business exposure.
4. **🔧 FixAuthor** generates the SQL/dbt fixes.
5. **🛡️ Reviewer** statically validates the fix — and if it's unsafe (e.g. contains `DROP TABLE`) rejects it, so FixAuthor tries again.

Only after the Reviewer approves does the orchestrator attach the **🚨 DATA-INCIDENT: DO NOT USE** tag to the impacted ML model in DataHub. This is the safety-gate pattern data platform teams actually want in production.

### Act 5 — Proof in DataHub

Open DataHub's own web UI (usually at `http://<your-server>:9002`). Search for the fraud model. You'll see the **DATA-INCIDENT** tag now attached — the agent team posted it there. Anyone else on the team who checks the model in DataHub will see the warning immediately.

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
| `globalTags` | `SCHEMA-DRIFT-DETECTED` tag on the **raw source table only** — never on a derived view sitting between it and the model (see below) |
| `incidentInfo` (native `Incident` entity) | A first-class DataHub Incident — Active status banner, priority, associated to the impacted model — not just a tag. Falls back to a `datasetProperties.customProperties` note if the installed SDK/GMS predates the Incident entity, so the agent still works end-to-end against older DataHub deployments |

All writes go through DataHub's official Python SDK (`DatahubRestEmitter`) as MetadataChangeProposal (MCP) events — the same mechanism DataHub itself uses internally.

**Correctness details that matter to anyone auditing the write-back path:**
- `datasetProperties` is a full-aspect MCP — writing it **replaces the whole aspect**, not merges. The agent always reads the entity's existing `name` first and preserves it; earlier versions of this write path accidentally overwrote the model's display name with the incident tag text. Fixed and covered by a regression test.
- A DataHub source table can appear in an ML model's upstream lineage two ways: as the true raw table, or as a **derived view** built on top of that raw table (e.g. a `feature_engineering_table` VIEW). During pipeline discovery, the agent checks each source table's *own* upstream lineage — if it has further lineage on a source platform, it's derived and is excluded from `monitored_table_names` (schema-drift scanning and the `SCHEMA-DRIFT-DETECTED` tag only ever target the true raw table). Derived tables still show up in the lineage view for context, just not as an independent monitoring target.

---

## 🏆 What Makes This Original

DataHub already does a lot out of the box. Here's what this project adds that DataHub itself doesn't provide:

- **Automatic pipeline discovery** — no config file needed, works on any DataHub instance with registered ML pipelines
- **Context-based severity** — the agent decides "how bad is this drift?" using tags, lineage position, and glossary descriptions from DataHub, not a hardcoded list
- **Audit trail with human context** — a `schema_change_log` table records not just *what* changed but *who* changed it and *why*
- **Real impact numbers, not estimates** — the agent runs actual `COUNT` queries to say "37,204 rows are now corrupted, 4.2% can be salvaged"
- **Auto-generated fixes** — downloadable SQL `ALTER TABLE` scripts and dbt CAST patches ready to review and merge
- **Multi-model aware** — one drift can affect several downstream ML models; the agent tags every one of them
- **Multi-agent team with safety gate** — the same investigation can be run as a five-agent team where the Reviewer can *veto* an unsafe fix and force a revision, and DataHub write-back is only allowed after approval (details below)
- **Prompt-injection resilient by design** — untrusted free-text fields (audit-log reasons, DataHub descriptions) inform LLM reasoning but never reach the SQL generation path; a static safety validator gates the Reviewer, so no `DROP`/`TRUNCATE`/unbounded `DELETE` can survive approval (see [Why the LLM Doesn't Generate SQL Directly](#-why-the-llm-doesnt-generate-sql-directly))

---

## 🧠 Multi-Agent Investigation Team

Beyond the single-agent 7-step investigation, the same flow is available as a **5-specialist team** with real handoffs, a review loop, and a safety gate before any DataHub write-back.

| # | Agent | Role | Tool set (enforced separation of concern) |
|---|---|---|---|
| 1 | 🔍 **Detector** | Scan schema + audit trail | `scan_schema`, `read_audit_log` |
| 2 | 🕵️ **RootCauseAnalyst** | Trace lineage & owners | `get_lineage`, `get_ownership` |
| 3 | 📊 **ImpactAssessor** | Quantify rows + $/hr | `compute_impact`, `get_table_stats`, `find_impacted_models`, `estimate_dollar_impact` |
| 4 | 🔧 **FixAuthor** | Select + justify a remediation strategy per drift, generate the matching script | `generate_fix_scripts` |
| 5 | 🛡️ **Reviewer** | Static safety gate | `validate_fix_safety` |

**FixAuthor never writes SQL freehand.** It picks from a fixed, vetted set of strategies based on `drift_type` and explains why each one fits — a type change gets cast back in place, but a *dropped* column can't be cast back (the data is gone), so it gets re-added with an explicit data-loss warning instead; a brand-new *additive* column isn't broken at all, so it gets accepted into the baseline rather than rolled back. Three different problems, three different templates, chosen by the LLM but always generated from vetted code — this is what keeps "the LLM decides" safe from hallucinated SQL.

**Handoff flow:**

```
Detector
   └─ (drift?) → RootCauseAnalyst
                     ├─ ImpactAssessor  ┐
                     └─ FixAuthor       ┘ (parallel)
                              └─ Reviewer
                                    ├─ APPROVE → DataHub write-back → DONE
                                    └─ REJECT  → back to FixAuthor (max 2 iterations)
```

**Built on LangGraph** — the orchestrator is a real `StateGraph`, not hand-rolled control flow. The fan-out (`analyst → assessor ‖ author`), the join (`→ reviewer`), and the review loop (`reviewer → author_revise → reviewer` on REJECT) are first-class graph edges. Click *📐 View LangGraph state machine* in the tab to see the compiled Mermaid diagram.

**What makes this a real multi-agent system, not prompt theatre:**

- **Enforced tool separation.** Detector *cannot* generate fixes; FixAuthor *cannot* tag DataHub. Each agent's `allowed_tools` list is enforced by the tool registry before every call.
- **The Reviewer can veto.** If a generated SQL contains `DROP TABLE`, `TRUNCATE`, `DELETE` without `WHERE`, missing `BEGIN`/`COMMIT`, or no verification step, the Reviewer returns `REJECT_UNSAFE` / `REJECT_INCOMPLETE` and the graph routes back to the FixAuthor with the rejection reason. Bounded by `max_review_iterations`.
- **Write-back is gated.** DataHub tagging only happens when the Reviewer approves. Rejected fixes exit through `writeback_skip → END` — they never touch the graph.
- **Pure LLM reasoning, provider-agnostic.** Every agent's analysis — and the Reviewer's final verdict — is genuine LLM output. Set `OPENAI_API_KEY` (default; supports `OPENAI_BASE_URL` gateways like SumoPod) or `ANTHROPIC_API_KEY`. **Tools run in code** — schema scans, lineage reads, and SQL validation execute deterministically, and the LLM reasons over that real output; it can never fabricate drift data, lineage, or SQL. A static safety validator additionally guarantees the LLM Reviewer can never approve a script flagged as unsafe.

**Try it:** open the Streamlit tab **🧠 Multi-Agent Team** and click *Dispatch Team*. Every agent's tool calls, reasoning, and the reviewer verdict stream live.

Reference implementations:
- Agents & tool separation: [`src/agent/multi_agent/agents.py`](src/agent/multi_agent/agents.py), [`src/agent/multi_agent/tools.py`](src/agent/multi_agent/tools.py)
- Orchestrator + review loop: [`src/agent/multi_agent/orchestrator.py`](src/agent/multi_agent/orchestrator.py)
- One YAML skill per agent for the DataHub Skills bonus criterion: [`datahub_skills/`](datahub_skills/) — `detector_agent.yaml`, `root_cause_analyst.yaml`, `impact_assessor.yaml`, `fix_author.yaml`, `reviewer_agent.yaml`, `multi_agent_orchestrator.yaml`

---

## 🛡️ Why the LLM Doesn't Generate SQL Directly

We deliberately split responsibilities between the LLM and the tool layer. This isn't a compromise or a limitation — it's the pattern that data platform teams actually deploy in production.

**The concern:** every LLM has a non-zero chance of hallucinating a destructive command — `DROP TABLE`, a forgotten `WHERE` clause, the wrong table name — especially when prompt context contains untrusted free text (audit-log reasons written by engineers, DataHub descriptions edited by data stewards, alert messages piped from monitoring systems). In a data reliability tool that literally exists to prevent silent failures, "the AI generated an unsafe fix and we ran it" is unacceptable.

**How this project handles it:**

| Concern | Naive LLM agent | This project |
|---|---|---|
| Who produces the SQL fix? | LLM emits SQL as a string in its response | `generate_fix_scripts` tool composes it from parameterized templates + real drift metadata |
| Prompt-injection surface | Every field in the prompt reaches the executable output path | Free-text fields (`change_reason`, `changed_by`) inform reasoning but never reach the SQL generation path |
| Destructive-command risk | LLM can decide `DROP TABLE` "solves" the drift | Static safety validator rejects `DROP`, `TRUNCATE`, unbounded `DELETE`, missing transaction wrapping — Reviewer *cannot* approve a script that failed validation |
| Reviewer role | Optional / cosmetic | Mandatory graph node with veto power; on `REJECT_UNSAFE` the graph routes back to FixAuthor with the rejection reason (bounded by `max_review_iterations`) |
| Write-back trigger | Whenever the LLM says so | Only after Reviewer verdict is `APPROVE`; rejected fixes exit through `writeback_skip → END` and never touch DataHub |

**What the LLM is used for**: reasoning about *context* — was this drift intentional? Which model is impacted? Does the generated fix semantically match the drift type? Are there any suspicious patterns the static validator missed? These are the tasks LLMs are genuinely good at. Producing executable code that touches production systems is the task they're genuinely dangerous at.

Reviewer output at runtime is rendered as an itemised checklist so you can *see* the guard rails firing — for example:

```
🛡️ Reviewer Analysis
  ✅ Static: transaction wrapped in BEGIN/COMMIT
  ✅ Static: no DROP / TRUNCATE / unbounded DELETE
  ✅ Static: USING clause safely casts VARCHAR → DOUBLE PRECISION
  ✅ LLM: semantic check — script only affects drifted column, no side effects
  ─────────────
  Final Verdict: APPROVE
```

This is a boring architecture on purpose. Boring is what survives production.

---

## 🏆 Judging Criteria Alignment

| Criterion | How this project addresses it |
|---|---|
| **Use of DataHub** | Reads 6 DataHub aspects to build its map of what to protect; writes back 2 aspects to report incidents. Full round-trip — DataHub feeds decisions in, agent posts results back out. |
| **Technical Execution** | End-to-end working on real infrastructure — real PostgreSQL introspection, real DataHub API calls, real MCP write-back via SDK. Graceful fallback when services aren't reachable. |
| **Originality** | Everything listed in the "What Makes This Original" section above — none of it exists in DataHub out of the box. |
| **Real-World Usefulness** | Silent ML failures from schema drift are a daily pain point in MLOps. This project addresses it with zero configuration — just register the pipeline in DataHub and the agent starts protecting it. |
| **Submission Quality** | Streamlit dashboard for visual demo, this README explains everything, sample outputs in `examples/` folder so judges can review artifacts without running the code. Apache-2.0 licensed. |
| **Bonus: Open-Source Contribution** | `datahub_skills/` contains **six** reusable DataHub Agent skill specs — one per team agent (`detector_agent.yaml`, `root_cause_analyst.yaml`, `impact_assessor.yaml`, `fix_author.yaml`, `reviewer_agent.yaml`) plus a `multi_agent_orchestrator.yaml` that captures the team handoff graph. Prepared for upstream contribution to `datahub-project/datahub`. |

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
│   ├── schema_drift_detector.yaml        # Original combined skill spec
│   ├── detector_agent.yaml               # Multi-agent team — one skill per role
│   ├── root_cause_analyst.yaml           # (bonus open-source contribution)
│   ├── impact_assessor.yaml
│   ├── fix_author.yaml
│   ├── reviewer_agent.yaml
│   └── multi_agent_orchestrator.yaml
│
├── src/
│   ├── agent/
│   │   ├── pipeline_discovery.py         # ★ Reads DataHub to discover what to protect
│   │   ├── datahub_gms_client.py         # DataHub API client (read + write)
│   │   ├── db_inspector.py               # PostgreSQL schema inspection + audit trail
│   │   ├── data_seeder.py                # Loads sample data
│   │   ├── fix_generator.py              # Generates SQL/dbt fix scripts
│   │   ├── prompts.py                    # Alert and report templates
│   │   │
│   │   └── multi_agent/                  # ★ 5-agent team with review loop
│   │       ├── agents.py                 #   Detector / Analyst / Assessor / Author / Reviewer
│   │       ├── tools.py                  #   Deterministic tool registry (enforced separation)
│   │       ├── orchestrator.py           #   Streaming coordinator + DataHub write-back gate
│   │       ├── llm.py                    #   Provider-agnostic LLM client (OpenAI/gateways/Anthropic)
│   │       └── types.py                  #   Events + session dataclasses
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

## 💬 The Pitch, One More Time

Most hackathon "AI agents" are a system prompt wrapped around an API call. Ask this one "why not just a Python script?" and the honest answer isn't defensive — it's the whole design: **a Python script can't read a commit-message-style audit reason and infer intent, can't synthesize a lineage graph plus ownership plus glossary into a business-impact sentence, and can't weigh which of three remediation strategies actually fits a given drift.** That's reasoning, not lookup — which is exactly the work delegated to the LLM here, and *only* that work. Everything that touches production — the schema scan, the lineage query, the SQL that gets written, the safety check that can veto it — runs as ordinary, auditable, testable code.

DataHub built the graph. This agent is what happens when something actually **uses** it — not to browse metadata, but to decide, act, and leave a trail the next person (or agent) can pick up.

---

## 📄 License

Apache License 2.0 — see [LICENSE](LICENSE).

---

## 🙏 Credits

Built for the **DataHub Agent Hackathon 2026**. Uses the [PaySim synthetic fintech dataset](https://www.kaggle.com/datasets/ealaxi/paysim1) as the demo pipeline.
