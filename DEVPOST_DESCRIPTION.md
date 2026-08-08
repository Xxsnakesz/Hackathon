# Devpost Submission — Text Description

*Draft for the Devpost project description field. Trim/adjust to fit their
character limit if needed — this is written to work standalone even if
Devpost truncates it, most important info front-loaded.*

---

## What it does

**AI Data Reliability Agent** protects production ML models from silent
schema drift — the class of bug where a database column quietly changes
type (e.g. `DOUBLE PRECISION` → `VARCHAR`), the model doesn't crash, but
every prediction downstream gets silently corrupted for hours before anyone
notices.

A 5-agent team (Detector → RootCauseAnalyst → ImpactAssessor ‖ FixAuthor →
Reviewer), orchestrated as a real **LangGraph state machine**, investigates
end-to-end: it detects the drift, traces DataHub's lineage graph to find
every affected ML model, quantifies real business impact from live
row-level queries, selects a remediation strategy from a vetted playbook,
and — only after a safety-gated Reviewer approves — writes a first-class
**Incident** back into DataHub so the next engineer (or agent) inherits the
finding instead of starting from zero.

## How it uses DataHub

DataHub is the operational backbone, not a passive catalog:

- **Dynamic pipeline discovery (read)** — on startup the agent searches
  DataHub for production ML models by platform + tag, then walks the
  lineage graph upstream (multi-hop) to find every real source table. No
  pipeline is hardcoded; register a new model + lineage in DataHub and the
  agent starts protecting it automatically.
- **Context-derived severity (read)** — per-column criticality comes from
  DataHub tags (`ml-target`, `critical-feature`, `pii`), lineage position,
  and glossary descriptions, not a hardcoded list.
- **Raw-vs-derived table detection (read)** — the agent checks each source
  table's own upstream lineage to tell a true raw table apart from a
  derived view sitting on top of it, so drift detection and tagging never
  target the wrong node in the pipeline.
- **Native Incident write-back (write)** — on approval, the agent creates a
  first-class DataHub `Incident` entity (`incidentInfo` aspect) associated
  with the affected model — a real Active/Resolved status banner, not just
  a tag — with a graceful fallback for older DataHub deployments.
- **Tag write-back (write)** — `DATA-INCIDENT: DO NOT USE` on every
  impacted model, `SCHEMA-DRIFT-DETECTED` on the true raw source table.
- Reads 6 aspects total (`schemaMetadata`, `globalTags`, `ownership`,
  `editableSchemaMetadata`, `upstreamLineage`, `datasetProperties`); writes
  back 3 (`globalTags`, `datasetProperties`, `incidentInfo`) — a full
  round-trip through DataHub's official Python SDK (`DatahubRestEmitter`)
  as real MetadataChangeProposal events.

## The tech behind it

- **LangGraph** — the investigation is a compiled `StateGraph`: a real
  fan-out (`RootCauseAnalyst → ImpactAssessor ‖ FixAuthor` in parallel), a
  join, and a conditional review loop (`Reviewer → FixAuthor` on rejection,
  bounded iterations) — not a linear script dressed up as "agents."
- **Pure LLM reasoning, deterministic execution** — every agent's analysis,
  and the Reviewer's final verdict, is genuine LLM output (OpenAI by
  default — including OpenAI-compatible gateways via `OPENAI_BASE_URL` — or
  Anthropic). But the LLM never writes SQL freehand: FixAuthor selects from
  a fixed set of vetted remediation templates (cast-back for a type change,
  re-add-with-data-loss-warning for a dropped column, accept-as-baseline for
  a new additive column) based on the real `drift_type`, and a static safety
  validator gates the Reviewer — the LLM can be *stricter* than the
  validator, never more lenient. Tools (schema scans, lineage reads, SQL
  generation, safety checks) always run as ordinary, testable Python code.
- **Real infrastructure** — PostgreSQL 15 (real `information_schema`
  introspection, a genuine audit trail table), the full DataHub reference
  stack (GMS, MySQL, Elasticsearch, Kafka) via Docker Compose, Streamlit for
  the live control-plane UI with a streaming view of every agent's reasoning
  and tool calls as they happen.
- Graceful fallback throughout: if DataHub GMS or Postgres aren't reachable,
  the agent switches to an in-memory catalog with the same logic, clearly
  logged as `[GMS: FALLBACK]` — the demo works end-to-end either way.

---

## Shorter version (if there's a strict character limit)

An AI agent that protects production ML models from silent schema drift —
when a column's type quietly changes and a model keeps running but produces
corrupted predictions with no crash to alert anyone. A 5-agent team,
orchestrated as a real LangGraph state machine, investigates end-to-end:
detects the drift, traces DataHub's lineage graph to find every affected
model, quantifies real business impact from live database queries, selects
a remediation strategy from a vetted safety-checked playbook, and — only
after a Reviewer agent approves — writes a first-class Incident back into
DataHub. DataHub is the operational backbone: the agent auto-discovers
pipelines from its lineage graph (nothing hardcoded), derives column
criticality from its tags and glossary, and round-trips real findings back
via native Incident entities and tags through the official SDK. Every
agent's reasoning is genuine LLM output (OpenAI/Anthropic), but the LLM
never writes SQL freehand — it selects from vetted templates gated by a
static safety validator the LLM cannot override to be more lenient. Built
on PostgreSQL, the full DataHub stack, LangGraph, and Streamlit.
