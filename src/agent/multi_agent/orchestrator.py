"""
Orchestrator — LangGraph state machine that runs the 5-agent team.

Graph shape:

    START
      │
    ┌─▼──────────┐
    │ detector   │
    └─┬──────────┘
      │  drift?
      ├── no  ───────────────────► END
      ▼
    ┌────────────┐
    │ analyst    │
    └─┬──────────┘
      │  (fan out)
    ┌─┴──────────┐   ┌───────────┐
    │ assessor   │   │ author    │
    └─┬──────────┘   └────┬──────┘
      │                   │
      └────────┬──────────┘  (join)
               ▼
         ┌─────────┐
         │ reviewer│
         └────┬────┘
              │  verdict?
     approve ─┤─── writeback ──► END
     reject  ─┤─── (iter < max)? author : writeback_skip ──► END
              ▼

Design principles:

* **Real state machine** — LangGraph makes the review loop and the fan-out
  first-class edges instead of hand-rolled control flow.
* **Pure LLM reasoning** — every agent's analysis and the reviewer's verdict
  are genuine LLM output (OpenAI by default, incl. OPENAI_BASE_URL gateways;
  Anthropic as alternative). No LLM key → the orchestrator refuses to start.
* **Tools run in code** — the ToolRegistry (with per-agent permission
  enforcement) executes schema scans, lineage reads, and SQL validation; the
  LLM reasons over that real output and can never fabricate it.
* **Write-back is gated** — DataHub tagging only fires when the reviewer
  approves. On REJECT the graph loops back to the author for a revision
  bounded by max_review_iterations.

The stream() method yields AgentEvent objects for the Streamlit UI so the
"agent chatter" panel stays live during the run.
"""
from __future__ import annotations

import logging
import operator
from datetime import datetime, timezone
from typing import Annotated, Any, Generator, Optional, TypedDict

try:
    from langgraph.graph import END, START, StateGraph
    _LANGGRAPH_AVAILABLE = True
except Exception:  # pragma: no cover
    _LANGGRAPH_AVAILABLE = False
    StateGraph = None  # type: ignore
    START = "__start__"  # type: ignore
    END = "__end__"  # type: ignore

from src.agent.datahub_gms_client import DatahubGmsClient
from src.agent.db_inspector import PostgresSchemaInspector
from src.agent.multi_agent.agents import (
    DetectorAgent,
    FixAuthor,
    ImpactAssessor,
    Reviewer,
    RootCauseAnalyst,
)
from src.agent.multi_agent.llm import Narrator, NarratorError
from src.agent.multi_agent.tools import ToolRegistry
from src.agent.multi_agent.types import (
    AgentEvent,
    EventKind,
    InvestigationSession,
    ReviewVerdict,
)
from src.agent.pipeline_discovery import PipelineContext, discover_pipeline

logger = logging.getLogger("MultiAgent.Orchestrator")


# ────────────────────────────────────────────────────────────────────
# Graph state
# ────────────────────────────────────────────────────────────────────
class GraphState(TypedDict, total=False):
    """
    Shared state across LangGraph nodes.

    - session: mutated in place by each agent (safe because sync execution).
    - events: appended by each node; the operator.add reducer concatenates
      lists returned by parallel branches during a join.
    """
    session: InvestigationSession
    events: Annotated[list[AgentEvent], operator.add]


# ────────────────────────────────────────────────────────────────────
# Orchestrator
# ────────────────────────────────────────────────────────────────────
class MultiAgentOrchestrator:
    def __init__(
        self,
        inspector: PostgresSchemaInspector,
        gms: DatahubGmsClient,
        ctx: Optional[PipelineContext] = None,
        max_review_iterations: int = 2,
    ):
        if not _LANGGRAPH_AVAILABLE:
            raise RuntimeError(
                "langgraph is not installed. Add `langgraph>=0.2.0` to "
                "requirements.txt and reinstall."
            )

        self.inspector = inspector
        self.gms = gms
        self.ctx = ctx or discover_pipeline(gms)

        if inspector.pipeline_context is None:
            inspector.pipeline_context = self.ctx

        self.registry = ToolRegistry(inspector, gms, self.ctx)

        # Pure-LLM design: every agent reasons via the LLM, so a working
        # provider is a hard requirement — no deterministic fallback.
        self.narrator = Narrator()
        if not self.narrator.is_live():
            raise NarratorError(
                "Multi-agent team requires an LLM. Set OPENAI_API_KEY "
                "(+ OPENAI_BASE_URL for gateways like SumoPod) or "
                "ANTHROPIC_API_KEY in .env."
            )

        # Instantiate agents (each holds its own allowed_tools whitelist)
        self.detector = DetectorAgent(self.registry, self.narrator)
        self.analyst = RootCauseAnalyst(self.registry, self.narrator)
        self.assessor = ImpactAssessor(self.registry, self.narrator)
        self.author = FixAuthor(self.registry, self.narrator)
        self.reviewer = Reviewer(self.registry, self.narrator)

        self.max_review_iterations = max_review_iterations
        self.graph = self._build_graph()

    # ────────────────────────────────────────────────────────────────
    # Graph construction
    # ────────────────────────────────────────────────────────────────
    def _run_agent_node(self, agent, state: GraphState) -> dict:
        """Turn an agent's imperative run() into a graph node return."""
        session = state["session"]
        events: list[AgentEvent] = []
        try:
            agent.run(session, events.append)
        except Exception as exc:
            events.append(AgentEvent(
                kind=EventKind.ERROR,
                agent=getattr(agent, "name", "?"),
                emoji="⚠️",
                text=f"{agent.name} raised {exc.__class__.__name__}: {exc}",
            ))
        return {"events": events}

    def _detector_node(self, state):   return self._run_agent_node(self.detector, state)
    def _analyst_node(self, state):    return self._run_agent_node(self.analyst, state)
    def _assessor_node(self, state):   return self._run_agent_node(self.assessor, state)
    def _author_node(self, state):     return self._run_agent_node(self.author, state)
    def _reviewer_node(self, state):   return self._run_agent_node(self.reviewer, state)

    def _writeback_node(self, state) -> dict:
        session: InvestigationSession = state["session"]
        events: list[AgentEvent] = []

        drifts = session.all_drifts()
        affected_tables = list({d["table"] for d in drifts})
        affected_columns = [d["column"] for d in drifts]
        n_crit = sum(1 for d in drifts if d.get("severity") == "CRITICAL")

        incident_tag = "DATA-INCIDENT: DO NOT USE"
        incident_desc = (
            f"Schema drift in {', '.join(affected_tables)}. "
            f"Columns: {', '.join(affected_columns)}. "
            f"Critical drifts: {n_crit}. "
            f"Approved by multi-agent reviewer at {datetime.now(timezone.utc).isoformat()}."
        )

        tag_results: dict = {}
        for m in session.impacted_models:
            urn = m["urn"]
            try:
                ok = self.gms.add_tag(urn, incident_tag)
                self.gms.emit_incident(urn, f"[INCIDENT] {incident_tag}", incident_desc)
                tag_results[urn] = ok
                events.append(AgentEvent(
                    kind=EventKind.TOOL_CALL, agent="Orchestrator", emoji="🏷️",
                    text=f"Tagged {m['name']} with `{incident_tag}`.",
                    tool="datahub.add_tag",
                    tool_result_summary="ok" if ok else "failed",
                ))
            except Exception as exc:
                tag_results[urn] = False
                events.append(AgentEvent(
                    kind=EventKind.ERROR, agent="Orchestrator", emoji="⚠️",
                    text=f"Failed to tag {m['name']}: {exc}",
                ))

        for tname in affected_tables:
            src = self.ctx.source_tables.get(tname)
            if not src:
                continue
            try:
                self.gms.add_tag(src.urn, "SCHEMA-DRIFT-DETECTED")
                events.append(AgentEvent(
                    kind=EventKind.TOOL_CALL, agent="Orchestrator", emoji="🏷️",
                    text=f"Tagged source table `{tname}` with SCHEMA-DRIFT-DETECTED.",
                    tool="datahub.add_tag",
                ))
            except Exception as exc:
                events.append(AgentEvent(
                    kind=EventKind.ERROR, agent="Orchestrator", emoji="⚠️",
                    text=f"Failed to tag {tname}: {exc}",
                ))

        session.datahub_writeback = {
            "tag": incident_tag,
            "description": incident_desc,
            "models_tagged": tag_results,
            "n_models": len(session.impacted_models),
        }
        events.append(AgentEvent(
            kind=EventKind.DONE, agent="Orchestrator", emoji="🏁",
            text="Investigation complete.",
        ))
        return {"events": events}

    def _writeback_skip_node(self, state) -> dict:
        session: InvestigationSession = state["session"]
        v = session.review_verdict.value if session.review_verdict else "UNKNOWN"
        events = [
            AgentEvent(
                kind=EventKind.ORCHESTRATOR, agent="Orchestrator", emoji="🛑",
                text=f"Fix NOT applied to DataHub — reviewer verdict is {v} after "
                     f"{session.fix_iterations} attempt(s).",
            ),
            AgentEvent(
                kind=EventKind.DONE, agent="Orchestrator", emoji="🏁",
                text="Investigation complete (held for human review).",
            ),
        ]
        return {"events": events}

    # Conditional routers
    def _route_after_detector(self, state: GraphState) -> str:
        return "analyst" if state["session"].has_drift() else END

    def _route_after_reviewer(self, state: GraphState) -> str:
        session = state["session"]
        if session.review_verdict == ReviewVerdict.APPROVE:
            return "writeback"
        # Rejected — loop back to author unless we've hit the iteration cap.
        if session.fix_iterations < session.max_review_iterations:
            return "author_revise"
        return "writeback_skip"

    def _author_revise_node(self, state: GraphState) -> dict:
        """Wrapper node that emits a handoff event before re-running FixAuthor."""
        events: list[AgentEvent] = [
            AgentEvent(
                kind=EventKind.HANDOFF, agent="Orchestrator", emoji="🔁",
                text=f"Reviewer rejected — sending back to FixAuthor "
                     f"(iteration {state['session'].fix_iterations + 1}/"
                     f"{state['session'].max_review_iterations}).",
            ),
        ]
        # Delegate to the shared node helper for the actual author run.
        author_result = self._run_agent_node(self.author, state)
        events.extend(author_result["events"])
        return {"events": events}

    def _build_graph(self):
        builder = StateGraph(GraphState)

        builder.add_node("detector", self._detector_node)
        builder.add_node("analyst", self._analyst_node)
        builder.add_node("assessor", self._assessor_node)
        builder.add_node("author", self._author_node)
        builder.add_node("reviewer", self._reviewer_node)
        builder.add_node("author_revise", self._author_revise_node)
        builder.add_node("writeback", self._writeback_node)
        builder.add_node("writeback_skip", self._writeback_skip_node)

        builder.add_edge(START, "detector")
        builder.add_conditional_edges(
            "detector", self._route_after_detector,
            {"analyst": "analyst", END: END},
        )

        # Fan out: analyst → (assessor ‖ author)
        builder.add_edge("analyst", "assessor")
        builder.add_edge("analyst", "author")

        # Join: both feed into reviewer (LangGraph waits for both)
        builder.add_edge("assessor", "reviewer")
        builder.add_edge("author", "reviewer")

        builder.add_conditional_edges(
            "reviewer", self._route_after_reviewer,
            {
                "writeback": "writeback",
                "author_revise": "author_revise",
                "writeback_skip": "writeback_skip",
            },
        )

        # After a revision, back to the reviewer
        builder.add_edge("author_revise", "reviewer")

        builder.add_edge("writeback", END)
        builder.add_edge("writeback_skip", END)

        return builder.compile()

    # ────────────────────────────────────────────────────────────────
    # Streaming entry point
    # ────────────────────────────────────────────────────────────────
    def stream(self, alert_message: str) -> Generator[AgentEvent, None, InvestigationSession]:
        session = InvestigationSession(
            alert_message=alert_message,
            llm_enabled=self.narrator.is_live(),
            llm_model=self.narrator.model if self.narrator.is_live() else "",
            max_review_iterations=self.max_review_iterations,
        )

        # Opening event so the UI has something to show immediately.
        yield AgentEvent(
            kind=EventKind.ORCHESTRATOR, agent="Orchestrator", emoji="🎬",
            text=(f"LangGraph investigation started. LLM: "
                  f"{self.narrator.provider} / {self.narrator.model}."),
        )

        initial: GraphState = {"session": session, "events": []}
        emitted = 0
        final_state: Optional[dict] = None

        # LangGraph's stream() yields per-node state updates. We use 'values'
        # mode so we get the merged state after each step (including reducer
        # concatenation of parallel branches).
        try:
            for step in self.graph.stream(initial, stream_mode="values"):
                events_now: list[AgentEvent] = step.get("events", [])
                new = events_now[emitted:]
                for evt in new:
                    yield evt
                emitted = len(events_now)
                final_state = step
        except Exception as exc:
            yield AgentEvent(
                kind=EventKind.ERROR, agent="Orchestrator", emoji="⚠️",
                text=f"Graph crashed: {exc.__class__.__name__}: {exc}",
            )

        # Ensure the terminal DONE event is delivered even if the graph
        # completed silently.
        if not any(e.kind == EventKind.DONE for e in (final_state or {}).get("events", [])):
            yield AgentEvent(
                kind=EventKind.DONE, agent="Orchestrator", emoji="🏁",
                text="Investigation complete.",
            )

        return session

    # Non-streaming convenience — same shape as the previous version
    def run(self, alert_message: str) -> tuple[InvestigationSession, list[AgentEvent]]:
        gen = self.stream(alert_message)
        events: list[AgentEvent] = []
        session: Optional[InvestigationSession] = None
        try:
            while True:
                events.append(next(gen))
        except StopIteration as stop:
            session = stop.value
        return session, events

    # ────────────────────────────────────────────────────────────────
    # Introspection — draw the graph (used in docs / debugging)
    # ────────────────────────────────────────────────────────────────
    def graph_mermaid(self) -> str:
        """Return a Mermaid diagram of the compiled graph."""
        try:
            return self.graph.get_graph().draw_mermaid()
        except Exception as exc:
            return f"(mermaid render failed: {exc})"
