# =============================================================================
# pipeline_discovery.py — DataHub-driven Pipeline Context Builder
# =============================================================================
# Reads the DataHub graph to discover which ML pipelines exist in production,
# what their upstream source tables are, and which columns are business-critical.
#
# No pipeline names, table names, or column names are hardcoded — everything
# comes from what DataHub knows.
#
# Output: PipelineContext, a self-contained snapshot the rest of the agent
# consumes without needing to touch DataHub again during a single investigation.
# =============================================================================

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from src.agent.datahub_gms_client import DatahubGmsClient

logger = logging.getLogger("PipelineDiscovery")

# Platforms considered "ML model artifact stores" by convention.
# Override at runtime via env var ML_PLATFORMS="mlflow,sagemaker,..."
DEFAULT_ML_PLATFORMS = ["mlflow", "sagemaker", "kubeflow", "vertex-ai", "tensorflow", "pytorch"]

# Tags on a source-table column that automatically escalate it to CRITICAL
# regardless of lineage. These are user-supplied signals in DataHub.
CRITICAL_TAGS = {"critical-feature", "ml-input", "ml-target", "pii", "target-variable"}

# Platforms considered "operational data stores" — a source table on one of
# these platforms is the ground truth the agent will introspect for drift.
DEFAULT_SOURCE_PLATFORMS = ["postgres", "mysql", "snowflake", "bigquery", "redshift"]


@dataclass
class ColumnSpec:
    """One column in a discovered source table, with context-derived criticality."""
    column: str
    baseline_type: str
    nullable: bool = True
    description: str = ""
    tags: list[str] = field(default_factory=list)
    severity: str = "MEDIUM"          # LOW / MEDIUM / HIGH / CRITICAL
    reason: str = ""                  # why this severity was chosen (audit trail)


@dataclass
class SourceTableSpec:
    """A source table upstream of at least one protected ML model."""
    table_name: str                   # bare table name for information_schema lookup
    urn: str
    platform: str
    owners: list[str] = field(default_factory=list)
    columns: list[ColumnSpec] = field(default_factory=list)
    downstream_models: list[str] = field(default_factory=list)  # ML model URNs that consume this


@dataclass
class MLModelSpec:
    """A protected ML model."""
    urn: str
    name: str
    platform: str
    tags: list[str] = field(default_factory=list)
    owners: list[str] = field(default_factory=list)
    upstream_tables: list[str] = field(default_factory=list)   # table URNs, multi-hop


@dataclass
class PipelineContext:
    """
    Full discovered snapshot: what to monitor, why, and how it connects.
    Passed to db_inspector, the multi_agent orchestrator, and the Streamlit UI.
    """
    ml_models: list[MLModelSpec] = field(default_factory=list)
    source_tables: dict[str, SourceTableSpec] = field(default_factory=dict)  # keyed by table_name
    ml_platforms_filter: list[str] = field(default_factory=list)
    tag_filter: list[str] = field(default_factory=list)
    discovered_at: str = ""

    @property
    def monitored_table_names(self) -> list[str]:
        return list(self.source_tables.keys())

    def critical_columns_for(self, table_name: str) -> set[str]:
        t = self.source_tables.get(table_name)
        if not t:
            return set()
        return {c.column for c in t.columns if c.severity == "CRITICAL"}

    def all_critical_columns(self) -> set[str]:
        out = set()
        for t in self.source_tables.values():
            out |= self.critical_columns_for(t.table_name)
        return out

    def is_empty(self) -> bool:
        return not self.ml_models or not self.source_tables

    def summary(self) -> str:
        lines = [
            f"Discovered {len(self.ml_models)} ML model(s), "
            f"{len(self.source_tables)} source table(s), "
            f"{len(self.all_critical_columns())} critical column(s).",
        ]
        for m in self.ml_models:
            lines.append(f"  • {m.name} [{m.platform}] ← {len(m.upstream_tables)} upstream table(s)")
        for t in self.source_tables.values():
            crit = self.critical_columns_for(t.table_name)
            lines.append(
                f"  • {t.table_name} [{t.platform}] — {len(t.columns)} cols, "
                f"{len(crit)} critical → feeds {len(t.downstream_models)} model(s)"
            )
        return "\n".join(lines)


# =============================================================================
# Discovery
# =============================================================================

def _parse_table_name_from_urn(urn: str) -> str:
    """
    Extract the bare table name from a dataset URN.
    URN format: urn:li:dataset:(urn:li:dataPlatform:X,DB.SCHEMA.TABLE,ENV)
    Returns lowercase table name only (strips DB.SCHEMA prefix if present).
    """
    if "," not in urn:
        return urn
    middle = urn.split(",")[-2]
    return middle.split(".")[-1].lower()


def _parse_platform_from_urn(urn: str) -> str:
    if "dataPlatform:" not in urn:
        return "unknown"
    return urn.split("dataPlatform:")[-1].split(",")[0].lower()


def _traverse_upstream(
    gms: DatahubGmsClient,
    start_urn: str,
    source_platforms: list[str],
    max_hops: int = 5,
    visited: Optional[set] = None,
) -> list[str]:
    """
    Traverse UPSTREAM from start_urn and collect EVERY entity along the way
    whose platform is in source_platforms. Views and their underlying tables
    are all monitored — a view's upstream base table is a real source too.
    """
    if visited is None:
        visited = set()
    if start_urn in visited or max_hops <= 0:
        return []
    visited.add(start_urn)

    sources: list[str] = []
    upstream = gms.get_lineage(start_urn, direction="UPSTREAM")
    for u in upstream:
        u_urn = u.get("urn", "")
        if not u_urn or u_urn in visited:
            continue
        u_platform = u.get("platform", "").lower() or _parse_platform_from_urn(u_urn)
        if u_platform in [p.lower() for p in source_platforms]:
            sources.append(u_urn)
        # Regardless of whether we collected it or not, keep walking up —
        # a source-platform entity may still be a VIEW with real tables above it.
        sources.extend(
            _traverse_upstream(gms, u_urn, source_platforms, max_hops - 1, visited)
        )
    return sources


def _derive_column_severity(
    column: str,
    baseline_type: str,
    field_tags: list[str],
    description: str,
    is_in_ml_lineage: bool,
) -> tuple[str, str]:
    """
    Context-based severity. Returns (severity, reason).
    Rules ordered by priority:
      1. Field tagged ml-target/target-variable → CRITICAL
      2. Field tagged critical-feature/ml-input → CRITICAL
      3. Field is on an ML platform's lineage path AND type is numeric → CRITICAL
      4. Field tagged pii → HIGH
      5. Field has rich description in DataHub glossary → HIGH (someone cared enough to document it)
      6. Default → MEDIUM
    """
    tag_set = {t.lower() for t in field_tags}

    if tag_set & {"ml-target", "target-variable"}:
        return "CRITICAL", f"Tagged as ML target in DataHub"
    if tag_set & {"critical-feature", "ml-input"}:
        return "CRITICAL", f"Tagged as critical ML input in DataHub"
    if is_in_ml_lineage and baseline_type in ("double precision", "integer", "bigint", "numeric"):
        return "CRITICAL", "Numeric feature on active ML lineage path"
    if "pii" in tag_set:
        return "HIGH", "Tagged as PII in DataHub"
    if description and len(description) > 40:
        return "HIGH", "Column has curated glossary description (business-critical)"
    return "MEDIUM", "Default — no elevated context signal"


def discover_pipeline(
    gms: DatahubGmsClient,
    ml_platforms: Optional[list[str]] = None,
    tag_filter: Optional[list[str]] = None,
    source_platforms: Optional[list[str]] = None,
) -> PipelineContext:
    """
    Full discovery. Returns a PipelineContext.

    Raises RuntimeError if the DataHub graph contains no matching ML model —
    the agent refuses to boot with no protected pipeline (user must register one).
    """
    from datetime import datetime, timezone

    ml_platforms = ml_platforms or [
        p.strip() for p in os.environ.get("ML_PLATFORMS", ",".join(DEFAULT_ML_PLATFORMS)).split(",") if p.strip()
    ]
    tag_filter = tag_filter or [
        t.strip() for t in os.environ.get("ML_TAG_FILTER", "production").split(",") if t.strip()
    ]
    source_platforms = source_platforms or [
        p.strip() for p in os.environ.get("SOURCE_PLATFORMS", ",".join(DEFAULT_SOURCE_PLATFORMS)).split(",") if p.strip()
    ]

    logger.info(
        f"🔎 Discovering pipelines — ml_platforms={ml_platforms}, "
        f"tag_filter={tag_filter}, source_platforms={source_platforms}"
    )

    ml_hits = gms.search_datasets(platforms=ml_platforms, tags=tag_filter, count=50)
    if not ml_hits:
        raise RuntimeError(
            f"No ML models found in DataHub matching platform ∈ {ml_platforms} AND tag ∈ {tag_filter}.\n"
            f"Register at least one ML pipeline before starting the agent.\n"
            f"See metadata/lineage_bootstrap.py for a reference registration script."
        )

    ctx = PipelineContext(
        ml_platforms_filter=ml_platforms,
        tag_filter=tag_filter,
        discovered_at=datetime.now(timezone.utc).isoformat(),
    )

    # Round 1: register each ML model + its upstream source tables
    for hit in ml_hits:
        model_urn = hit["urn"]
        model = MLModelSpec(
            urn=model_urn,
            name=hit["name"],
            platform=hit["platform"] or _parse_platform_from_urn(model_urn),
            tags=hit.get("tags", []),
            owners=gms.get_ownership(model_urn),
        )

        source_urns = _traverse_upstream(gms, model_urn, source_platforms)
        model.upstream_tables = source_urns
        ctx.ml_models.append(model)

        for src_urn in source_urns:
            table_name = _parse_table_name_from_urn(src_urn)
            if table_name in ctx.source_tables:
                # already registered by another ML model — just add to downstream set
                ctx.source_tables[table_name].downstream_models.append(model_urn)
                continue

            src_entity = gms.get_entity(src_urn)
            schema = gms.get_schema_metadata(src_urn)
            owners = gms.get_ownership(src_urn)

            # Is this table's data actually consumed by an ML model? (yes — that's how we found it)
            is_in_ml_lineage = True

            columns = []
            for col_info in schema:
                col = col_info["column"]
                field_tags = gms.get_field_tags(src_urn, col)
                severity, reason = _derive_column_severity(
                    column=col,
                    baseline_type=col_info["type"],
                    field_tags=field_tags,
                    description=col_info.get("description", ""),
                    is_in_ml_lineage=is_in_ml_lineage,
                )
                columns.append(ColumnSpec(
                    column=col,
                    baseline_type=col_info["type"],
                    nullable=col_info.get("nullable", True),
                    description=col_info.get("description", ""),
                    tags=field_tags,
                    severity=severity,
                    reason=reason,
                ))

            ctx.source_tables[table_name] = SourceTableSpec(
                table_name=table_name,
                urn=src_urn,
                platform=src_entity.get("platform") or _parse_platform_from_urn(src_urn),
                owners=owners,
                columns=columns,
                downstream_models=[model_urn],
            )

    logger.info(f"✅ Discovery complete\n{ctx.summary()}")
    return ctx
