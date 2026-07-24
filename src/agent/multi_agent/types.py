"""
Shared types for the multi-agent investigation system.

The orchestrator streams AgentEvents to the UI while agents execute; each agent
returns a structured AgentOutput. InvestigationSession is the shared working
state that agents read from and write to.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class EventKind(str, Enum):
    ORCHESTRATOR = "orchestrator"
    AGENT_START = "agent_start"
    AGENT_MESSAGE = "agent_message"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    HANDOFF = "handoff"
    REVIEW_VERDICT = "review_verdict"
    AGENT_COMPLETE = "agent_complete"
    ITERATION_START = "iteration_start"
    DONE = "done"
    ERROR = "error"


class ReviewVerdict(str, Enum):
    APPROVE = "APPROVE"
    REJECT_UNSAFE = "REJECT_UNSAFE"
    REJECT_INCOMPLETE = "REJECT_INCOMPLETE"


@dataclass
class AgentEvent:
    """One event in the streaming timeline the UI renders."""
    kind: EventKind
    agent: str
    emoji: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    text: str = ""
    tool: Optional[str] = None
    tool_args: Optional[dict] = None
    tool_result_summary: Optional[str] = None
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "agent": self.agent,
            "emoji": self.emoji,
            "timestamp": self.timestamp,
            "text": self.text,
            "tool": self.tool,
            "tool_args": self.tool_args,
            "tool_result_summary": self.tool_result_summary,
            "data": self.data,
        }


@dataclass
class AgentOutput:
    """What each agent hands off to the next."""
    agent: str
    status: str                    # "success" | "needs_revision" | "failed"
    summary: str                   # one-paragraph narrative for the UI
    findings: dict = field(default_factory=dict)
    events: list[AgentEvent] = field(default_factory=list)


@dataclass
class InvestigationSession:
    """
    Shared mutable state across the agent team.

    Each agent reads what it needs from earlier fields and writes what it
    produces into its own field. Fields default to None/empty so the session
    remains valid at every point in the pipeline.
    """
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # DetectorAgent fills these
    drift_report: Optional[dict] = None
    audit_log: list[dict] = field(default_factory=list)
    detector_output: Optional[AgentOutput] = None

    # RootCauseAnalyst fills these
    lineage_trace: str = ""
    lineage_graph: list[dict] = field(default_factory=list)
    ownership: dict = field(default_factory=dict)
    root_cause: str = ""
    hypothesis: dict = field(default_factory=dict)
    root_cause_output: Optional[AgentOutput] = None

    # ImpactAssessor fills these
    impact_metrics: dict = field(default_factory=dict)
    table_stats: dict = field(default_factory=dict)
    impacted_models: list[dict] = field(default_factory=list)
    dollar_impact_hour: float = 0.0
    impact_output: Optional[AgentOutput] = None

    # FixAuthor fills these
    fix_scripts: dict = field(default_factory=dict)
    fix_iterations: int = 0
    reviewer_feedback: str = ""
    fix_output: Optional[AgentOutput] = None

    # Reviewer fills these
    review_verdict: Optional[ReviewVerdict] = None
    review_notes: str = ""
    review_output: Optional[AgentOutput] = None

    # Populated by orchestrator after write-back to DataHub
    datahub_writeback: dict = field(default_factory=dict)

    # Runtime settings
    max_review_iterations: int = 2
    llm_enabled: bool = False
    llm_model: str = ""

    def has_drift(self) -> bool:
        return bool(self.drift_report and self.drift_report.get("any_drift"))

    def all_drifts(self) -> list[dict]:
        if not self.drift_report:
            return []
        out = []
        for _, info in self.drift_report.get("tables", {}).items():
            out.extend(info.get("drifts", []))
        return out
