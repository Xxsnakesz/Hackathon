# =============================================================================
# db_inspector.py — Real PostgreSQL Schema Inspector (v2 — Full Real)
# =============================================================================
# Connects to PostgreSQL and reads:
#   1. Actual column types from information_schema.columns
#   2. Schema change history from schema_change_log table
#   3. Baseline schema from schema_baseline table
#   4. Impact metrics computed from real data (COUNT, NULL %, cast failures)
#
# Falls back to full in-memory simulation if PostgreSQL is not reachable.
# Simulation mode is stateful and mutable — drifts/resets persist in-session.
# =============================================================================

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("DBInspector")

# Path to the baseline JSON (used in simulation mode fallback only)
BASELINE_PATH = Path(__file__).parent / "schema_baseline.json"

# Default fallback list — used only when no PipelineContext has been injected.
# In normal (context-based) operation these are set from DataHub discovery.
MONITORED_TABLES = ["paysim_raw_transactions", "feature_engineering_table"]

# Default fallback critical columns — same story: overridden by PipelineContext.
CRITICAL_COLUMNS = {
    "amount", "oldbalanceorg", "newbalanceorig",
    "oldbalancedest", "newbalancedest", "isfraud", "isflaggedfraud",
}

# Preset drift scenarios metadata (for UI rendering)
DRIFT_SCENARIOS = {
    "A": {
        "name": "ETL Refactor Gone Wrong",
        "description": "Engineer changes `amount` to VARCHAR during ETL pipeline refactor",
        "emoji": "💣",
        "impact": "ALL numeric ML features break — amount_ratio, is_large_transaction",
        "changed_by_default": "bambang_engineer",
        "reason_default": "ETL pipeline refactor — column type changed unintentionally during migration",
        "drifts": [
            {"table": "paysim_raw_transactions", "column": "amount", "new_type": "character varying"}
        ],
    },
    "B": {
        "name": "Fraud Label Corruption",
        "description": "Data pipeline outputs isFraud as string 'true'/'false' instead of 0/1",
        "emoji": "☣️",
        "impact": "ML model target label becomes string — model cannot be trained or scored",
        "changed_by_default": "data_pipeline_v3",
        "reason_default": "CSV ingestion pipeline v3 updated — writes fraud label as string instead of integer",
        "drifts": [
            {"table": "paysim_raw_transactions", "column": "isfraud", "new_type": "character varying"}
        ],
    },
    "C": {
        "name": "Balance Column Downgrade",
        "description": "DBA converts balance columns to TEXT for 'storage optimization'",
        "emoji": "📉",
        "impact": "balance_change_orig and balance_change_dest features break",
        "changed_by_default": "dba_team",
        "reason_default": "Storage optimization initiative — converting numeric balance columns to text",
        "drifts": [
            {"table": "paysim_raw_transactions", "column": "oldbalanceorg", "new_type": "character varying"},
            {"table": "paysim_raw_transactions", "column": "newbalanceorig", "new_type": "character varying"},
        ],
    },
}


# =============================================================================
# Simulation State
# =============================================================================

def _load_baseline_json() -> dict:
    """Load the baseline schema from the JSON file (for simulation mode)."""
    with open(BASELINE_PATH, "r") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


_SIM_SCHEMA_STATE: dict = {}     # Mutable schema state
_SIM_CHANGE_LOG: list  = []      # In-memory audit trail


def _get_sim_schema() -> dict:
    global _SIM_SCHEMA_STATE
    if not _SIM_SCHEMA_STATE:
        _SIM_SCHEMA_STATE = _load_baseline_json()
    return _SIM_SCHEMA_STATE


def reset_sim_state():
    """Reset simulation state to baseline and log the reset."""
    global _SIM_SCHEMA_STATE, _SIM_CHANGE_LOG
    _SIM_SCHEMA_STATE = _load_baseline_json()
    # Mark all active drifts in log as reverted
    for entry in _SIM_CHANGE_LOG:
        if not entry.get("is_reverted") and entry.get("severity") != "INFO":
            entry["is_reverted"] = True
            entry["reverted_at"] = datetime.now(timezone.utc).isoformat()
            entry["notes"] = "Reverted via Reset to Baseline"
    logger.info("[SIM] All columns reset to baseline.")


def apply_sim_drift(table: str, column: str, new_type: str,
                    changed_by: str = "unknown", reason: str = "No reason provided",
                    severity: str = "HIGH"):
    """Apply a simulated schema drift to the in-memory state and log it."""
    state = _get_sim_schema()
    if table not in state:
        raise ValueError(f"Table '{table}' not in simulation state")
    for field in state[table]:
        if field["column"].lower() == column.lower():
            old_type = field["type"]
            field["_original_type"] = old_type
            field["type"] = new_type.lower()
            entry = {
                "id": len(_SIM_CHANGE_LOG) + 1,
                "table_name": table,
                "column_name": column.lower(),
                "old_type": old_type,
                "new_type": new_type.lower(),
                "changed_by": changed_by,
                "change_reason": reason,
                "changed_at": datetime.now(timezone.utc).isoformat(),
                "reverted_at": None,
                "is_reverted": False,
                "severity": severity,
                "notes": None,
            }
            _SIM_CHANGE_LOG.append(entry)
            logger.info(f"[SIM] Drift: {table}.{column} {old_type} → {new_type}")
            return
    raise ValueError(f"Column '{column}' not found in table '{table}'")


def get_sim_change_log() -> list:
    return list(_SIM_CHANGE_LOG)


# =============================================================================
# PostgreSQL Inspector
# =============================================================================

class PostgresSchemaInspector:
    """
    Inspects actual PostgreSQL schema and reads the schema_change_log.
    Falls back to mutable simulation if DB is not reachable.
    """

    def __init__(
        self,
        host: str = None, port: int = None,
        database: str = None, user: str = None, password: str = None,
        pipeline_context=None,
    ):
        # Try loading .env if available
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass

        # Note: docker-compose maps host:5433 → container:5432
        self.host     = host     or os.environ.get("POSTGRES_HOST", "localhost")
        self.port     = int(port or os.environ.get("POSTGRES_PORT", "5433"))
        self.database = database or os.environ.get("POSTGRES_DB", "paysim_fintech")
        self.user     = user     or os.environ.get("POSTGRES_USER", "paysim_user")
        self.password = password or os.environ.get("POSTGRES_PASSWORD", "paysim_secret_2026")

        # PipelineContext (from pipeline_discovery.discover_pipeline) — when set,
        # overrides MONITORED_TABLES / CRITICAL_COLUMNS / baseline source with
        # what DataHub knows. None = fall back to hardcoded PaySim defaults.
        self.pipeline_context = pipeline_context

        self._conn = None
        self._mode: str = "unknown"
        self._connect()

    def monitored_tables(self) -> list[str]:
        """
        Effective list of tables to monitor (context-aware).
        Views are excluded — their schema is derived from base tables and
        cannot be independently ALTERed. If the underlying base table drifts,
        the view breaks automatically; monitoring the base table is enough.
        """
        candidates = (
            self.pipeline_context.monitored_table_names
            if self.pipeline_context is not None else None
        ) or MONITORED_TABLES

        if not self.is_live():
            return candidates

        # Filter out views by querying pg_class
        try:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT c.relname
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                  AND c.relkind = 'r'      -- ordinary tables only, not views
                  AND c.relname = ANY(%s)
                """,
                (candidates,),
            )
            base_tables = {r[0] for r in cur.fetchall()}
            cur.close()
            self._conn.commit()  # end the transaction now — don't hold a lock while idle
            filtered = [t for t in candidates if t in base_tables]
            skipped = [t for t in candidates if t not in base_tables]
            if skipped:
                logger.info(f"Skipping non-base-table entities (views): {skipped}")
            return filtered or candidates  # fallback if filter matched nothing
        except Exception as exc:
            self._safe_rollback()
            logger.warning(f"Could not filter views out of monitored tables: {exc}")
            return candidates

    def critical_columns_for(self, table_name: str) -> set[str]:
        """Effective set of critical columns for a given table (context-aware)."""
        if self.pipeline_context is not None:
            crit = self.pipeline_context.critical_columns_for(table_name)
            if crit:
                return crit
        return CRITICAL_COLUMNS  # fallback

    def _connect(self):
        try:
            import psycopg2
            self._conn = psycopg2.connect(
                host=self.host, port=self.port,
                database=self.database, user=self.user,
                password=self.password, connect_timeout=5,
            )
            self._conn.autocommit = False
            self._mode = "live"
            logger.info(f"✅ Connected to PostgreSQL at {self.host}:{self.port}/{self.database}")
        except Exception as exc:
            self._conn = None
            self._mode = "simulation"
            logger.warning(f"⚠️  PostgreSQL unreachable ({exc}). Switching to SIMULATION mode.")

    def is_live(self) -> bool:
        return self._mode == "live"

    def mode(self) -> str:
        return self._mode

    def _safe_rollback(self):
        """
        Roll back the current transaction after a failed query.

        With autocommit=False, one failed statement leaves the connection in
        PostgreSQL's "current transaction is aborted" state — every
        subsequent query on that same connection fails with the same error
        until a ROLLBACK is issued, even for completely unrelated queries.
        Because `self._conn` is a long-lived object cached across many
        Streamlit reruns, a single unhandled failure anywhere would silently
        break every future read. Call this from every except block that
        catches a query failure.
        """
        if self._conn:
            try:
                self._conn.rollback()
            except Exception:
                pass  # connection itself is dead; _connect() will replace it next use

    # ─────────────────────────────────────────────
    # Schema Inspection
    # ─────────────────────────────────────────────

    def get_schema(self, table_name: str, schema_name: str = "public") -> list[dict]:
        """Return actual column list: [{column, type, nullable}]"""
        if self.is_live():
            return self._live_schema(table_name, schema_name)
        return self._sim_schema(table_name)

    def _live_schema(self, table_name: str, schema_name: str) -> list[dict]:
        try:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s
                ORDER BY ordinal_position
                """,
                (schema_name, table_name),
            )
            rows = cur.fetchall()
            cur.close()
            self._conn.commit()  # end the transaction now — don't hold a lock while idle
            return [
                {"column": r[0].lower(), "type": r[1].lower(), "nullable": r[2].upper() == "YES"}
                for r in rows
            ]
        except Exception as exc:
            logger.error(f"Error reading schema for {table_name}: {exc}")
            self._safe_rollback()
            self._connect()
            return []

    def _sim_schema(self, table_name: str) -> list[dict]:
        state = _get_sim_schema()
        return [
            {"column": f["column"].lower(), "type": f["type"].lower(),
             "nullable": f.get("nullable", True)}
            for f in state.get(table_name, [])
        ]

    def get_all_monitored_schemas(self) -> dict:
        return {t: self.get_schema(t) for t in self.monitored_tables()}

    # ─────────────────────────────────────────────
    # Baseline from DB
    # ─────────────────────────────────────────────

    def get_baseline_from_db(self, table_name: str) -> list[dict]:
        """
        Return baseline schema for a table. Priority:
          1. PipelineContext (DataHub schemaMetadata) — most authoritative
          2. schema_baseline table in Postgres
          3. schema_baseline.json fallback
        """
        # 1. PipelineContext (DataHub-driven)
        if self.pipeline_context is not None:
            t = self.pipeline_context.source_tables.get(table_name)
            if t and t.columns:
                return [
                    {
                        "column": c.column,
                        "type": c.baseline_type,
                        "nullable": c.nullable,
                        "is_critical": c.severity == "CRITICAL",
                        "description": c.description,
                    }
                    for c in t.columns
                ]

        if not self.is_live():
            return _load_baseline_json().get(table_name, [])
        try:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT column_name, expected_type, is_nullable, is_critical, description
                FROM schema_baseline
                WHERE table_name = %s
                ORDER BY id
                """,
                (table_name,),
            )
            rows = cur.fetchall()
            cur.close()
            self._conn.commit()  # end the transaction now — don't hold a lock while idle
            return [
                {
                    "column": r[0], "type": r[1],
                    "nullable": r[2], "is_critical": r[3],
                    "description": r[4],
                }
                for r in rows
            ]
        except Exception as exc:
            self._safe_rollback()
            logger.warning(f"Cannot read schema_baseline from DB: {exc}. Using JSON fallback.")
            return _load_baseline_json().get(table_name, [])

    # ─────────────────────────────────────────────
    # Schema Change Log
    # ─────────────────────────────────────────────

    def get_schema_change_log(self, table_name: str = None, limit: int = 100) -> list[dict]:
        """
        Read schema change history from schema_change_log table (or sim log).
        If table_name is None, returns entries for all tables.
        """
        if not self.is_live():
            logs = get_sim_change_log()
            if table_name:
                logs = [l for l in logs if l["table_name"] == table_name]
            return logs[-limit:]

        try:
            cur = self._conn.cursor()
            if table_name:
                cur.execute(
                    """
                    SELECT id, table_name, column_name, old_type, new_type,
                           changed_by, change_reason, changed_at,
                           reverted_at, is_reverted, severity, notes
                    FROM schema_change_log
                    WHERE table_name = %s
                    ORDER BY changed_at DESC
                    LIMIT %s
                    """,
                    (table_name, limit),
                )
            else:
                cur.execute(
                    """
                    SELECT id, table_name, column_name, old_type, new_type,
                           changed_by, change_reason, changed_at,
                           reverted_at, is_reverted, severity, notes
                    FROM schema_change_log
                    ORDER BY changed_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
            rows = cur.fetchall()
            cur.close()
            self._conn.commit()  # end the transaction now — don't hold a lock while idle
            cols = ["id", "table_name", "column_name", "old_type", "new_type",
                    "changed_by", "change_reason", "changed_at",
                    "reverted_at", "is_reverted", "severity", "notes"]
            return [dict(zip(cols, r)) for r in rows]
        except Exception as exc:
            self._safe_rollback()
            logger.warning(f"Cannot read schema_change_log: {exc}")
            return []

    def log_schema_change_to_db(
        self, table: str, column: str, old_type: str, new_type: str,
        changed_by: str, reason: str, severity: str = "HIGH",
    ) -> bool:
        """Write a schema change event to schema_change_log."""
        if not self.is_live():
            apply_sim_drift(table, column, new_type, changed_by, reason, severity)
            return True
        try:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO schema_change_log
                    (table_name, column_name, old_type, new_type, changed_by, change_reason, severity)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (table, column.lower(), old_type, new_type, changed_by, reason, severity),
            )
            self._conn.commit()
            cur.close()
            return True
        except Exception as exc:
            self._conn.rollback()
            logger.error(f"Failed to log schema change: {exc}")
            return False

    def mark_change_reverted(self, table: str, column: str) -> bool:
        """Mark all active drift entries for a column as reverted."""
        if not self.is_live():
            for entry in _SIM_CHANGE_LOG:
                if (entry["table_name"] == table and
                        entry["column_name"] == column.lower() and
                        not entry["is_reverted"]):
                    entry["is_reverted"] = True
                    entry["reverted_at"] = datetime.now(timezone.utc).isoformat()
            return True
        try:
            cur = self._conn.cursor()
            cur.execute(
                """
                UPDATE schema_change_log
                SET is_reverted = TRUE, reverted_at = NOW(), notes = 'Reverted via AI Agent / UI'
                WHERE table_name = %s AND column_name = %s AND is_reverted = FALSE
                """,
                (table, column.lower()),
            )
            self._conn.commit()
            cur.close()
            return True
        except Exception as exc:
            self._conn.rollback()
            logger.error(f"Failed to mark revert: {exc}")
            return False

    # ─────────────────────────────────────────────
    # Apply / Revert Drift
    # ─────────────────────────────────────────────

    def apply_drift_to_db(
        self, table: str, column: str, new_pg_type: str,
        changed_by: str = "unknown", reason: str = "No reason provided",
        severity: str = "HIGH", schema_name: str = "public",
    ) -> tuple[bool, str]:
        """
        ALTER TABLE to change a column type.
        Returns (success, message).
        Also records the change in schema_change_log.
        """
        # Get current type before altering
        current_schema = self.get_schema(table, schema_name)
        current_field = next(
            (f for f in current_schema if f["column"].lower() == column.lower()), None
        )
        old_type = current_field["type"] if current_field else "unknown"

        if not self.is_live():
            apply_sim_drift(table, column, new_pg_type, changed_by, reason, severity)
            msg = f"[SIM] {table}.{column}: {old_type} → {new_pg_type}"
            logger.info(msg)
            return True, msg

        # --- Live DB: ALTER TABLE ---
        # Postgres refuses to alter a column that is referenced by a view.
        # Approach: drop dependent views + ALTER in one transaction; commit;
        # then try to recreate views in a separate transaction. If a view
        # can't be rebuilt (e.g. a numeric expression on a now-VARCHAR
        # column), we cache its definition so restore can rebuild it once
        # the column type is put back.
        safe_type = new_pg_type.upper().replace(";", "").replace("--", "")
        # Ensure the cache dict exists (persist across calls on the same instance)
        if not hasattr(self, "_dropped_view_defs"):
            self._dropped_view_defs = {}
        try:
            cur = self._conn.cursor()

            # Find views that reference this column
            cur.execute(
                """
                SELECT DISTINCT v.viewname, pg_get_viewdef(c.oid, true) AS def
                FROM pg_class c
                JOIN pg_views v ON v.viewname = c.relname AND v.schemaname = %s
                JOIN pg_rewrite r ON r.ev_class = c.oid
                JOIN pg_depend d ON d.objid = r.oid
                JOIN pg_attribute a ON a.attrelid = d.refobjid AND a.attnum = d.refobjsubid
                JOIN pg_class t ON t.oid = d.refobjid
                WHERE c.relkind = 'v'
                  AND t.relname = %s
                  AND a.attname = %s
                """,
                (schema_name, table, column.lower()),
            )
            dependent_views = cur.fetchall()

            for view_name, view_def in dependent_views:
                cur.execute(f"DROP VIEW {schema_name}.{view_name} CASCADE")
                # Cache the definition so restore can recreate it later
                self._dropped_view_defs[view_name] = view_def
                logger.info(f"  ↪ Dropped dependent view: {view_name}")

            cur.execute(
                f"ALTER TABLE {schema_name}.{table} "
                f"ALTER COLUMN {column} TYPE {safe_type} "
                f"USING {column}::text::{safe_type}"
            )
            self._conn.commit()
            cur.close()

            # Now attempt to recreate the views. Each in its own transaction —
            # if a view fails (expression no longer compiles), we skip it and
            # keep the ALTER committed.
            broken_views: list[str] = []
            for view_name, view_def in dependent_views:
                try:
                    cur = self._conn.cursor()
                    cur.execute(f"CREATE VIEW {schema_name}.{view_name} AS {view_def}")
                    self._conn.commit()
                    cur.close()
                    logger.info(f"  ↪ Recreated view: {view_name}")
                except Exception as view_exc:
                    self._conn.rollback()
                    broken_views.append(view_name)
                    logger.warning(
                        f"  ↪ Could not recreate view {view_name} (kept in cache for restore): {view_exc}"
                    )

            self.log_schema_change_to_db(table, column, old_type, new_pg_type, changed_by, reason, severity)
            msg = f"Altered {table}.{column}: {old_type} → {new_pg_type}"
            if broken_views:
                msg += f" (view(s) unavailable until restored: {', '.join(broken_views)})"
            elif dependent_views:
                msg += f" (recreated {len(dependent_views)} dependent view(s))"
            logger.info(f"✅ {msg}")
            return True, msg
        except Exception as exc:
            self._conn.rollback()
            msg = f"Failed: {exc}"
            logger.error(f"❌ {msg}")
            return False, msg

    def restore_column_to_baseline(
        self, table: str, column: str, schema_name: str = "public"
    ) -> tuple[bool, str]:
        """Restore a column to its baseline type."""
        baseline = self.get_baseline_from_db(table)
        target = next((f for f in baseline if f["column"].lower() == column.lower()), None)
        if not target:
            return False, f"Column '{column}' not in baseline for table '{table}'"

        baseline_type = target["type"]

        if not self.is_live():
            state = _get_sim_schema()
            for field in state.get(table, []):
                if field["column"].lower() == column.lower():
                    field["type"] = baseline_type
            self.mark_change_reverted(table, column)
            return True, f"[SIM] {table}.{column} restored to {baseline_type}"

        # Convert baseline type string to valid DDL type
        pg_type_ddl_map = {
            "integer":           "INTEGER",
            "double precision":  "DOUBLE PRECISION",
            "character varying": "VARCHAR",
            "varchar":           "VARCHAR",
            "text":              "TEXT",
            "boolean":           "BOOLEAN",
            "bigint":            "BIGINT",
        }
        ddl_type = pg_type_ddl_map.get(baseline_type.lower(), baseline_type.upper())

        cast_type = ddl_type.lower().replace(" ", "_") if ddl_type.lower() != "double precision" else "double precision"
        if not hasattr(self, "_dropped_view_defs"):
            self._dropped_view_defs = {}
        try:
            cur = self._conn.cursor()

            # Views still present in the DB that reference this column
            cur.execute(
                """
                SELECT DISTINCT v.viewname, pg_get_viewdef(c.oid, true) AS def
                FROM pg_class c
                JOIN pg_views v ON v.viewname = c.relname AND v.schemaname = %s
                JOIN pg_rewrite r ON r.ev_class = c.oid
                JOIN pg_depend d ON d.objid = r.oid
                JOIN pg_attribute a ON a.attrelid = d.refobjid AND a.attnum = d.refobjsubid
                JOIN pg_class t ON t.oid = d.refobjid
                WHERE c.relkind = 'v'
                  AND t.relname = %s
                  AND a.attname = %s
                """,
                (schema_name, table, column.lower()),
            )
            live_views = cur.fetchall()

            for view_name, _ in live_views:
                cur.execute(f"DROP VIEW {schema_name}.{view_name} CASCADE")

            cur.execute(
                f"ALTER TABLE {schema_name}.{table} "
                f"ALTER COLUMN {column} TYPE {ddl_type} "
                f"USING {column}::text::{cast_type}"
            )

            # Merge: recreate views that were live plus any cached broken ones
            to_recreate = {name: defn for name, defn in live_views}
            to_recreate.update(self._dropped_view_defs)  # cached views take priority

            for view_name, view_def in to_recreate.items():
                cur.execute(f"CREATE VIEW {schema_name}.{view_name} AS {view_def}")

            self._conn.commit()
            cur.close()
            self.mark_change_reverted(table, column)

            # Views are alive again; clear the drop cache
            self._dropped_view_defs = {}

            msg = f"Restored {table}.{column} → {ddl_type}"
            if to_recreate:
                msg += f" (recreated {len(to_recreate)} view(s))"
            logger.info(f"✅ {msg}")
            return True, msg
        except Exception as exc:
            self._conn.rollback()
            msg = f"Failed to restore {table}.{column}: {exc}"
            logger.error(f"❌ {msg}")
            return False, msg

    def restore_all_to_baseline(self) -> dict:
        """Restore all drifted columns back to their baseline types."""
        current_report = scan_all_drifts(self)
        results = {"restored": [], "failed": [], "skipped": []}

        for table_name, info in current_report["tables"].items():
            for drift in info.get("drifts", []):
                if drift["drift_type"] == "TYPE_CHANGE":
                    ok, msg = self.restore_column_to_baseline(table_name, drift["column"])
                    if ok:
                        results["restored"].append(f"{table_name}.{drift['column']}")
                    else:
                        results["failed"].append(f"{table_name}.{drift['column']}: {msg}")
                elif drift["drift_type"] == "COLUMN_MISSING":
                    results["skipped"].append(f"{table_name}.{drift['column']} (missing — cannot auto-restore)")

        return results

    # ─────────────────────────────────────────────
    # Impact Metrics (from real data)
    # ─────────────────────────────────────────────

    def get_impact_metrics(self, table: str = "paysim_raw_transactions") -> dict:
        """
        Compute real data impact metrics for the current schema state.
        Returns metrics like: total_rows, pct_numeric_amount_valid,
        pct_fraud_label_valid, null_counts, etc.
        """
        if not self.is_live():
            return self._sim_impact_metrics()

        metrics = {
            "mode": "live",
            "table": table,
            "total_rows": 0,
            "columns": {},
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            cur = self._conn.cursor()

            # Total rows
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            metrics["total_rows"] = cur.fetchone()[0]

            if metrics["total_rows"] == 0:
                cur.close()
                self._conn.commit()  # end the transaction now — don't hold a lock while idle
                metrics["warning"] = "Table is empty — seed data first."
                return metrics

            # Per-column metrics for critical columns
            critical_cols = {
                "amount":       "double precision",
                "oldbalanceorg": "double precision",
                "isfraud":       "integer",
                "isflaggedfraud": "integer",
            }

            for col, expected_type in critical_cols.items():
                col_metrics = {}

                # Check if column exists in actual schema
                cur.execute(
                    """
                    SELECT data_type FROM information_schema.columns
                    WHERE table_name = %s AND column_name = %s
                    """,
                    (table, col),
                )
                row = cur.fetchone()
                if not row:
                    col_metrics["status"] = "COLUMN_MISSING"
                    metrics["columns"][col] = col_metrics
                    continue

                actual_type = row[0]
                col_metrics["actual_type"] = actual_type
                col_metrics["expected_type"] = expected_type
                col_metrics["type_ok"] = (actual_type.lower() == expected_type.lower())

                # NULL count
                cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} IS NULL")
                col_metrics["null_count"] = cur.fetchone()[0]
                col_metrics["null_pct"] = round(
                    100.0 * col_metrics["null_count"] / metrics["total_rows"], 2
                )

                # If type is wrong (text/varchar), check how many values can be cast back
                if not col_metrics["type_ok"] and actual_type.lower() in ("character varying", "text", "varchar"):
                    if expected_type == "double precision":
                        cur.execute(
                            f"""
                            SELECT COUNT(*) FROM {table}
                            WHERE {col} ~ '^[-+]?[0-9]*\\.?[0-9]+([eE][-+]?[0-9]+)?$'
                            """
                        )
                        castable = cur.fetchone()[0]
                        col_metrics["castable_to_numeric"] = castable
                        col_metrics["castable_pct"] = round(100.0 * castable / metrics["total_rows"], 2)
                        col_metrics["corrupted_count"] = metrics["total_rows"] - castable
                        col_metrics["corrupted_pct"] = round(
                            100.0 * col_metrics["corrupted_count"] / metrics["total_rows"], 2
                        )
                    elif expected_type == "integer":
                        cur.execute(
                            f"SELECT COUNT(*) FROM {table} WHERE {col} ~ '^[0-9]+$'"
                        )
                        castable = cur.fetchone()[0]
                        col_metrics["castable_to_integer"] = castable
                        col_metrics["castable_pct"] = round(100.0 * castable / metrics["total_rows"], 2)

                metrics["columns"][col] = col_metrics

            cur.close()
            self._conn.commit()  # end the transaction now — don't hold a lock while idle
        except Exception as exc:
            self._safe_rollback()
            logger.error(f"Error computing impact metrics: {exc}")
            metrics["error"] = str(exc)

        return metrics

    def _sim_impact_metrics(self) -> dict:
        """Return simulated impact metrics based on current sim state."""
        state = _get_sim_schema()
        baseline = _load_baseline_json()

        # Compute drifts in sim
        drifts_sim = []
        for table, fields in state.items():
            base_fields = {f["column"].lower(): f for f in baseline.get(table, [])}
            for f in fields:
                col = f["column"].lower()
                if col in base_fields and f["type"].lower() != base_fields[col]["type"].lower():
                    drifts_sim.append(f)

        sim_total = 50000  # Simulated row count
        sim_drift_count = len(drifts_sim)

        return {
            "mode": "simulation",
            "table": "paysim_raw_transactions",
            "total_rows": sim_total,
            "simulated_row_count": sim_total,
            "active_drifts": sim_drift_count,
            "columns": {
                "amount": {
                    "type_ok": not any(f["column"] == "amount" for f in drifts_sim),
                    "null_count": 0,
                    "null_pct": 0.0,
                },
                "isfraud": {
                    "type_ok": not any(f["column"] == "isfraud" for f in drifts_sim),
                    "null_count": 0,
                    "null_pct": 0.0,
                },
            },
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }

    def get_table_stats(self, table: str = "paysim_raw_transactions") -> dict:
        """Basic stats: row count, fraud count, avg amount, transaction type breakdown."""
        if not self.is_live():
            return {
                "mode": "simulation",
                "total_rows": 50000,
                "fraud_rows": 82,
                "fraud_rate_pct": 0.16,
                "avg_amount": 178234.52,
                "transaction_types": {"PAYMENT": 18207, "TRANSFER": 5431, "CASH_OUT": 21626, "DEBIT": 2151, "CASH_IN": 2585},
                "note": "Simulated values based on PaySim dataset distribution",
            }
        try:
            cur = self._conn.cursor()
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            total = cur.fetchone()[0]
            if total == 0:
                cur.close()
                self._conn.commit()  # end the transaction now — don't hold a lock while idle
                return {"mode": "live", "total_rows": 0, "note": "Table is empty — run seed first"}

            # Fraud count (only works if isFraud is integer)
            fraud_count = 0
            try:
                cur.execute(f"SELECT COUNT(*) FROM {table} WHERE isfraud = 1")
                fraud_count = cur.fetchone()[0]
            except Exception:
                # A drifted isfraud column (e.g. cast to VARCHAR by Scenario B)
                # makes `isfraud = 1` fail with a type-mismatch error, which
                # poisons the transaction for every query after it — roll
                # back so avg_amount/type_breakdown below can still run.
                self._safe_rollback()
                cur = self._conn.cursor()

            # Avg amount (only works if amount is numeric)
            avg_amount = None
            try:
                cur.execute(f"SELECT ROUND(AVG(amount)::numeric, 2) FROM {table}")
                avg_amount = float(cur.fetchone()[0] or 0)
            except Exception:
                self._safe_rollback()
                cur = self._conn.cursor()

            # Transaction type breakdown
            type_breakdown = {}
            try:
                cur.execute(f"SELECT type, COUNT(*) FROM {table} GROUP BY type ORDER BY COUNT(*) DESC")
                type_breakdown = {r[0]: r[1] for r in cur.fetchall()}
            except Exception:
                self._safe_rollback()
                cur = self._conn.cursor()

            cur.close()
            self._conn.commit()  # end the transaction now — don't hold a lock while idle
            return {
                "mode": "live",
                "total_rows": total,
                "fraud_rows": fraud_count,
                "fraud_rate_pct": round(100.0 * fraud_count / total, 4) if total else 0,
                "avg_amount": avg_amount,
                "transaction_types": type_breakdown,
            }
        except Exception as exc:
            self._safe_rollback()
            logger.error(f"Error fetching table stats: {exc}")
            return {"mode": "live", "error": str(exc)}

    def close(self):
        if self._conn:
            self._conn.close()


# =============================================================================
# Drift Detection
# =============================================================================

def detect_drift(
    table_name: str,
    baseline: list[dict],
    actual: list[dict],
    critical_columns: set[str] = None,
) -> list[dict]:
    """
    Compare baseline vs actual schema. Returns list of drift events.
    critical_columns is the effective critical set for THIS table (from context).
    """
    if critical_columns is None:
        critical_columns = CRITICAL_COLUMNS
    baseline_map = {f["column"].lower(): f for f in baseline}
    actual_map   = {f["column"].lower(): f for f in actual}
    now = datetime.now(timezone.utc).isoformat()
    drifts = []

    for col, base in baseline_map.items():
        if col.startswith("_"):
            continue
        if col not in actual_map:
            drifts.append({
                "table": table_name, "column": col,
                "baseline_type": base["type"], "actual_type": "MISSING",
                "detected_at": now, "drift_type": "COLUMN_MISSING",
                "severity": "CRITICAL" if col in critical_columns else "MEDIUM",
            })
        elif actual_map[col]["type"].lower() != base["type"].lower():
            drifts.append({
                "table": table_name, "column": col,
                "baseline_type": base["type"], "actual_type": actual_map[col]["type"],
                "detected_at": now, "drift_type": "TYPE_CHANGE",
                "severity": "CRITICAL" if col in critical_columns else "MEDIUM",
            })

    for col in actual_map:
        if col not in baseline_map and not col.startswith("_"):
            drifts.append({
                "table": table_name, "column": col,
                "baseline_type": "N/A (new column)", "actual_type": actual_map[col]["type"],
                "detected_at": now, "drift_type": "COLUMN_ADDED",
                "severity": "LOW",
            })

    return drifts


def scan_all_drifts(inspector: PostgresSchemaInspector) -> dict:
    """
    Scan all monitored tables and return a full drift report.
    Baseline source (DataHub schemaMetadata > schema_baseline table > JSON) and
    critical column set are both context-aware.
    """
    tables_to_scan = inspector.monitored_tables()
    actual_all = inspector.get_all_monitored_schemas()
    report = {
        "mode": inspector.mode(),
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "tables": {},
        "total_drifts": 0,
        "critical_drifts": 0,
        "any_drift": False,
    }

    for table in tables_to_scan:
        baseline = inspector.get_baseline_from_db(table)
        actual   = actual_all.get(table, [])
        crit_cols = inspector.critical_columns_for(table)

        if not actual:
            report["tables"][table] = {
                "baseline": baseline, "actual": [],
                "drifts": [], "status": "TABLE_NOT_FOUND",
            }
            continue

        drifts = detect_drift(table, baseline, actual, critical_columns=crit_cols)
        critical = [d for d in drifts if d.get("severity") == "CRITICAL"]

        report["tables"][table] = {
            "baseline": baseline,
            "actual": actual,
            "drifts": drifts,
            "status": "DRIFTED" if drifts else "OK",
        }
        report["total_drifts"]    += len(drifts)
        report["critical_drifts"] += len(critical)

    report["any_drift"] = report["total_drifts"] > 0
    return report
