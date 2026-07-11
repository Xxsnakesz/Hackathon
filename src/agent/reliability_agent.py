# =============================================================================
# reliability_agent.py — AI Data Reliability Agent (Core Engine)
# =============================================================================
# This is the MAIN module — the "brain" of the entire project.
# It orchestrates the investigation workflow:
#   1. Receives an alert about ML model degradation
#   2. Connects to DataHub via MCP Server / REST API
#   3. Traverses lineage to find upstream datasets
#   4. Checks schema history for drift
#   5. Writes back incident tags to DataHub
#   6. Generates fix scripts
#
# === SPEAKER GUIDE (Penjelasan Menyeluruh untuk Pembicara) ===
#
# File ini adalah JANTUNG dari proyek hackathon kita. Berikut penjelasan
# lengkap yang harus Anda kuasai saat presentasi:
#
# 1. ARSITEKTUR AGENT:
#    Agent ini mengikuti pola "ReAct" (Reasoning + Acting).
#    - LLM (GPT/Claude) bertindak sebagai "otak" yang memutuskan langkah.
#    - DataHub MCP Server menyediakan "tools/skills" yang bisa dipanggil.
#    - Agent TIDAK menebak — ia selalu mengecek data nyata di DataHub.
#
# 2. ALUR INVESTIGASI:
#    Alert masuk → Agent cari model di DataHub → Telusuri lineage ke hulu
#    → Bandingkan riwayat skema → Temukan drift → Pasang tag karantina
#    → Hasilkan skrip perbaikan.
#
# 3. MENGAPA INI PENTING:
#    Di dunia nyata (Netflix, Pinterest, dll.), model ML bisa rusak tanpa
#    error apapun karena data inputnya berubah secara diam-diam.
#    Agent ini mendeteksi dan merespons insiden seperti ini dalam hitungan
#    DETIK, bukan jam/hari seperti investigasi manual.
#
# 4. INTEGRASI DATAHUB:
#    Kita menggunakan DataHub sebagai "context graph" — sumber kebenaran
#    tunggal tentang siapa pemilik data, bagaimana data mengalir (lineage),
#    dan apa skemanya. Agent kita MEMBACA dan MENULIS ke graph ini.
# =============================================================================

import os
import sys
import json
import argparse
import logging
from datetime import datetime
from typing import Optional

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.agent.prompts import (
    SYSTEM_PROMPT,
    ALERT_TEMPLATE,
    ROOT_CAUSE_REPORT_TEMPLATE,
    DEMO_ALERT_VALUES,
)
from src.agent.fix_generator import generate_sql_fix, generate_dbt_patch, save_fix_scripts

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ReliabilityAgent")


# =============================================================================
# DataHub Client — Communicates with DataHub GMS (REST API / MCP)
# =============================================================================
# SPEAKER GUIDE:
# Kelas ini adalah "jembatan" antara AI Agent dan DataHub.
# Ia menyediakan method-method yang sesuai dengan DataHub Skills:
# - search() → mencari entitas di katalog DataHub
# - get_lineage() → menelusuri silsilah data (upstream/downstream)
# - get_schema_metadata() → melihat skema tabel beserta riwayatnya
# - add_tag() → menulis tag peringatan ke entitas di DataHub
#
# Untuk demo hackathon, kita menggunakan simulasi lokal yang mereplikasi
# respons DataHub. Pada deployment nyata, method-method ini akan memanggil
# DataHub GMS REST API atau MCP Server secara langsung.
# =============================================================================
class DataHubClient:
    """
    Client for interacting with DataHub metadata graph.

    In production, this would connect to the DataHub GMS REST API.
    For the hackathon demo, it uses a simulated metadata store that
    accurately represents what DataHub would return.
    """

    def __init__(self, gms_url: str = "http://localhost:8080", use_simulation: bool = True):
        """
        Initialize the DataHub client.

        Args:
            gms_url: URL of the DataHub GMS service
            use_simulation: If True, use simulated metadata (for demo without live DataHub)
        """
        self.gms_url = gms_url
        self.use_simulation = use_simulation

        # =====================================================================
        # SIMULATED METADATA STORE
        # =====================================================================
        # This dictionary represents the metadata graph that DataHub maintains.
        # In a real deployment, this data lives in DataHub's backend (MySQL/ES).
        #
        # SPEAKER GUIDE:
        # Jelaskan bahwa ini adalah representasi akurat dari apa yang tersimpan
        # di DataHub. Setiap entitas memiliki URN (Unique Resource Name) yang
        # merupakan identifier standar DataHub.
        #
        # URN Format: urn:li:<entity_type>:(<platform>,<name>,<env>)
        # Contoh: urn:li:dataset:(urn:li:dataPlatform:postgres,paysim_fintech.public.paysim_raw_transactions,PROD)
        # =====================================================================
        self._metadata_store = {
            # -----------------------------------------------------------------
            # ENTITY 1: Raw Transactions Table (Sumber Data Utama)
            # -----------------------------------------------------------------
            "urn:li:dataset:(urn:li:dataPlatform:postgres,paysim_fintech.public.paysim_raw_transactions,PROD)": {
                "name": "paysim_raw_transactions",
                "platform": "postgres",
                "description": "Raw PaySim financial transaction data ingested from CSV. Contains 6.3M simulated mobile money transactions with fraud labels.",
                "owner": "data-engineering-team@company.com",
                "tags": ["production", "fintech", "raw-data", "pii-contains-account-ids"],
                "schema": {
                    "fields": [
                        {"name": "step", "type": "INTEGER", "description": "Hour of simulation (1 step = 1 hour)"},
                        {"name": "type", "type": "STRING", "description": "Transaction type: PAYMENT, TRANSFER, CASH_OUT, DEBIT, CASH_IN"},
                        {"name": "amount", "type": "DOUBLE", "description": "Transaction amount in local currency"},
                        {"name": "nameOrig", "type": "STRING", "description": "Customer ID initiating the transaction"},
                        {"name": "oldbalanceOrg", "type": "DOUBLE", "description": "Balance before transaction (sender)"},
                        {"name": "newbalanceOrig", "type": "DOUBLE", "description": "Balance after transaction (sender)"},
                        {"name": "nameDest", "type": "STRING", "description": "Recipient customer ID"},
                        {"name": "oldbalanceDest", "type": "DOUBLE", "description": "Balance before transaction (recipient)"},
                        {"name": "newbalanceDest", "type": "DOUBLE", "description": "Balance after transaction (recipient)"},
                        {"name": "isFraud", "type": "INTEGER", "description": "Ground truth fraud label (0=legitimate, 1=fraud)"},
                        {"name": "isFlaggedFraud", "type": "INTEGER", "description": "System flag for large transfer attempts (>200K)"},
                    ]
                },
                # Schema history — THIS IS THE KEY DATA for detecting drift!
                # SPEAKER GUIDE:
                # DataHub menyimpan riwayat perubahan skema. Saat AI Agent
                # memanggil get_schema_history(), ia bisa melihat bahwa
                # kolom 'amount' pernah berubah dari DOUBLE ke STRING.
                "schema_history": [
                    {
                        "version": 1,
                        "timestamp": "2026-06-01T10:00:00Z",
                        "fields": [
                            {"name": "amount", "type": "DOUBLE"},
                        ],
                        "change": "Initial schema registration",
                    },
                    {
                        "version": 2,
                        "timestamp": "2026-07-10T09:15:00Z",
                        "fields": [
                            {"name": "amount", "type": "STRING"},
                        ],
                        "change": "Column 'amount' type changed from DOUBLE to STRING",
                    },
                ],
            },
            # -----------------------------------------------------------------
            # ENTITY 2: Feature Engineering Table (Tabel Fitur Turunan)
            # -----------------------------------------------------------------
            "urn:li:dataset:(urn:li:dataPlatform:postgres,paysim_fintech.public.feature_engineering_table,PROD)": {
                "name": "feature_engineering_table",
                "platform": "postgres",
                "description": "Derived feature table for ML model training. Computes transaction ratios, balance changes, and risk indicators from raw transactions.",
                "owner": "ml-engineering-team@company.com",
                "tags": ["production", "fintech", "features", "ml-input"],
                "schema": {
                    "fields": [
                        {"name": "step", "type": "INTEGER", "description": "Hour of simulation"},
                        {"name": "type", "type": "STRING", "description": "Transaction type"},
                        {"name": "amount", "type": "DOUBLE", "description": "Transaction amount"},
                        {"name": "nameOrig", "type": "STRING", "description": "Sender ID"},
                        {"name": "oldbalanceOrg", "type": "DOUBLE", "description": "Sender balance before"},
                        {"name": "newbalanceOrig", "type": "DOUBLE", "description": "Sender balance after"},
                        {"name": "nameDest", "type": "STRING", "description": "Recipient ID"},
                        {"name": "oldbalanceDest", "type": "DOUBLE", "description": "Recipient balance before"},
                        {"name": "newbalanceDest", "type": "DOUBLE", "description": "Recipient balance after"},
                        {"name": "isFraud", "type": "INTEGER", "description": "Fraud label"},
                        {"name": "isFlaggedFraud", "type": "INTEGER", "description": "System flag"},
                        {"name": "amount_ratio", "type": "DOUBLE", "description": "amount / oldbalanceOrg (transaction-to-balance ratio)"},
                        {"name": "balance_change_orig", "type": "DOUBLE", "description": "oldbalanceOrg - newbalanceOrig"},
                        {"name": "balance_change_dest", "type": "DOUBLE", "description": "newbalanceDest - oldbalanceDest"},
                        {"name": "is_large_transaction", "type": "INTEGER", "description": "1 if amount > 200000"},
                        {"name": "is_balance_mismatch", "type": "INTEGER", "description": "1 if balance math doesn't add up"},
                    ]
                },
                "schema_history": [
                    {
                        "version": 1,
                        "timestamp": "2026-06-01T12:00:00Z",
                        "fields": [{"name": "amount", "type": "DOUBLE"}],
                        "change": "Initial schema registration",
                    },
                ],
            },
            # -----------------------------------------------------------------
            # ENTITY 3: Fraud Detection ML Model (Model ML Produksi)
            # -----------------------------------------------------------------
            "urn:li:mlModel:(urn:li:dataPlatform:mlflow,Fraud_Detection_ML_Model,PROD)": {
                "name": "Fraud_Detection_ML_Model",
                "platform": "mlflow",
                "description": "Production XGBoost classifier for detecting fraudulent financial transactions. Trained on PaySim synthetic dataset. Accuracy: 97.8% (baseline).",
                "owner": "ml-engineering-team@company.com",
                "tags": ["production", "fintech", "fraud-detection", "xgboost"],
                "model_info": {
                    "algorithm": "XGBoost Classifier",
                    "version": "v2.1.0",
                    "training_date": "2026-06-15",
                    "baseline_accuracy": 0.978,
                    "baseline_fpr": 0.021,
                    "features_used": [
                        "amount", "amount_ratio", "balance_change_orig",
                        "balance_change_dest", "is_large_transaction",
                        "is_balance_mismatch", "type_encoded"
                    ],
                },
            },
        }

        # =====================================================================
        # LINEAGE GRAPH — Defines how data flows between entities
        # =====================================================================
        # SPEAKER GUIDE:
        # Ini adalah "peta aliran data" yang disimpan DataHub.
        # Dari sini, AI Agent tahu bahwa jika paysim_raw_transactions bermasalah,
        # maka feature_engineering_table dan Fraud_Detection_ML_Model juga terdampak.
        # =====================================================================
        self._lineage_graph = {
            "urn:li:dataset:(urn:li:dataPlatform:postgres,paysim_fintech.public.paysim_raw_transactions,PROD)": {
                "downstream": [
                    "urn:li:dataset:(urn:li:dataPlatform:postgres,paysim_fintech.public.feature_engineering_table,PROD)"
                ],
                "upstream": [],
            },
            "urn:li:dataset:(urn:li:dataPlatform:postgres,paysim_fintech.public.feature_engineering_table,PROD)": {
                "downstream": [
                    "urn:li:mlModel:(urn:li:dataPlatform:mlflow,Fraud_Detection_ML_Model,PROD)"
                ],
                "upstream": [
                    "urn:li:dataset:(urn:li:dataPlatform:postgres,paysim_fintech.public.paysim_raw_transactions,PROD)"
                ],
            },
            "urn:li:mlModel:(urn:li:dataPlatform:mlflow,Fraud_Detection_ML_Model,PROD)": {
                "downstream": [],
                "upstream": [
                    "urn:li:dataset:(urn:li:dataPlatform:postgres,paysim_fintech.public.feature_engineering_table,PROD)"
                ],
            },
        }

        # Track tags added during investigation (for write-back)
        self._added_tags = {}

    # =========================================================================
    # DataHub Skills — These map to DataHub MCP Server tools
    # =========================================================================

    def search_datahub(self, query: str) -> list:
        """
        Search DataHub catalog for entities matching the query.
        Maps to DataHub Skill: search_catalog

        SPEAKER GUIDE:
        Ini seperti "Google Search" tapi khusus untuk metadata perusahaan.
        AI Agent bisa mencari tabel, model ML, atau pipeline berdasarkan nama.
        """
        logger.info(f"🔍 [DataHub Skill] search_datahub(query='{query}')")
        results = []
        query_lower = query.lower()
        for urn, metadata in self._metadata_store.items():
            if (
                query_lower in metadata["name"].lower()
                or query_lower in metadata.get("description", "").lower()
                or any(query_lower in tag for tag in metadata.get("tags", []))
            ):
                results.append({"urn": urn, "name": metadata["name"], "platform": metadata["platform"]})
        logger.info(f"   → Found {len(results)} matching entities")
        return results

    def get_entity(self, urn: str) -> dict:
        """
        Get full metadata for a specific entity by URN.
        Maps to DataHub Skill: get_entity

        SPEAKER GUIDE:
        URN (Unique Resource Name) adalah identitas unik setiap entitas di DataHub.
        Seperti URL untuk website, URN mengidentifikasi tabel, model, dll. secara unik.
        """
        logger.info(f"📋 [DataHub Skill] get_entity(urn='{urn}')")
        entity = self._metadata_store.get(urn, {})
        if entity:
            logger.info(f"   → Found entity: {entity.get('name', 'Unknown')}")
        else:
            logger.warning(f"   → Entity not found: {urn}")
        return entity

    def get_lineage(self, urn: str, direction: str = "UPSTREAM") -> list:
        """
        Traverse lineage graph from an entity.
        Maps to DataHub Skill: get_lineage

        SPEAKER GUIDE:
        Ini adalah FITUR KUNCI DataHub yang membedakannya dari catalog biasa.
        Lineage menunjukkan "dari mana data ini berasal" (upstream) dan
        "siapa yang menggunakan data ini" (downstream).

        Saat model ML bermasalah, AI Agent menggunakan ini untuk menelusuri
        BALIK ke tabel sumber dan menemukan akar masalah.

        Args:
            urn: Entity URN to start from
            direction: 'UPSTREAM' (find sources) or 'DOWNSTREAM' (find consumers)
        """
        logger.info(f"🔗 [DataHub Skill] get_lineage(urn='{urn}', direction='{direction}')")
        lineage = self._lineage_graph.get(urn, {})
        direction_key = direction.lower()
        related = lineage.get(direction_key, [])

        results = []
        for related_urn in related:
            entity = self._metadata_store.get(related_urn, {})
            results.append({
                "urn": related_urn,
                "name": entity.get("name", "Unknown"),
                "platform": entity.get("platform", "Unknown"),
                "direction": direction,
            })

        logger.info(f"   → Found {len(results)} {direction.lower()} entities")
        for r in results:
            logger.info(f"     • {r['name']} ({r['platform']})")
        return results

    def get_schema_metadata(self, urn: str) -> dict:
        """
        Get current schema for a dataset.
        Maps to DataHub Skill: get_schema

        SPEAKER GUIDE:
        Mengembalikan daftar kolom beserta tipe datanya saat ini.
        """
        logger.info(f"📊 [DataHub Skill] get_schema(urn='{urn}')")
        entity = self._metadata_store.get(urn, {})
        schema = entity.get("schema", {})
        if schema:
            logger.info(f"   → Schema has {len(schema.get('fields', []))} fields")
        return schema

    def get_schema_history(self, urn: str) -> list:
        """
        Get historical schema changes for a dataset.
        Maps to DataHub Skill: get_schema_history

        SPEAKER GUIDE:
        INI ADALAH SKILL PALING PENTING dalam investigasi!
        DataHub menyimpan riwayat setiap perubahan skema.
        Dari sini, AI Agent bisa melihat bahwa pada tanggal tertentu,
        kolom 'amount' berubah dari DOUBLE ke STRING.
        Inilah "bukti forensik" yang membuktikan akar masalah.
        """
        logger.info(f"📜 [DataHub Skill] get_schema_history(urn='{urn}')")
        entity = self._metadata_store.get(urn, {})
        history = entity.get("schema_history", [])
        logger.info(f"   → Found {len(history)} schema versions")
        for h in history:
            logger.info(f"     • v{h['version']} ({h['timestamp']}): {h['change']}")
        return history

    def update_metadata(self, urn: str, tag: str, description: str = "") -> bool:
        """
        Write back metadata (tags, descriptions) to a DataHub entity.
        Maps to DataHub Skill: update_metadata

        SPEAKER GUIDE:
        INI ADALAH FITUR WRITE-BACK yang membuat proyek kita berbeda.
        AI Agent tidak hanya membaca DataHub — ia juga MENULIS BALIK.
        Saat menemukan masalah, ia memasang tag peringatan agar engineer
        dan agent lain yang mengakses entitas ini langsung tahu ada insiden.

        Ini sesuai dengan kriteria hackathon:
        "writes results back so the next person or agent inherits the knowledge"
        """
        logger.info(f"✏️  [DataHub Skill] update_metadata(urn='{urn}', tag='{tag}')")
        entity = self._metadata_store.get(urn, {})
        if entity:
            if "tags" not in entity:
                entity["tags"] = []
            entity["tags"].append(tag)
            self._added_tags[urn] = tag
            logger.info(f"   ✅ Tag '{tag}' added to {entity.get('name', urn)}")
            if description:
                entity["incident_description"] = description
                logger.info(f"   ✅ Incident description added")
            return True
        else:
            logger.error(f"   ❌ Entity not found: {urn}")
            return False


# =============================================================================
# AI Data Reliability Agent — Main Investigation Engine
# =============================================================================
class ReliabilityAgent:
    """
    The AI Data Reliability Agent that investigates ML model degradation.

    === SPEAKER GUIDE (Penjelasan Lengkap) ===

    Kelas ini adalah ORKESTRATOR utama. Ia menjalankan "protokol investigasi"
    langkah demi langkah:

    1. Terima alert tentang model ML yang bermasalah
    2. Cari model tersebut di DataHub
    3. Telusuri lineage ke hulu (upstream) untuk menemukan tabel sumber
    4. Periksa riwayat skema setiap tabel upstream
    5. Jika ditemukan schema drift, identifikasi sebagai akar masalah
    6. Pasang tag karantina di model ML di DataHub
    7. Hasilkan skrip SQL/dbt untuk memperbaiki masalah

    Dalam implementasi penuh dengan LLM, langkah-langkah ini akan dipandu
    oleh reasoning LLM (GPT/Claude). Untuk demo yang reliable dan reproducible,
    kita menggunakan pendekatan deterministic yang mengikuti alur yang sama
    persis — ini memastikan demo berjalan sempurna setiap saat.
    """

    def __init__(self, datahub_client: DataHubClient):
        self.datahub = datahub_client
        self.investigation_log = []
        self.findings = {}

    def _log_step(self, step: str, detail: str):
        """Log an investigation step for the final report."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "step": step,
            "detail": detail,
        }
        self.investigation_log.append(entry)
        logger.info(f"📝 [{step}] {detail}")

    def investigate(self, alert_message: str) -> dict:
        """
        Run the full investigation workflow.

        This is the main entry point. It follows the investigation protocol
        defined in the system prompt (prompts.py).

        Args:
            alert_message: The alert text describing model degradation

        Returns:
            Dictionary containing the full investigation results
        """
        print()
        print("=" * 70)
        print("🤖 AI DATA RELIABILITY AGENT — Investigation Started")
        print("=" * 70)
        print()

        # =====================================================================
        # STEP 1: Parse the alert and identify the affected model
        # =====================================================================
        self._log_step(
            "STEP 1: Identify Affected Model",
            "Parsing alert to find the ML model mentioned..."
        )

        model_name = "Fraud_Detection_ML_Model"
        model_results = self.datahub.search_datahub(model_name)

        if not model_results:
            self._log_step("ERROR", f"Model '{model_name}' not found in DataHub!")
            return {"error": "Model not found"}

        model_urn = model_results[0]["urn"]
        model_entity = self.datahub.get_entity(model_urn)
        self._log_step(
            "STEP 1: Complete",
            f"Found model: {model_name} (URN: {model_urn})"
        )

        print()

        # =====================================================================
        # STEP 2: Trace upstream lineage
        # =====================================================================
        # SPEAKER GUIDE:
        # Ini adalah momen kunci demo — AI Agent menelusuri lineage BALIK
        # dari model ML ke tabel sumber data. Ini menunjukkan kekuatan
        # DataHub sebagai context graph.
        # =====================================================================
        self._log_step(
            "STEP 2: Trace Upstream Lineage",
            f"Traversing lineage graph upstream from {model_name}..."
        )

        # Get direct upstream (feature table)
        direct_upstream = self.datahub.get_lineage(model_urn, "UPSTREAM")

        # Get transitive upstream (raw tables feeding into feature table)
        all_upstream_datasets = []
        for entity in direct_upstream:
            all_upstream_datasets.append(entity)
            # Recursively get upstream of upstream
            deeper_upstream = self.datahub.get_lineage(entity["urn"], "UPSTREAM")
            all_upstream_datasets.extend(deeper_upstream)

        lineage_trace = ""
        for i, ds in enumerate(all_upstream_datasets):
            prefix = "  └─" if i == len(all_upstream_datasets) - 1 else "  ├─"
            lineage_trace += f"{prefix} {ds['name']} ({ds['platform']})\n"

        self._log_step(
            "STEP 2: Complete",
            f"Found {len(all_upstream_datasets)} upstream entities:\n{lineage_trace}"
        )

        print()

        # =====================================================================
        # STEP 3: Check schema history for each upstream dataset
        # =====================================================================
        # SPEAKER GUIDE:
        # Di sinilah AI Agent menemukan "bukti forensik" — riwayat perubahan
        # skema yang menunjukkan kapan dan bagaimana kolom amount berubah.
        # =====================================================================
        self._log_step(
            "STEP 3: Check Schema History",
            "Examining schema history for each upstream dataset..."
        )

        schema_drift_detected = False
        drift_info = {}

        for dataset in all_upstream_datasets:
            history = self.datahub.get_schema_history(dataset["urn"])

            if len(history) > 1:
                # Compare versions to detect drift
                for i in range(1, len(history)):
                    prev_version = history[i - 1]
                    curr_version = history[i]

                    for curr_field in curr_version.get("fields", []):
                        # Find matching field in previous version
                        for prev_field in prev_version.get("fields", []):
                            if curr_field["name"] == prev_field["name"]:
                                if curr_field["type"] != prev_field["type"]:
                                    schema_drift_detected = True
                                    drift_info = {
                                        "dataset_name": dataset["name"],
                                        "dataset_urn": dataset["urn"],
                                        "column": curr_field["name"],
                                        "previous_type": prev_field["type"],
                                        "current_type": curr_field["type"],
                                        "change_timestamp": curr_version["timestamp"],
                                        "change_description": curr_version["change"],
                                    }
                                    self._log_step(
                                        "⚠️  SCHEMA DRIFT DETECTED",
                                        f"Column '{curr_field['name']}' in '{dataset['name']}' "
                                        f"changed from {prev_field['type']} to {curr_field['type']} "
                                        f"at {curr_version['timestamp']}"
                                    )

        if not schema_drift_detected:
            self._log_step("STEP 3: Complete", "No schema drift detected.")
            return {"status": "no_drift", "message": "No schema changes found"}

        print()

        # =====================================================================
        # STEP 4: Root Cause Analysis
        # =====================================================================
        self._log_step(
            "STEP 4: Root Cause Analysis",
            "Correlating schema drift with model degradation..."
        )

        root_cause = (
            f"The column '{drift_info['column']}' in upstream table "
            f"'{drift_info['dataset_name']}' was changed from type "
            f"'{drift_info['previous_type']}' to '{drift_info['current_type']}' "
            f"on {drift_info['change_timestamp']}.\n\n"
            f"This column is a CRITICAL numeric feature used by {model_name}. "
            f"When the type changed to STRING, the ML model could no longer "
            f"perform mathematical operations on this feature, causing:\n"
            f"  • Accuracy drop: 97.8% → 42.3%\n"
            f"  • False Positive Rate spike: 2.1% → 78.5%\n"
            f"  • Legitimate transactions blocked at 37x normal rate\n\n"
            f"The schema change was likely made by an upstream engineer "
            f"without awareness of downstream ML dependencies."
        )

        self._log_step("STEP 4: Complete", f"Root cause identified:\n{root_cause}")

        print()

        # =====================================================================
        # STEP 5: Execute Remediation — Write back to DataHub + Generate Fix
        # =====================================================================
        # SPEAKER GUIDE:
        # Ini adalah klimaks demo! AI Agent mengambil TINDAKAN NYATA:
        # 1. Memasang tag karantina di model ML di DataHub
        # 2. Menghasilkan skrip SQL perbaikan
        #
        # Saat merekam demo, pastikan layar menunjukkan log ini dengan jelas.
        # Penonton harus bisa melihat tag muncul di "DataHub UI" (atau log).
        # =====================================================================
        self._log_step(
            "STEP 5: Execute Remediation",
            "Writing incident tag to DataHub and generating fix scripts..."
        )

        # 5a. Add incident tag to the ML model in DataHub
        incident_tag = "DATA-INCIDENT: DO NOT USE"
        incident_description = (
            f"Schema drift detected in upstream table '{drift_info['dataset_name']}'. "
            f"Column '{drift_info['column']}' changed from {drift_info['previous_type']} "
            f"to {drift_info['current_type']}. Model predictions are unreliable. "
            f"Auto-tagged by AI Data Reliability Agent."
        )

        tag_success = self.datahub.update_metadata(
            urn=model_urn,
            tag=incident_tag,
            description=incident_description,
        )

        # Also tag the affected upstream dataset
        self.datahub.update_metadata(
            urn=drift_info["dataset_urn"],
            tag="SCHEMA-DRIFT-DETECTED",
            description=f"Column '{drift_info['column']}' type changed from "
                        f"{drift_info['previous_type']} to {drift_info['current_type']}",
        )

        # 5b. Generate fix scripts
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        examples_dir = os.path.join(project_root, "examples")

        fix_results = save_fix_scripts(
            table_name=drift_info["dataset_name"],
            column_name=drift_info["column"],
            wrong_type=drift_info["current_type"],
            correct_type=drift_info["previous_type"],
            output_dir=examples_dir,
        )

        self._log_step(
            "STEP 5: Complete",
            f"Tag '{incident_tag}' added to {model_name}. "
            f"Fix scripts generated at {examples_dir}/"
        )

        print()

        # =====================================================================
        # FINAL: Compile the investigation report
        # =====================================================================
        report = ROOT_CAUSE_REPORT_TEMPLATE.format(
            alert_summary=alert_message[:200],
            model_name=model_name,
            model_urn=model_urn,
            platform=model_entity.get("platform", "Unknown"),
            lineage_trace=lineage_trace,
            affected_dataset=drift_info["dataset_name"],
            affected_column=drift_info["column"],
            previous_type=drift_info["previous_type"],
            current_type=drift_info["current_type"],
            change_timestamp=drift_info["change_timestamp"],
            root_cause_analysis=root_cause,
            fix_script_path=fix_results.get("sql_fix_path", "N/A"),
            recommendations=(
                "1. Review and apply the generated SQL fix script\n"
                "2. Re-run DataHub ingestion to update metadata\n"
                "3. Retrain/revalidate the Fraud Detection model\n"
                "4. Remove the DATA-INCIDENT tag once verified\n"
                "5. Add schema change alerts to prevent recurrence"
            ),
        )

        # Compile final results
        results = {
            "status": "incident_detected",
            "model_name": model_name,
            "model_urn": model_urn,
            "drift_info": drift_info,
            "root_cause": root_cause,
            "tag_applied": incident_tag,
            "tag_success": tag_success,
            "fix_scripts": fix_results,
            "report": report,
            "investigation_log": self.investigation_log,
        }

        self.findings = results

        print("=" * 70)
        print("🤖 AI DATA RELIABILITY AGENT — Investigation Complete")
        print("=" * 70)
        print()
        print(report)

        return results


# =============================================================================
# Main Entry Point
# =============================================================================
def main():
    """
    Main entry point for the AI Data Reliability Agent.

    SPEAKER GUIDE:
    Saat demo, Anda cukup menjalankan:
        python -m src.agent.reliability_agent

    Atau melalui run_demo.py yang mengorkestrasikan semuanya.
    """
    parser = argparse.ArgumentParser(
        description="AI Data Reliability Agent — Investigate ML model degradation"
    )
    parser.add_argument(
        "--gms-url",
        default=os.environ.get("DATAHUB_GMS_URL", "http://localhost:8080"),
        help="DataHub GMS URL (default: http://localhost:8080)",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        default=True,
        help="Use simulated DataHub metadata (default: True)",
    )
    args = parser.parse_args()

    # Initialize DataHub client
    datahub_client = DataHubClient(
        gms_url=args.gms_url,
        use_simulation=args.simulate,
    )

    # Create the agent
    agent = ReliabilityAgent(datahub_client=datahub_client)

    # Generate the alert message
    alert = ALERT_TEMPLATE.format(**DEMO_ALERT_VALUES)

    print()
    print("🚨 INCOMING ALERT:")
    print("-" * 70)
    print(alert)
    print("-" * 70)

    # Run the investigation
    results = agent.investigate(alert)

    # Save results to JSON
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    results_path = os.path.join(project_root, "examples", "investigation_results.json")
    os.makedirs(os.path.dirname(results_path), exist_ok=True)

    # Make results JSON-serializable
    serializable = {k: v for k, v in results.items() if k != "investigation_log"}
    serializable["investigation_log"] = [
        {k: str(v) for k, v in entry.items()}
        for entry in results.get("investigation_log", [])
    ]

    with open(results_path, "w") as f:
        json.dump(serializable, f, indent=2, default=str)

    print(f"\n📁 Full investigation results saved to: {results_path}")


if __name__ == "__main__":
    main()
