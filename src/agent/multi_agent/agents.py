"""
Five specialized agents — pure LLM reasoning over real tool output.

Each agent:
  1. Emits an AGENT_START event.
  2. Runs its tool calls (via ToolRegistry) — the tools it is *allowed* to
     call are declared in `allowed_tools`, which enforces separation of
     concern between roles. Tools run in code; the LLM never fabricates
     drift data, lineage, or SQL.
  3. Sends the raw tool output to the LLM and emits the LLM's analysis as
     its message. There is no template fallback — if the LLM is down the
     agent fails loudly (NarratorError) and the orchestrator surfaces it.
  4. Writes its findings back into the shared InvestigationSession.
  5. Emits AGENT_COMPLETE and returns an AgentOutput for the orchestrator.

The Reviewer both runs the static safety validator AND asks the LLM for the
final verdict, feeding it the validator's findings. The LLM's first line must
be APPROVE / REJECT_UNSAFE / REJECT_INCOMPLETE; if it flags unsafe patterns
the orchestrator loops back to the FixAuthor.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from src.agent.multi_agent.llm import Narrator
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

    def __init__(self, registry: ToolRegistry, narrator: Narrator):
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

    def _llm(self, system: str, user: str) -> str:
        return self.narrator.narrate(system, user)

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

        drift_report = self._tool(emit, "scan_schema", summary="Schema scan complete")
        n_tables = len(drift_report.get("tables", {}))
        n_drifts = drift_report.get("total_drifts", 0)
        n_crit = drift_report.get("critical_drifts", 0)
        session.drift_report = drift_report

        audit = self._tool(emit, "read_audit_log", summary="Audit log read")
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

        drift_lines = [
            f"- {d['table']}.{d['column']}: {d['baseline_type']} → {d['actual_type']} "
            f"[{d.get('severity','?')}] by {d.get('changed_by','?')} — {d.get('change_reason','?')}"
            for d in session.all_drifts()
        ]
        narrative = self._llm(
            system=(
                "You are the Detector agent in a data-reliability team investigating "
                "a production incident. In 2-3 sentences of plain English, report what "
                "your schema scan found. Be specific about columns, type changes, and "
                "who made them. Use ONLY the facts given — never invent numbers or names."
            ),
            user=(
                f"Tables scanned: {n_tables}. Drifts found: {n_drifts} "
                f"({n_crit} critical). Audit entries: {len(audit)}.\n"
                f"Drift details:\n" + ("\n".join(drift_lines) or "No drifts detected — all schemas match baseline.")
            ),
        )

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

        trace, edges = self._tool(emit, "get_lineage", summary="Lineage traced")
        session.lineage_trace = trace
        session.lineage_graph = edges

        ownership = self._tool(emit, "get_ownership",
                               summary=f"Owners fetched for {len(affected_tables)} table(s)")
        session.ownership = ownership

        drift_lines = []
        for d in drifts:
            owners = ownership.get(d["table"], [])
            owner_str = f" | owners: {', '.join(owners[:2])}" if owners else " | owners: none registered"
            drift_lines.append(
                f"- [{d.get('severity','?')}] {d['table']}.{d['column']}: "
                f"{d['baseline_type']} → {d['actual_type']} by `{d.get('changed_by','?')}` "
                f"— reason given: {d.get('change_reason','?')}{owner_str}"
            )

        narrative = self._llm(
            system=(
                "You are the Root Cause Analyst in a data-reliability team. Given a "
                "drift summary, the audit trail (who changed what and their stated "
                "reason), table ownership, and the DataHub lineage trace, write a "
                "3-4 sentence root-cause hypothesis: WHO likely caused this, WHY it "
                "happened (based on their stated reason), and WHICH downstream systems "
                "will break according to the lineage. Refer to real names and columns "
                "from the input. Never speculate beyond the given data."
            ),
            user=(
                f"Drifts with audit context:\n{chr(10).join(drift_lines)}\n\n"
                f"Lineage trace (upstream of each ML model):\n{trace}\n\n"
                f"Ownership by table: {ownership}"
            ),
        )

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

        impact = self._tool(emit, "compute_impact", summary="Impact metrics computed")
        session.impact_metrics = impact

        stats = self._tool(emit, "get_table_stats",
                           summary=f"{impact.get('total_rows', 0):,} rows analysed")
        session.table_stats = stats

        impacted = self._tool(emit, "find_impacted_models", affected_tables,
                              summary="Downstream models resolved")
        session.impacted_models = impacted

        total_rows = int(stats.get("total_rows") or 0)
        fraud_rate = float(stats.get("fraud_rate_pct") or 0.0)
        dollar_hr = self._tool(
            emit, "estimate_dollar_impact",
            n_crit, total_rows, len(impacted), fraud_rate,
            summary="Exposure heuristic applied",
        )
        session.dollar_impact_hour = dollar_hr

        col_lines = []
        for col, cm in impact.get("columns", {}).items():
            if not cm.get("type_ok", True):
                if isinstance(cm.get("corrupted_count"), int):
                    col_lines.append(
                        f"- `{col}`: {cm['corrupted_count']:,} rows corrupted "
                        f"({cm.get('corrupted_pct','?')}%), "
                        f"{cm.get('castable_pct','?')}% castable back"
                    )
                else:
                    col_lines.append(f"- `{col}`: type mismatch detected")

        narrative = self._llm(
            system=(
                "You are the Impact Assessor in a data-reliability team. Given raw "
                "impact metrics, produce a 3-sentence business summary: rows affected, "
                "which ML models are at risk, and the estimated hourly dollar exposure. "
                "The dollar figure is a documented heuristic — call it 'estimated' or "
                "'rough', never a forecast. Use the exact numbers given; never invent any."
            ),
            user=(
                f"Total rows: {total_rows:,}. Fraud rows: {stats.get('fraud_rows','?')}. "
                f"Fraud rate: {fraud_rate}%.\n"
                f"Impacted ML models: {[m['name'] for m in impacted] or 'none matched'}.\n"
                f"Critical drifts: {n_crit}.\n"
                f"Estimated $/hour exposure (heuristic): {dollar_hr}.\n"
                f"Per-column corruption:\n{chr(10).join(col_lines) or 'none measured'}"
            ),
        )

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

        self._emit(emit, EventKind.AGENT_START,
                   text=f"{header}: authoring fixes for {len(type_changes)} type-change drift(s).")

        scripts = self._tool(emit, "generate_fix_scripts", type_changes,
                             summary=f"{len(type_changes)} fix script(s) generated")
        session.fix_scripts = scripts
        session.fix_iterations = iteration

        fix_lines = [f"- `{k}` → `{v.get('sql_fix_path','?')}`" for k, v in scripts.items()]
        feedback_note = (
            f"The reviewer rejected the previous attempt with this feedback: "
            f"{session.reviewer_feedback}\n" if session.reviewer_feedback else ""
        )

        narrative = self._llm(
            system=(
                "You are the Fix Author in a data-reliability team. In 2-3 sentences, "
                "describe the remediation scripts you generated and why the approach is "
                "safe (transactional BEGIN/COMMIT, verification step, cast-back USING "
                "clause). If reviewer feedback is present, acknowledge what you changed. "
                "Quote only the file names given — never invent paths."
            ),
            user=f"{feedback_note}Generated scripts:\n{chr(10).join(fix_lines) or 'none — no TYPE_CHANGE drifts'}",
        )

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
    role_summary = "Runs the static safety validator, then issues the final verdict via LLM."
    allowed_tools = ("validate_fix_safety",)

    _VALID_VERDICTS = {v.value for v in ReviewVerdict}

    def run(self, session, emit):
        self._emit(emit, EventKind.AGENT_START,
                   text=f"Reviewing {len(session.fix_scripts)} fix script(s).")

        result = self._tool(emit, "validate_fix_safety", session.fix_scripts,
                            summary="Static safety analysis complete")
        validator_verdict = result.get("verdict", "REJECT_INCOMPLETE")
        issues = result.get("issues", [])
        issue_lines = [f"- {i['reason']} (file: {i['file']})" for i in issues]

        # The LLM issues the final verdict, grounded in the validator findings.
        raw = self._llm(
            system=(
                "You are the Reviewer — the safety gate in a data-reliability team. "
                "No fix reaches production or DataHub without your approval.\n"
                "You are given the findings of a static SQL safety analysis. Decide "
                "the verdict.\n"
                "STRICT OUTPUT FORMAT: first line must be exactly one of APPROVE, "
                "REJECT_UNSAFE, or REJECT_INCOMPLETE. Then 1-2 sentences explaining "
                "your reasoning.\n"
                "Rules: any unsafe SQL pattern (DROP TABLE, TRUNCATE, DELETE without "
                "WHERE, GRANT ALL) → REJECT_UNSAFE. Missing BEGIN/COMMIT or missing "
                "verification step → REJECT_INCOMPLETE. No findings → APPROVE. "
                "Never approve a script the analysis flagged as unsafe."
            ),
            user=(
                f"Static analysis verdict suggestion: {validator_verdict}\n"
                f"Findings ({len(issues)}):\n"
                + ("\n".join(issue_lines) or "- none — all checks passed")
                + f"\nFiles checked: {result.get('checked_files', [])}"
            ),
        )

        first_line = raw.strip().splitlines()[0].strip().upper().rstrip(".:")
        if first_line in self._VALID_VERDICTS:
            verdict = ReviewVerdict(first_line)
        else:
            # LLM broke the output contract — fall back to the validator's
            # verdict so the safety gate still holds (never weaker than static).
            logger.warning(f"Reviewer LLM output had no valid verdict line: {raw[:120]!r}. "
                           f"Using validator verdict {validator_verdict}.")
            verdict = ReviewVerdict(validator_verdict)

        # Safety invariant: the LLM may be stricter than the validator but
        # never more lenient — an unsafe finding can't be talked away.
        if validator_verdict != "APPROVE" and verdict == ReviewVerdict.APPROVE:
            logger.warning("Reviewer LLM tried to approve over validator rejection — overruled.")
            verdict = ReviewVerdict(validator_verdict)

        session.review_verdict = verdict
        session.review_notes = raw
        session.reviewer_feedback = "; ".join(i["reason"] for i in issues) if issues else ""

        self._emit(emit, EventKind.AGENT_MESSAGE, text=raw)
        self._emit(emit, EventKind.REVIEW_VERDICT, text=verdict.value,
                   data={"verdict": verdict.value, "issues": issues})
        self._emit(emit, EventKind.AGENT_COMPLETE, text=f"Verdict issued: {verdict.value}")

        output = AgentOutput(
            agent=self.name,
            status="success" if verdict == ReviewVerdict.APPROVE else "needs_revision",
            summary=raw,
            findings={"verdict": verdict.value, "n_issues": len(issues)},
        )
        session.review_output = output
        return output
