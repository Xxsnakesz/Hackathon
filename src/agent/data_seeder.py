# =============================================================================
# data_seeder.py — Load PaySim CSV into PostgreSQL
# =============================================================================
# Seeds the paysim_raw_transactions table with real data from the CSV.
# Idempotent: skips seeding if table already has rows.
# Can be called from Streamlit or CLI.
# =============================================================================

import csv
import io
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("DataSeeder")

PROJECT_ROOT = Path(__file__).parent.parent.parent
CSV_PATH = PROJECT_ROOT / "data" / "sample_transactions.csv"
LARGE_CSV_PATH = PROJECT_ROOT / "PS_20174392719_1491204439457_log.csv"


def seed_transactions(inspector, max_rows: int = 50000, force: bool = False) -> dict:
    """
    Load PaySim transaction data into paysim_raw_transactions.

    Args:
        inspector: PostgresSchemaInspector instance (must be live mode)
        max_rows:  Maximum rows to load (default 50000 from sample CSV)
        force:     If True, truncate and re-seed even if data exists

    Returns:
        dict with 'rows_loaded', 'skipped', 'error'
    """
    if not inspector.is_live():
        return {
            "rows_loaded": 0,
            "skipped": True,
            "message": "Simulation mode — no DB to seed. Using in-memory state.",
        }

    try:
        import psycopg2
        conn = inspector._conn
        cur = conn.cursor()

        # Check existing row count
        cur.execute("SELECT COUNT(*) FROM paysim_raw_transactions")
        existing = cur.fetchone()[0]

        if existing > 0 and not force:
            cur.close()
            return {
                "rows_loaded": 0,
                "skipped": True,
                "existing_rows": existing,
                "message": f"Table already has {existing:,} rows. Use force=True to re-seed.",
            }

        if force and existing > 0:
            cur.execute("TRUNCATE TABLE paysim_raw_transactions")
            conn.commit()
            logger.info(f"Truncated {existing:,} existing rows.")

        # Choose CSV file
        csv_file = CSV_PATH if CSV_PATH.exists() else LARGE_CSV_PATH
        if not csv_file.exists():
            cur.close()
            return {"rows_loaded": 0, "skipped": False, "error": f"CSV not found: {csv_file}"}

        logger.info(f"Seeding from: {csv_file} (max {max_rows:,} rows)")

        # Use COPY via StringIO for efficiency
        loaded = 0
        buffer = io.StringIO()

        with open(csv_file, "r", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if loaded >= max_rows:
                    break
                # Write as TSV row: step,type,amount,nameOrig,oldbalanceOrg,newbalanceOrig,nameDest,oldbalanceDest,newbalanceDest,isFraud,isFlaggedFraud
                buffer.write(
                    "\t".join([
                        row.get("step", "0"),
                        row.get("type", ""),
                        row.get("amount", "0"),
                        row.get("nameOrig", ""),
                        row.get("oldbalanceOrg", "0"),
                        row.get("newbalanceOrig", "0"),
                        row.get("nameDest", ""),
                        row.get("oldbalanceDest", "0"),
                        row.get("newbalanceDest", "0"),
                        row.get("isFraud", "0"),
                        row.get("isFlaggedFraud", "0"),
                    ]) + "\n"
                )
                loaded += 1

        buffer.seek(0)
        cur.copy_from(
            buffer,
            "paysim_raw_transactions",
            sep="\t",
            columns=["step", "type", "amount", "nameorig", "oldbalanceorg",
                     "newbalanceorig", "namedest", "oldbalancedest",
                     "newbalancedest", "isfraud", "isflaggedfraud"],
        )
        conn.commit()

        # Refresh materialized view if it exists
        try:
            cur.execute("REFRESH MATERIALIZED VIEW transaction_health_summary")
            conn.commit()
        except Exception:
            pass

        cur.close()
        logger.info(f"✅ Seeded {loaded:,} rows into paysim_raw_transactions")
        return {
            "rows_loaded": loaded,
            "skipped": False,
            "csv_used": str(csv_file),
            "message": f"Successfully loaded {loaded:,} transactions.",
        }

    except Exception as exc:
        logger.error(f"Seeding failed: {exc}")
        try:
            inspector._conn.rollback()
        except Exception:
            pass
        return {"rows_loaded": 0, "skipped": False, "error": str(exc)}


def check_data_loaded(inspector) -> dict:
    """Check if transaction data is already in the DB."""
    if not inspector.is_live():
        return {"loaded": False, "row_count": 0, "mode": "simulation"}
    try:
        cur = inspector._conn.cursor()
        cur.execute("SELECT COUNT(*) FROM paysim_raw_transactions")
        count = cur.fetchone()[0]
        cur.close()
        return {"loaded": count > 0, "row_count": count, "mode": "live"}
    except Exception as exc:
        return {"loaded": False, "row_count": 0, "mode": "live", "error": str(exc)}


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass

    sys.path.insert(0, str(PROJECT_ROOT))
    from src.agent.db_inspector import PostgresSchemaInspector

    inspector = PostgresSchemaInspector()
    if not inspector.is_live():
        print("❌ Cannot seed: PostgreSQL not connected. Run docker-compose up first.")
        sys.exit(1)

    print("🌱 Seeding paysim_raw_transactions...")
    result = seed_transactions(inspector, max_rows=50000)
    if result.get("error"):
        print(f"❌ Error: {result['error']}")
    elif result.get("skipped"):
        print(f"⏭️  Skipped: {result['message']}")
    else:
        print(f"✅ Loaded {result['rows_loaded']:,} rows from {result['csv_used']}")
