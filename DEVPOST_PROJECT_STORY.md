## Inspiration

Fraud detection models don't crash when their data breaks — they just get quietly worse. In FinTech, a model that scores every transaction depends on a handful of upstream Postgres columns. When an engineer refactors an ETL pipeline and accidentally changes `amount` from `DOUBLE PRECISION` to `VARCHAR`, nothing throws an exception. The model keeps running. It just starts blocking legitimate customers and letting fraud through, and nobody finds out until a business metric looks wrong days later.

We kept coming back to one observation: DataHub already knows the entire pipeline graph — which tables feed which models, who owns what, which columns are tagged critical. Most tools treat that graph as a catalog you browse. We wanted to see what happens if an agent actually *acts* on it: reads the graph to know what to protect, reasons about what broke, and writes the outcome back so the next engineer — or the next agent — inherits the finding instead of starting from a blank Slack thread.

## What it does

A five-agent team — Detector, RootCauseAnalyst, ImpactAssessor, FixAuthor, and Reviewer — investigates a schema drift incident end-to-end, orchestrated as a real **LangGraph state machine**, not a linear script wearing a multi-agent costume.

1. **Detector** scans the live Postgres schema against the baseline registered in DataHub and reads the audit trail.
2. **RootCauseAnalyst** walks DataHub's lineage graph upstream (multi-hop) to find every ML model actually affected, and pulls ownership context.
3. **ImpactAssessor** and **FixAuthor** run in parallel: one computes real business impact from live row-level queries, the other selects a remediation strategy from a vetted playbook based on exactly what kind of drift happened.
4. **Reviewer** statically validates the generated fix for unsafe SQL patterns, then asks an LLM for a final verdict — grounded in those findings, never able to be more lenient than the static check.
5. Only after approval does the agent write a first-class DataHub **Incident** back — a real Active/Resolved status entity, not just a tag — so anyone checking the model in DataHub sees the warning immediately.

Every agent's reasoning is genuine LLM output. None of the SQL that touches production is.

## How we built it

- **Discovery, not configuration.** On startup, the agent searches DataHub for production ML models by platform + tag, then traverses lineage upstream to find real source tables. Nothing is hardcoded to the demo pipeline — register a different model in DataHub, click Rediscover, and the agent starts protecting it.
- **LangGraph** compiles the five agents into a real graph: a fan-out edge (`RootCauseAnalyst → ImpactAssessor ‖ FixAuthor`), a join at `Reviewer`, and a conditional review loop that routes back to `FixAuthor` on rejection, bounded by a max-iteration count.
- **Enforced tool separation.** Each agent gets a whitelist of tools it's allowed to call, checked at the registry level — Detector cannot generate a fix, FixAuthor cannot write to DataHub. This is what makes the handoff real instead of cosmetic.
- **Three remediation strategies, not one template.** A type change gets cast back with `USING CAST`. A dropped column can't be cast back — the data is gone — so it gets re-added with an explicit data-loss warning instead. A brand-new additive column isn't broken at all, so the fix is registering it into the baseline, not rolling it back. The LLM picks and justifies the strategy; the SQL always comes from the matching vetted template.
- **Provider-agnostic LLM layer** — OpenAI by default (including OpenAI-compatible gateways via a configurable base URL) or Anthropic. No deterministic fallback: if no LLM is configured, the team refuses to run rather than silently degrading to templated text pretending to be reasoning.
- **Real infrastructure throughout** — PostgreSQL 15 with genuine `information_schema` introspection and an audit-trail table, the full DataHub reference stack via Docker Compose (GMS, MySQL, Elasticsearch, Kafka), and a Streamlit control plane that streams every agent's tool calls and reasoning live as they happen.

## Challenges we ran into

Most of our real challenges only showed up once we stopped testing against the happy path and started running the system against live infrastructure.

- **DataHub's `datasetProperties` write is a full-aspect replace, not a merge.** Our first incident-write implementation set the model's display name to the incident tag text — because writing that aspect without first reading the existing name silently overwrote it. Nothing in the SDK warns you about this; we only caught it by inspecting the actual DataHub UI after a write and noticing the model's name had changed.
- **Schema drift got tagged on the wrong node in the pipeline.** A derived view sitting between the raw table and the ML model was showing up as independently "drifted," because its inferred schema naturally differs from a separately registered baseline. We had to teach the discovery step to check each source table's *own* upstream lineage and exclude anything that isn't a true raw source from monitoring.
- **A single failed query could poison an entire session.** Postgres runs in `autocommit=False`; when one query fails without a rollback (for example, comparing a drifted `VARCHAR` column against an integer), every later query on that same cached connection fails identically until the process restarts — including completely unrelated reads. This produced a genuinely confusing symptom (a drift we'd just applied would "disappear" on the next investigation) that took real log-diving to trace back to a missing `rollback()`.
- **A GMS response shape we assumed wrong.** Our lineage client assumed `relationships[].entity` was a nested object; real DataHub GMS returns it as a plain URN string. Every lineage call was silently failing and falling back — invisible until we actually deployed against a live GMS instance and read the logs.
- **Migrating to a new VPS surfaced a Python version gap.** An f-string with an escaped quote inside its expression part is legal on Python 3.12+ but a hard `SyntaxError` on the Ubuntu-default Python — invisible in local development, a startup crash in production.

## Accomplishments that we're proud of

- A genuine DataHub read/write round-trip: six aspects read to build the protection map, three written back — including a first-class native Incident entity, with a documented fallback for older DataHub deployments.
- A safety invariant we can actually defend under questioning: the Reviewer's LLM verdict can be stricter than the static validator, but code guarantees it can never be more lenient — an "approve" over a validator-flagged unsafe script is structurally impossible, not just discouraged by a prompt.
- Catching and fixing real bugs in our own DataHub integration by reading actual GMS responses and actual UI output, instead of assuming the docs matched reality.
- A remediation system that tells the truth about what it can't do — a dropped column's historical data is gone, and the generated fix says so instead of pretending a cast can undo a `DROP COLUMN`.

## What we learned

- Treat any metadata write as a full-aspect replace until proven otherwise — read-before-write is the only way to avoid silently clobbering a field you never meant to touch.
- A lineage graph tells you what's connected, not what's structurally equivalent — a raw table and a view sitting on top of it both show up as "upstream," and you have to derive the distinction yourself if the platform doesn't hand it to you.
- The most valuable place to separate "the LLM decides" from "the code executes" isn't where it's most convenient — it's specifically around anything that can destroy data. Root-cause reasoning and business-impact framing are exactly what an LLM is good at; freehand SQL against production is exactly what it shouldn't be trusted with.
- Bugs that never appear in a demo scenario show up the moment you point the same code at real, slightly-different infrastructure. Every fix in this project's history came from actually running it, not from re-reading the code.

## What's next for AI Data Reliability Agent

- Turning the local DataHub Skill specs in `datahub_skills/` into an actual upstream contribution to `datahub-project/datahub`, rather than a repo-local artifact.
- A "shift-left" mode that evaluates a proposed schema migration against the lineage graph *before* it merges, not just after it's already broken something in production.
- Verifying the native Incident write-back against a wider range of real DataHub GMS versions, and extending the same safety-gated pattern to other classes of drift beyond column type/presence changes — renames, constraint changes, and partition-key drift.
- Notification integrations (Slack/Teams) so the Reviewer's verdict reaches the team that owns the affected model without anyone having to check a dashboard.
