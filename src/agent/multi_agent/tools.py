"""
Deterministic tools each agent can call.

Tools are pure functions that wrap the existing db_inspector / datahub_gms_client
/ fix_generator plumbing. Each agent receives a subset via its `allowed_tools`
list — this enforces separation of concern (Detector can't write fix scripts,
FixAuthor can't tag DataHub, etc.), which is what makes the "multi-agent"
setup a real handoff instead of prompt theater.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable

from src.agent.db_inspector import PostgresSchemaInspector, scan_all_drifts
from src.agent.datahub_gms_client import DatahubGmsClient
from src.agent.fix_generator import STRATEGY_BY_DRIFT_TYPE, generate_fix_for_drift
from src.agent.pipeline_discovery import PipelineContext

logger = logging.getLogger("MultiAgent.Tools")

EXAMPLES_DIR = Path(__file__).parent.parent.parent.parent / "examples"


def _bare_table_name(urn: str) -> str:
    if "," not in urn:
        return urn.lower()
    return urn.split(",")[-2].split(".")[-1].lower()


# ─────────────────────────────────────────────────────────────────────────────
# Detector tools
# ─────────────────────────────────────────────────────────────────────────────

def tool_scan_schema(inspector: PostgresSchemaInspector) -> dict:
    """Scan every monitored table against baseline. Returns full drift report."""
    return scan_all_drifts(inspector)


def tool_read_audit_log(inspector: PostgresSchemaInspector, limit: int = 20) -> list[dict]:
    """Return recent, non-INFO, non-reverted entries from schema_change_log."""
    raw = inspector.get_schema_change_log(limit=limit)
    return [
        e for e in raw
        if not e.get("is_reverted", False)
        and e.get("column_name", "") != "__schema_init__"
        and e.get("severity", "INFO") != "INFO"
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Root cause tools
# ─────────────────────────────────────────────────────────────────────────────

def tool_get_lineage(gms: DatahubGmsClient, ctx: PipelineContext) -> tuple[str, list[dict]]:
    """
    Two-hop upstream traversal for every discovered ML model.

    Returns (ascii_trace, structured_edges).
    """
    lines: list[str] = []
    edges: list[dict] = []
    for model in ctx.ml_models:
        lines.append(f"◆ {model.name} [{model.platform}]")
        edges.append({"from": model.urn, "to": None, "level": 0})
        for l1 in gms.get_lineage(model.urn, "UPSTREAM"):
            lines.append(f"  ├─ {l1['name']} ({l1['platform']})")
            edges.append({"from": l1["urn"], "to": model.urn, "level": 1})
            for l2 in gms.get_lineage(l1["urn"], "UPSTREAM"):
                lines.append(f"  │    └─ {l2['name']} ({l2['platform']})")
                edges.append({"from": l2["urn"], "to": l1["urn"], "level": 2})
    return "\n".join(lines) or "(no lineage discovered)", edges


def tool_get_ownership(gms: DatahubGmsClient, ctx: PipelineContext) -> dict:
    """Collect owner emails/urns for every source table in context."""
    out: dict = {}
    for tname, spec in ctx.source_tables.items():
        try:
            owners = gms.get_ownership(spec.urn) or spec.owners
        except Exception:
            owners = spec.owners
        out[tname] = owners
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Impact tools
# ─────────────────────────────────────────────────────────────────────────────

def tool_compute_impact(inspector: PostgresSchemaInspector) -> dict:
    return inspector.get_impact_metrics()


def tool_get_table_stats(inspector: PostgresSchemaInspector) -> dict:
    return inspector.get_table_stats()


def tool_find_impacted_models(ctx: PipelineContext, affected_tables: list[str]) -> list[dict]:
    """Return the ML models whose upstream set intersects the drifted tables."""
    affected_set = {t.lower() for t in affected_tables}
    out: list[dict] = []
    for m in ctx.ml_models:
        upstream_names = {_bare_table_name(u) for u in m.upstream_tables}
        if upstream_names & affected_set:
            out.append({
                "urn": m.urn, "name": m.name, "platform": m.platform,
                "tags": m.tags, "owners": m.owners,
            })
    return out


def tool_estimate_dollar_impact(
    n_critical_drifts: int,
    total_rows: int,
    impacted_models: int,
    fraud_rate_pct: float,
) -> float:
    """
    Rough hourly loss estimate. Not a real model — deliberately transparent
    heuristic so we can show a number without pretending it's a forecast.

    Assumptions (documented so the demo is honest):
      - Each impacted production ML model = ~$5k/hour baseline exposure
      - Each critical drift multiplies exposure by 1.5x
      - Empty tables → $0 (no traffic)
    """
    if total_rows == 0 or impacted_models == 0:
        return 0.0
    base = 5000.0 * impacted_models
    multiplier = max(1.0, 1.5 * n_critical_drifts) if n_critical_drifts else 1.0
    scale = min(1.0, total_rows / 50_000.0)  # dampen for tiny demo tables
    return round(base * multiplier * scale, 2)


# ─────────────────────────────────────────────────────────────────────────────
# Fix author tools
# ─────────────────────────────────────────────────────────────────────────────

def tool_generate_fix_scripts(drifts: list[dict]) -> dict:
    """
    Generate a fix for every drift, dispatching to the strategy matching its
    drift_type (see fix_generator.STRATEGY_BY_DRIFT_TYPE):
      TYPE_CHANGE    -> cast_back            (ALTER ... USING CAST)
      COLUMN_MISSING -> re_add_column        (ALTER ... ADD COLUMN, data-loss warned)
      COLUMN_ADDED   -> accept_into_baseline (register as expected, no rollback)

    Each drift_type needs a genuinely different remediation — this is NOT
    the same template with swapped parameters. Drifts with no defined
    strategy are reported separately rather than silently skipped, so
    FixAuthor (and the Reviewer) can see the gap instead of it disappearing.

    Returns {key: result_dict}. Each result_dict includes `strategy` so the
    LLM can explain which one it used and why.
    """
    EXAMPLES_DIR.mkdir(exist_ok=True)
    results: dict = {}
    unhandled: list[str] = []
    for d in drifts:
        res = generate_fix_for_drift(d, output_dir=str(EXAMPLES_DIR))
        key = f"{d['table']}.{d['column']}"
        if res is None:
            unhandled.append(f"{key} (drift_type={d.get('drift_type')})")
            continue
        results[key] = res
    if unhandled:
        results["_unhandled"] = unhandled
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Reviewer tools
# ─────────────────────────────────────────────────────────────────────────────

# Patterns the reviewer will flag as unsafe.
_UNSAFE_PATTERNS = [
    (r"\bDROP\s+TABLE\b", "DROP TABLE without explicit approval"),
    (r"\bTRUNCATE\b", "TRUNCATE erases data"),
    (r"\bDELETE\s+FROM\s+\w+\s*;", "DELETE without WHERE clause"),
    (r"\bGRANT\s+ALL\b", "GRANT ALL is over-privileged"),
]


def tool_validate_fix_safety(fix_scripts: dict) -> dict:
    """
    Static safety check on generated fix scripts.

    Rules:
      - No unsafe SQL patterns (see _UNSAFE_PATTERNS)
      - Every fix must be wrapped in BEGIN...COMMIT
      - Every fix must include a verification step (RAISE NOTICE)

    Returns {verdict, issues, checked_files}. verdict is APPROVE | REJECT_UNSAFE
    | REJECT_INCOMPLETE.
    """
    issues: list[dict] = []
    checked: list[str] = []
    if not fix_scripts:
        return {
            "verdict": "REJECT_INCOMPLETE",
            "issues": [{"file": "-", "reason": "No fix scripts produced"}],
            "checked_files": [],
        }

    for key, fs in fix_scripts.items():
        if key == "_unhandled":
            for entry in fs:
                issues.append({"file": entry, "reason": "No remediation strategy defined for this drift_type"})
            continue
        sql_path = fs.get("sql_fix_path")
        if not sql_path or not Path(sql_path).exists():
            issues.append({"file": key, "reason": "SQL fix file missing on disk"})
            continue
        checked.append(sql_path)
        content = Path(sql_path).read_text()

        # Strip SQL line comments so pattern rules only see executable SQL.
        code_only = re.sub(r"--[^\n]*", "", content)

        for pattern, reason in _UNSAFE_PATTERNS:
            if re.search(pattern, code_only, flags=re.IGNORECASE):
                issues.append({"file": sql_path, "reason": reason})

        if "BEGIN" not in code_only.upper() or "COMMIT" not in code_only.upper():
            issues.append({"file": sql_path, "reason": "Missing BEGIN/COMMIT wrapping"})

        if "RAISE NOTICE" not in code_only.upper():
            issues.append({"file": sql_path, "reason": "No post-fix verification step"})

    unsafe = any("without explicit" in i["reason"] or "erases" in i["reason"]
                 or "without WHERE" in i["reason"] or "over-privileged" in i["reason"]
                 for i in issues)

    if unsafe:
        verdict = "REJECT_UNSAFE"
    elif issues:
        verdict = "REJECT_INCOMPLETE"
    else:
        verdict = "APPROVE"

    return {"verdict": verdict, "issues": issues, "checked_files": checked}


# ─────────────────────────────────────────────────────────────────────────────
# Tool registry
# ─────────────────────────────────────────────────────────────────────────────

class ToolRegistry:
    """Bundles the shared dependencies so agents can request tools by name."""

    def __init__(
        self,
        inspector: PostgresSchemaInspector,
        gms: DatahubGmsClient,
        ctx: PipelineContext,
    ):
        self.inspector = inspector
        self.gms = gms
        self.ctx = ctx

        self._tools: dict[str, Callable[..., Any]] = {
            "scan_schema":            lambda: tool_scan_schema(inspector),
            "read_audit_log":         lambda: tool_read_audit_log(inspector),
            "get_lineage":            lambda: tool_get_lineage(gms, ctx),
            "get_ownership":          lambda: tool_get_ownership(gms, ctx),
            "compute_impact":         lambda: tool_compute_impact(inspector),
            "get_table_stats":        lambda: tool_get_table_stats(inspector),
            "find_impacted_models":   lambda affected_tables: tool_find_impacted_models(ctx, affected_tables),
            "estimate_dollar_impact": tool_estimate_dollar_impact,
            "generate_fix_scripts":   tool_generate_fix_scripts,
            "validate_fix_safety":    tool_validate_fix_safety,
        }

    def call(self, name: str, *args, **kwargs) -> Any:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        return self._tools[name](*args, **kwargs)

    def has(self, name: str) -> bool:
        return name in self._tools
