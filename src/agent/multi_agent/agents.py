"""
Five specialized agents.

Each agent:
  1. Emits an AGENT_START event.
  2. Runs its deterministic tool calls (via ToolRegistry) — the tools it is
     *allowed* to call are declared in `allowed_tools`, which enforces
     separation of concern between roles.
  3. Optionally asks Claude for a short narrative commentary (via the
     ClaudeNarrator). If Claude is unavailable, a deterministic template
     produces the same commentary from the raw findings.
  4. Writes its output back into the shared InvestigationSession.
  5. Emits AGENT_COMPLETE and returns an AgentOutput for the orchestrator.

The reviewer is special: it produces a ReviewVerdict that the orchestrator uses
to decide whether to loop back to the FixAuthor for a revision.
"""
from __future__ import annotations

import logging
from typing import Callable, Iterable, Optional

from src.agent.multi_agent.llm import ClaudeNarrator
from src.agent.multi_agent.tools import ToolRegistry, _bare_table_name
from src.agent.multi_agent.types import (
    AgentEvent,
    AgentOutput,
    EventKind,
    InvestigationSession,
    ReviewVerdict,
)

logger = logging.getLogger("MultiAgent.Agents")

EventEmitter = Callable[[AgentEvent], None]


class BaseAgent:
    name: str = "base"
    emoji: str = "🤖"
    role_summary: str = ""
    allowed_tools: tuple[str, ...] = ()

    def __init__(self, registry: ToolRegistry, narrator: ClaudeNarrator):
        self.registry = registry
        self.narrator = narrator

    # ── helpers ──────────────────────────────────────────────────────────
    def _emit(self, emitter: EventEmitter, kind: EventKind, text: str = "", **kw):
        emitter(AgentEvent(kind=kind, agent=self.name, emoji=self.emoji, text=text, **kw))

    def _tool(self, emitter: EventEmitter, name: str, *args, summary: str = "", **kwargs):
        if name not in self.allowed_tools:
            raise PermissionError(f"Agent {self.name} not allowed to call tool {name}")
        self._emit(
            emitter, EventKind.TOOL_CALL,
            text=f"→ {name}({', '.join(map(repr, args))})",
            tool=name, tool_args={"args": [repr(a) for a in args], "kwargs": kwargs},
        )
        result = self.registry.call(name, *args, **kwargs)
        self._emit(
            emitter, EventKind.TOOL_RESULT,
            text=summary or f"← {name} returned",
            tool=name, tool_result_summary=summary,
        )
        return result

    def _narrate(self, system: str, user: str, fallback: str) -> str:
        return self.narrator.narrate(system, user, fallback)

    # ── subclass contract ────────────────────────────────────────────────
    def run(self, session: InvestigationSession, emit: EventEmitter) -> AgentOutput:
        raise NotImplementedError


# ─────────────────────────────────────────────────────────────────────────────
# 1. DetectorAgent — the eyes
# ─────────────────────────────────────────────────────────────────────────────
class DetectorAgent(BaseAgent):
    name = "Detector"
    emoji = "🔍"
    role_summary = "Scans monitored tables for schema drift and reads the audit trail."
    allowed_tools = ("scan_schema", "read_audit_log")

    def run(self, session, emit):
        self._emit(emit, EventKind.AGENT_START, text="Scanning monitored tables for drift.")

        drift_report = self._tool(emit, "scan_schema",
                                  summary=f"Scanned tables, "
                                          f"found {0 if not None else ''}")
        n_tables = len(drift_report.get("tables", {}))
        n_drifts = drift_report.get("total_drifts", 0)
        n_crit = drift_report.get("critical_drifts", 0)
        session.drift_report = drift_report

        audit = self._tool(emit, "read_audit_log",
                           summary=f"Read {0} audit entries")
        session.audit_log = audit

        # Enrich each drift with the matching audit entry (who/when/why)
        for _, info in drift_report.get("tables", {}).items():
            for d in info.get("drifts", []):
                match = next(
                    (e for e in audit
                     if e.get("table_name") == d["table"]
                     and e.get("column_name", "").lower() == d["column"].lower()),
                    None,
                )
                d["changed_by"] = match.get("changed_by", "unknown") if match else "unknown"
                d["change_reason"] = match.get("change_reason", "N/A") if match else "N/A"
                d["log_timestamp"] = (
                    match.get("changed_at", d["detected_at"]) if match else d["detected_at"]
                )

        fallback = (
            f"Scanned {n_tables} table(s). "
            f"Detected {n_drifts} drift(s) — {n_crit} CRITICAL. "
            f"Cross-referenced with {len(audit)} recent audit entry/entries."
        )
        if session.llm_enabled:
            drift_lines = [
                f"- {d['table']}.{d['column']}: {d['baseline_type']} → {d['actual_type']} "
                f"[{d.get('severity','?')}] by {d.get('changed_by','?')}"
                for d in session.all_drifts()
            ]
            narrative = self._narrate(
                system=(
                    "You are the Detector agent in a data-reliability team. "
                    "In 2-3 sentences, plain English, summarise what you just found. "
                    "Be specific about the columns and who changed them. "
                    "Do not invent numbers."
                ),
                user=(
                    f"Tables scanned: {n_tables}. Drifts: {n_drifts} "
                    f"({n_crit} critical). Details:\n" +
                    ("\n".join(drift_lines) or "No drifts.")
                ),
                fallback=fallback,
            )
        else:
            narrative = fallback

        self._emit(emit, EventKind.AGENT_MESSAGE, text=narrative)
        handoff_text = "Handoff → RootCauseAnalyst" if n_drifts else "No drift — investigation ends here."
        self._emit(emit, EventKind.AGENT_COMPLETE, text=handoff_text)

        output = AgentOutput(
            agent=self.name,
            status="success" if n_drifts else "no_drift",
            summary=narrative,
            findings={
                "n_tables_scanned": n_tables,
                "n_drifts": n_drifts,
                "n_critical": n_crit,
                "audit_entries": len(audit),
            },
        )
        session.detector_output = output
        return output


# ─────────────────────────────────────────────────────────────────────────────
# 2. RootCauseAnalyst — the detective
# ─────────────────────────────────────────────────────────────────────────────
class RootCauseAnalyst(BaseAgent):
    name = "RootCauseAnalyst"
    emoji = "🕵️"
    role_summary = "Traces DataHub lineage and correlates the drift with owners + audit trail."
    allowed_tools = ("get_lineage", "get_ownership")

    def run(self, session, emit):
        drifts = session.all_drifts()
        affected_tables = list({d["table"] for d in drifts})

        self._emit(emit, EventKind.AGENT_START,
                   text=f"Investigating root cause for {len(drifts)} drift(s) "
                        f"across {len(affected_tables)} table(s).")

        trace, edges = self._tool(emit, "get_lineage",
                                  summary=f"Traced {len(session.detector_output.findings) if session.detector_output else 0} lineage edges")
        session.lineage_trace = trace
        session.lineage_graph = edges

        ownership = self._tool(emit, "get_ownership",
                               summary=f"Fetched owners for {len(affected_tables)} table(s)")
        session.ownership = ownership

        # Deterministic root-cause narrative
        drift_lines = []
        for d in drifts:
            who = d.get("changed_by", "unknown")
            reason = d.get("change_reason", "N/A")
            owner_line = ""
            owners = ownership.get(d["table"], [])
            if owners:
                owner_line = f" | owners: {', '.join(owners[:2])}"
            drift_lines.append(
                f"  • [{d.get('severity','?')}] {d['table']}.{d['column']}: "
                f"{d['baseline_type']} → {d['actual_type']} by `{who}` — {reason}{owner_line}"
            )

        deterministic = (
            f"SCHEMA DRIFT in {', '.join(affected_tables)}.\n"
            + "\n".join(drift_lines)
            + "\n\nHypothesis: schema change on source table(s) was not coordinated "
              "with downstream ML consumers. Type changes break numeric operations in "
              "feature engineering, causing silent inference failures."
        )

        if session.llm_enabled:
            narrative = self._narrate(
                system=(
                    "You are the Root Cause Analyst in a data-reliability team. "
                    "Given a drift summary, an audit trail, ownership info and a "
                    "lineage trace, write a 3-4 sentence hypothesis about WHO likely "
                    "caused this and WHY, and which downstream systems it will hurt. "
                    "Refer to real names from the input. No speculation beyond the data."
                ),
                user=(
                    f"Drifts:\n{chr(10).join(drift_lines)}\n\n"
                    f"Lineage:\n{trace}\n\n"
                    f"Ownership by table: {ownership}"
                ),
                fallback=deterministic,
            )
        else:
            narrative = deterministic

        session.root_cause = narrative
        session.hypothesis = {
            "affected_tables": affected_tables,
            "primary_actor": (drifts[0].get("changed_by") if drifts else "unknown"),
            "primary_reason": (drifts[0].get("change_reason") if drifts else "unknown"),
            "n_owners": sum(len(v) for v in ownership.values()),
        }

        self._emit(emit, EventKind.AGENT_MESSAGE, text=narrative)
        self._emit(emit, EventKind.AGENT_COMPLETE, text="Handoff → ImpactAssessor + FixAuthor (parallel)")

        output = AgentOutput(
            agent=self.name,
            status="success",
            summary=narrative,
            findings={
                "affected_tables": affected_tables,
                "lineage_edges": len(edges),
                "owners_found": sum(len(v) for v in ownership.values()),
            },
        )
        session.root_cause_output = output
        return output


# ─────────────────────────────────────────────────────────────────────────────
# 3. ImpactAssessor — the accountant
# ─────────────────────────────────────────────────────────────────────────────
class ImpactAssessor(BaseAgent):
    name = "ImpactAssessor"
    emoji = "📊"
    role_summary = "Quantifies rows affected, impacted ML models, and rough $/hour exposure."
    allowed_tools = ("compute_impact", "get_table_stats",
                     "find_impacted_models", "estimate_dollar_impact")

    def run(self, session, emit):
        drifts = session.all_drifts()
        affected_tables = list({d["table"] for d in drifts})
        n_crit = sum(1 for d in drifts if d.get("severity") == "CRITICAL")

        self._emit(emit, EventKind.AGENT_START,
                   text=f"Computing business impact for {len(drifts)} drift(s).")

        impact = self._tool(emit, "compute_impact",
                            summary=f"Impact metrics computed")
        session.impact_metrics = impact

        stats = self._tool(emit, "get_table_stats",
                           summary=f"Table stats: {impact.get('total_rows', 0):,} rows")
        session.table_stats = stats

        impacted = self._tool(emit, "find_impacted_models", affected_tables,
                              summary=f"{0} model(s) at risk")
        session.impacted_models = impacted

        total_rows = int(stats.get("total_rows") or 0)
        fraud_rate = float(stats.get("fraud_rate_pct") or 0.0)
        dollar_hr = self._tool(
            emit, "estimate_dollar_impact",
            n_crit, total_rows, len(impacted), fraud_rate,
            summary="Dollar exposure heuristic applied",
        )
        session.dollar_impact_hour = dollar_hr

        # Deterministic impact narrative
        col_lines = []
        for col, cm in impact.get("columns", {}).items():
            if not cm.get("type_ok", True):
                if isinstance(cm.get("corrupted_count"), int):
                    col_lines.append(
                        f"  • `{col}`: {cm['corrupted_count']:,} rows corrupted "
                        f"({cm.get('corrupted_pct','?')}%), "
                        f"{cm.get('castable_pct','?')}% castable back"
                    )
                else:
                    col_lines.append(f"  • `{col}`: type mismatch")

        model_lines = [f"  • {m['name']} [{m['platform']}]" for m in impacted] or ["  • (no downstream ML model matched)"]

        deterministic = (
            f"Impact on {total_rows:,} transactions "
            f"({stats.get('fraud_rows', '?')} fraud cases, {fraud_rate:.4f}% rate).\n"
            f"Impacted ML model(s):\n" + "\n".join(model_lines) + "\n" +
            ("Per-column corruption:\n" + "\n".join(col_lines) if col_lines else "") +
            f"\nRough exposure heuristic: ~${dollar_hr:,.0f}/hour "
            f"({len(impacted)} model(s) × {n_crit} critical drift multiplier)."
        )

        if session.llm_enabled:
            narrative = self._narrate(
                system=(
                    "You are the Impact Assessor in a data-reliability team. "
                    "Given raw impact metrics, produce a 3-sentence business summary: "
                    "rows affected, models at risk, and estimated hourly exposure. "
                    "The dollar figure is a documented heuristic — say 'estimated' or "
                    "'rough' — never claim it as forecast. Use the exact numbers given."
                ),
                user=(
                    f"Total rows: {total_rows}. Fraud rows: {stats.get('fraud_rows','?')}. "
                    f"Impacted models: {[m['name'] for m in impacted]}. "
                    f"Critical drifts: {n_crit}. "
                    f"Dollar/hour heuristic: {dollar_hr}. "
                    f"Column corruption:\n{chr(10).join(col_lines) or 'none'}"
                ),
                fallback=deterministic,
            )
        else:
            narrative = deterministic

        self._emit(emit, EventKind.AGENT_MESSAGE, text=narrative)
        self._emit(emit, EventKind.AGENT_COMPLETE,
                   text=f"Impact quantified. ~${dollar_hr:,.0f}/hr exposure.")

        output = AgentOutput(
            agent=self.name,
            status="success",
            summary=narrative,
            findings={
                "total_rows": total_rows,
                "impacted_models": len(impacted),
                "dollar_hour_estimate": dollar_hr,
                "corrupted_columns": len(col_lines),
            },
        )
        session.impact_output = output
        return output


# ─────────────────────────────────────────────────────────────────────────────
# 4. FixAuthor — the fixer
# ─────────────────────────────────────────────────────────────────────────────
class FixAuthor(BaseAgent):
    name = "FixAuthor"
    emoji = "🔧"
    role_summary = "Generates SQL + dbt remediation scripts for every TYPE_CHANGE drift."
    allowed_tools = ("generate_fix_scripts",)

    def run(self, session, emit):
        drifts = session.all_drifts()
        type_changes = [d for d in drifts if d.get("drift_type") == "TYPE_CHANGE"]

        iteration = session.fix_iterations + 1
        header = f"Iteration #{iteration}" if iteration > 1 else "First attempt"

        prefix = ""
        if session.reviewer_feedback:
            prefix = f"Reviewer feedback last round: {session.reviewer_feedback}\n"

        self._emit(emit, EventKind.AGENT_START,
                   text=f"{header}: authoring fixes for {len(type_changes)} type-change drift(s).")

        scripts = self._tool(emit, "generate_fix_scripts", type_changes,
                             summary=f"Generated {len(type_changes)} fix script(s)")
        session.fix_scripts = scripts
        session.fix_iterations = iteration

        fix_lines = [f"  • `{k}` → `{v.get('sql_fix_path','?')}`" for k, v in scripts.items()]
        deterministic = (
            f"{prefix}Generated {len(scripts)} fix script(s) — each wrapped in BEGIN/COMMIT "
            f"and containing a post-fix verification step.\n" + "\n".join(fix_lines)
        )

        if session.llm_enabled:
            narrative = self._narrate(
                system=(
                    "You are the Fix Author in a data-reliability team. "
                    "In 2 sentences, describe what you generated and why the approach "
                    "is safe (transactional, verification step, cast-back). Do NOT invent "
                    "file names — quote the ones given."
                ),
                user=f"Scripts:\n{chr(10).join(fix_lines) or 'none'}\n{prefix}",
                fallback=deterministic,
            )
        else:
            narrative = deterministic

        self._emit(emit, EventKind.AGENT_MESSAGE, text=narrative)
        self._emit(emit, EventKind.AGENT_COMPLETE, text="Handoff → Reviewer")

        output = AgentOutput(
            agent=self.name,
            status="success" if scripts else "failed",
            summary=narrative,
            findings={"n_scripts": len(scripts), "iteration": iteration},
        )
        session.fix_output = output
        return output


# ─────────────────────────────────────────────────────────────────────────────
# 5. Reviewer — the gate
# ─────────────────────────────────────────────────────────────────────────────
class Reviewer(BaseAgent):
    name = "Reviewer"
    emoji = "🛡️"
    role_summary = "Statically validates fix scripts for safety and completeness."
    allowed_tools = ("validate_fix_safety",)

    def run(self, session, emit):
        self._emit(emit, EventKind.AGENT_START,
                   text=f"Reviewing {len(session.fix_scripts)} fix script(s).")

        result = self._tool(emit, "validate_fix_safety", session.fix_scripts,
                            summary=f"Verdict: pending")
        verdict_str = result.get("verdict", "REJECT_INCOMPLETE")
        verdict = ReviewVerdict(verdict_str)
        session.review_verdict = verdict
        issues = result.get("issues", [])

        if verdict == ReviewVerdict.APPROVE:
            note = f"✅ APPROVE — {len(result.get('checked_files', []))} script(s) pass safety + completeness checks."
        else:
            issue_lines = [f"  • {i['reason']} ({i['file']})" for i in issues]
            note = f"❌ {verdict.value} — {len(issues)} issue(s):\n" + "\n".join(issue_lines)

        session.review_notes = note
        session.reviewer_feedback = "; ".join(i["reason"] for i in issues) if issues else ""

        if session.llm_enabled:
            issue_reasons = "\n".join(f"- {i['reason']}" for i in issues) or "none"
            narrative = self._narrate(
                system=(
                    "You are the Reviewer in a data-reliability team — the safety gate "
                    "before any fix is applied. In 2 sentences, state the verdict "
                    "(APPROVE / REJECT) and the top reason. Be direct."
                ),
                user=f"Verdict: {verdict.value}. Issues:\n{issue_reasons}",
                fallback=note,
            )
        else:
            narrative = note

        self._emit(emit, EventKind.AGENT_MESSAGE, text=narrative)
        self._emit(emit, EventKind.REVIEW_VERDICT, text=verdict.value,
                   data={"verdict": verdict.value, "issues": issues})
        self._emit(emit, EventKind.AGENT_COMPLETE, text=f"Verdict issued: {verdict.value}")

        output = AgentOutput(
            agent=self.name,
            status="success" if verdict == ReviewVerdict.APPROVE else "needs_revision",
            summary=narrative,
            findings={"verdict": verdict.value, "n_issues": len(issues)},
        )
        session.review_output = output
        return output
