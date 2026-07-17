# =============================================================================
# db_inspector.py — Real PostgreSQL Schema Inspector
# =============================================================================
# This module connects to PostgreSQL and reads the ACTUAL column types from
# information_schema.columns. This is how the agent detects REAL schema drift
# instead of relying on hardcoded metadata.
#
# Fallback: if PostgreSQL is unreachable, it switches to simulation mode
# where a mutable in-memory state can be drifted interactively (for demo
# without Docker).
# =============================================================================

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("DBInspector")

# Path to the baseline schema JSON file
BASELINE_PATH = Path(__file__).parent / "schema_baseline.json"

# Tables the agent monitors
MONITORED_TABLES = ["paysim_raw_transactions", "feature_engineering_table"]


# =============================================================================
# Simulation State (used when PostgreSQL is not available)
# =============================================================================
# This is a mutable copy of the baseline that can be modified at runtime to
# simulate drift without touching a real database.

def _load_baseline() -> dict:
    """Load the baseline schema from the JSON file."""
    with open(BASELINE_PATH, "r") as f:
        data = json.load(f)
    # Strip meta key
    return {k: v for k, v in data.items() if not k.startswith("_")}


# Global simulation state — starts as a clean copy of baseline
_SIM_STATE: dict = {}


def _get_sim_state() -> dict:
    """Return (and lazily initialize) the simulation state."""
    global _SIM_STATE
    if not _SIM_STATE:
        _SIM_STATE = _load_baseline()
    return _SIM_STATE


def reset_sim_state():
    """Reset simulation state back to baseline (undo all simulated drifts)."""
    global _SIM_STATE
    _SIM_STATE = _load_baseline()


def apply_sim_drift(table: str, column: str, new_type: str):
    """
    Apply a simulated schema drift to the in-memory state.
    Call this to fake an engineer changing a column type.
    """
    state = _get_sim_state()
    if table not in state:
        raise ValueError(f"Table '{table}' not found in simulation state")
    for field in state[table]:
        if field["column"].lower() == column.lower():
            field["_original_type"] = field["type"]
            field["type"] = new_type
            logger.info(f"[SIM] Drifted {table}.{column}: {field['_original_type']} → {new_type}")
            return
    raise ValueError(f"Column '{column}' not found in table '{table}'")


# =============================================================================
# PostgreSQL Inspector
# =============================================================================

class PostgresSchemaInspector:
    """
    Inspects the ACTUAL schema of PostgreSQL tables by querying
    information_schema.columns.

    Falls back to simulation mode if the database is not reachable.
    """

    def __init__(
        self,
        host: str = None,
        port: int = None,
        database: str = None,
        user: str = None,
        password: str = None,
    ):
        self.host = host or os.environ.get("POSTGRES_HOST", "localhost")
        self.port = int(port or os.environ.get("POSTGRES_PORT", "5432"))
        self.database = database or os.environ.get("POSTGRES_DB", "paysim_fintech")
        self.user = user or os.environ.get("POSTGRES_USER", "paysim_user")
        self.password = password or os.environ.get("POSTGRES_PASSWORD", "paysim_secret_2026")

        self._conn = None
        self._mode: str = "unknown"  # "live" or "simulation"
        self._connect()

    def _connect(self):
        """Attempt to connect to PostgreSQL. Switch to simulation on failure."""
        try:
            import psycopg2
            self._conn = psycopg2.connect(
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password,
                connect_timeout=5,
            )
            self._mode = "live"
            logger.info(f"✅ Connected to PostgreSQL at {self.host}:{self.port}/{self.database}")
        except Exception as exc:
            self._conn = None
            self._mode = "simulation"
            logger.warning(
                f"⚠️  Cannot connect to PostgreSQL ({exc}). "
                "Switching to SIMULATION mode."
            )

    def is_live(self) -> bool:
        return self._mode == "live"

    def mode(self) -> str:
        return self._mode

    def get_schema(self, table_name: str, schema_name: str = "public") -> list[dict]:
        """
        Return the actual column schema for a table.

        Each item in the returned list:
            {
                "column":   str,   # lowercase column name
                "type":     str,   # PostgreSQL data type string
                "nullable": bool,
            }

        In simulation mode, reads from the mutable in-memory state.
        """
        if self.is_live():
            return self._live_schema(table_name, schema_name)
        else:
            return self._sim_schema(table_name)

    def _live_schema(self, table_name: str, schema_name: str) -> list[dict]:
        """Query information_schema.columns for the real table schema."""
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
            return [
                {
                    "column": row[0].lower(),
                    "type": row[1].lower(),
                    "nullable": row[2].upper() == "YES",
                }
                for row in rows
            ]
        except Exception as exc:
            logger.error(f"Error querying schema for {table_name}: {exc}")
            # Reconnect attempt
            self._connect()
            return []

    def _sim_schema(self, table_name: str) -> list[dict]:
        """Return schema from the mutable simulation state."""
        state = _get_sim_state()
        table_data = state.get(table_name, [])
        return [
            {
                "column": f["column"].lower(),
                "type": f["type"].lower(),
                "nullable": f.get("nullable", True),
            }
            for f in table_data
        ]

    def apply_drift_to_db(
        self, table_name: str, column_name: str, new_pg_type: str, schema_name: str = "public"
    ) -> bool:
        """
        Execute an ALTER TABLE to change a column type in the real database.
        Returns True on success, False on failure.

        USING clause attempts a safe cast; may fail if data is incompatible.
        """
        if not self.is_live():
            logger.info(f"[SIM] Applying simulated drift: {table_name}.{column_name} → {new_pg_type}")
            apply_sim_drift(table_name, column_name, new_pg_type)
            return True

        sql = f"""
            ALTER TABLE {schema_name}.{table_name}
            ALTER COLUMN {column_name}
            TYPE {new_pg_type}
            USING {column_name}::text::{new_pg_type};
        """
        try:
            cur = self._conn.cursor()
            cur.execute(sql)
            self._conn.commit()
            cur.close()
            logger.info(f"✅ Altered {table_name}.{column_name} → {new_pg_type} in live DB")
            return True
        except Exception as exc:
            self._conn.rollback()
            logger.error(f"❌ Failed to alter {table_name}.{column_name}: {exc}")
            return False

    def restore_column_to_baseline(
        self, table_name: str, column_name: str, schema_name: str = "public"
    ) -> bool:
        """
        Restore a drifted column back to its baseline type.
        Looks up the correct type from schema_baseline.json.
        """
        baseline = _load_baseline()
        table_baseline = baseline.get(table_name, [])
        target_field = next(
            (f for f in table_baseline if f["column"].lower() == column_name.lower()), None
        )
        if not target_field:
            logger.error(f"Column '{column_name}' not in baseline for table '{table_name}'")
            return False

        baseline_type = target_field["type"]

        if not self.is_live():
            reset_sim_state()
            logger.info(f"[SIM] Simulation state fully reset to baseline.")
            return True

        # Convert baseline type to valid PostgreSQL DDL type
        pg_type_map = {
            "integer": "INTEGER",
            "double precision": "DOUBLE PRECISION",
            "character varying": "VARCHAR",
            "text": "TEXT",
            "boolean": "BOOLEAN",
            "bigint": "BIGINT",
        }
        pg_type = pg_type_map.get(baseline_type, baseline_type.upper())

        sql = f"""
            ALTER TABLE {schema_name}.{table_name}
            ALTER COLUMN {column_name}
            TYPE {pg_type}
            USING {column_name}::text::{pg_type.lower().replace(' ', '_')};
        """
        try:
            cur = self._conn.cursor()
            cur.execute(sql)
            self._conn.commit()
            cur.close()
            logger.info(f"✅ Restored {table_name}.{column_name} → {pg_type}")
            return True
        except Exception as exc:
            self._conn.rollback()
            logger.error(f"❌ Failed to restore {table_name}.{column_name}: {exc}")
            return False

    def get_all_monitored_schemas(self) -> dict[str, list[dict]]:
        """Get actual schemas for all monitored tables."""
        result = {}
        for table in MONITORED_TABLES:
            result[table] = self.get_schema(table)
        return result

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
) -> list[dict]:
    """
    Compare baseline schema vs actual schema for a table.

    Returns a list of drift events:
        {
            "table":         str,
            "column":        str,
            "baseline_type": str,
            "actual_type":   str,
            "detected_at":   str (ISO timestamp),
            "drift_type":    "TYPE_CHANGE" | "COLUMN_MISSING" | "COLUMN_ADDED"
        }
    """
    baseline_map = {f["column"].lower(): f for f in baseline}
    actual_map   = {f["column"].lower(): f for f in actual}
    drifts = []
    now = datetime.utcnow().isoformat() + "Z"

    # Check for type changes and missing columns
    for col, base_field in baseline_map.items():
        if col not in actual_map:
            drifts.append({
                "table": table_name,
                "column": col,
                "baseline_type": base_field["type"],
                "actual_type": "MISSING",
                "detected_at": now,
                "drift_type": "COLUMN_MISSING",
            })
        elif actual_map[col]["type"].lower() != base_field["type"].lower():
            drifts.append({
                "table": table_name,
                "column": col,
                "baseline_type": base_field["type"],
                "actual_type": actual_map[col]["type"],
                "detected_at": now,
                "drift_type": "TYPE_CHANGE",
            })

    # Check for added columns (not in baseline)
    for col, act_field in actual_map.items():
        if col not in baseline_map:
            drifts.append({
                "table": table_name,
                "column": col,
                "baseline_type": "N/A (new column)",
                "actual_type": act_field["type"],
                "detected_at": now,
                "drift_type": "COLUMN_ADDED",
            })

    return drifts


def scan_all_drifts(inspector: PostgresSchemaInspector) -> dict:
    """
    Scan all monitored tables and return a full drift report.

    Returns:
        {
            "mode": "live" | "simulation",
            "scanned_at": str,
            "tables": {
                "table_name": {
                    "baseline": [...],
                    "actual":   [...],
                    "drifts":   [...],
                    "status":   "OK" | "DRIFTED"
                }
            },
            "total_drifts": int,
            "any_drift": bool,
        }
    """
    baseline_all = _load_baseline()
    actual_all = inspector.get_all_monitored_schemas()
    report = {
        "mode": inspector.mode(),
        "scanned_at": datetime.utcnow().isoformat() + "Z",
        "tables": {},
        "total_drifts": 0,
        "any_drift": False,
    }

    for table in MONITORED_TABLES:
        baseline = baseline_all.get(table, [])
        actual   = actual_all.get(table, [])

        # If actual is empty (table doesn't exist), skip gracefully
        if not actual:
            report["tables"][table] = {
                "baseline": baseline,
                "actual": [],
                "drifts": [],
                "status": "TABLE_NOT_FOUND",
            }
            continue

        drifts = detect_drift(table, baseline, actual)
        report["tables"][table] = {
            "baseline": baseline,
            "actual": actual,
            "drifts": drifts,
            "status": "DRIFTED" if drifts else "OK",
        }
        report["total_drifts"] += len(drifts)

    report["any_drift"] = report["total_drifts"] > 0
    return report
