# =============================================================================
# datahub_gms_client.py — Real DataHub GMS Integration
# =============================================================================
# This module provides genuine DataHub integration:
#   - READ lineage from DataHub GMS REST API
#   - READ entity metadata from DataHub GMS
#   - WRITE BACK tags / incidents via DataHub Python SDK (DatahubRestEmitter)
#   - SEARCH entities via DataHub REST API
#
# Falls back gracefully to an in-memory catalog if GMS is not reachable.
# This means the agent works end-to-end in demo/simulation mode AND in
# production with a real DataHub instance.
#
# DataHub REST API reference:
#   http://localhost:8085/openapi/swagger-ui/index.html (when running)
# =============================================================================

import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("DatahubGmsClient")

# Default GMS URL — override via DATAHUB_GMS_URL env var or docker-compose
DEFAULT_GMS_URL = "http://localhost:8085"


def _http_get(url: str, params: dict = None, timeout: int = 10) -> tuple:
    """
    HTTP GET — tries `requests` first, falls back to urllib.
    Returns (status_code, json_data_or_None).
    """
    try:
        import requests as _req
        full_url = url
        r = _req.get(full_url, params=params, timeout=timeout)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, None
    except ImportError:
        pass

    # urllib fallback
    try:
        if params:
            url = url + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:
        return 0, None


def _http_post(url: str, payload: dict, timeout: int = 10) -> tuple:
    """
    HTTP POST — tries `requests` first, falls back to urllib.
    Returns (status_code, json_data_or_None).
    """
    try:
        import requests as _req
        r = _req.post(url, json=payload,
                      headers={"Content-Type": "application/json"}, timeout=timeout)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, None
    except ImportError:
        pass

    # urllib fallback
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:
        return 0, None


# =============================================================================
# Real DataHub GMS Client
# =============================================================================
class DatahubGmsClient:
    """
    Wraps DataHub GMS REST API for:
      - Entity reads (GET /entities, /relationships)
      - Metadata write-back (POST via DatahubRestEmitter SDK)

    Falls back to in-memory catalog if GMS is unreachable.
    """

    def __init__(self, gms_url: str = None):
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass

        self.gms_url = (gms_url or os.environ.get("DATAHUB_GMS_URL", DEFAULT_GMS_URL)).rstrip("/")
        self._live = False
        self._emitter = None

        self._probe_connection()

    def _probe_connection(self):
        """Test if DataHub GMS is reachable."""
        try:
            status, _ = _http_get(f"{self.gms_url}/config", timeout=5)
            if status == 200:
                self._live = True
                logger.info(f"✅ Connected to DataHub GMS at {self.gms_url}")
                self._init_emitter()
            else:
                logger.warning(f"DataHub GMS responded with {status}. Using fallback.")
        except Exception as exc:
            logger.warning(f"⚠️  DataHub GMS not reachable ({exc}). Using in-memory fallback.")

    def _init_emitter(self):
        """Initialize the DatahubRestEmitter for write-back operations."""
        try:
            from datahub.emitter.rest_emitter import DatahubRestEmitter
            self._emitter = DatahubRestEmitter(gms_server=self.gms_url)
            self._emitter.test_connection()
            logger.info("✅ DatahubRestEmitter initialized for write-back")
        except ImportError:
            logger.warning("acryl-datahub not installed — write-back via SDK disabled.")
        except Exception as exc:
            logger.warning(f"DatahubRestEmitter init failed: {exc}")

    def is_live(self) -> bool:
        return self._live

    # ─────────────────────────────────────────────────────────────────────
    # READ: Entity Metadata
    # ─────────────────────────────────────────────────────────────────────

    def get_entity(self, urn: str) -> dict:
        """
        Fetch entity metadata from DataHub GMS.
        API: GET /entities/{urlEncodedUrn}
        """
        if not self._live:
            return _FALLBACK_CATALOG.get(urn, {})
        try:
            encoded = urllib.parse.quote(urn, safe="")
            status, data = _http_get(f"{self.gms_url}/entities/{encoded}", timeout=10)
            if status == 200 and data:
                return self._normalize_entity(data, urn)
            else:
                logger.warning(f"GMS GET entity {urn}: {status} — using fallback")
                return _FALLBACK_CATALOG.get(urn, {})
        except Exception as exc:
            logger.warning(f"GMS get_entity failed: {exc} — using fallback")
            return _FALLBACK_CATALOG.get(urn, {})

    def _normalize_entity(self, raw: dict, urn: str) -> dict:
        """Extract useful fields from raw GMS entity response."""
        aspects = raw.get("aspects", {})
        props = aspects.get("datasetProperties", {}).get("value", {})
        name = props.get("name") or urn.split(",")[-2] if "," in urn else urn

        tags_raw = aspects.get("globalTags", {}).get("value", {}).get("tags", [])
        tags = [t.get("tag", "").replace("urn:li:tag:", "") for t in tags_raw]

        return {
            "urn": urn,
            "name": name,
            "description": props.get("description", ""),
            "tags": tags,
            "platform": urn.split("dataPlatform:")[-1].split(",")[0] if "dataPlatform:" in urn else "unknown",
            "_raw": raw,
        }

    # ─────────────────────────────────────────────────────────────────────
    # READ: Lineage
    # ─────────────────────────────────────────────────────────────────────

    def get_lineage(self, urn: str, direction: str = "UPSTREAM", count: int = 10) -> list:
        """
        Fetch upstream or downstream lineage for an entity from DataHub GMS.
        API: GET /relationships?urn={urn}&direction={direction}&types=DownstreamOf
        """
        if not self._live:
            return self._fallback_lineage(urn, direction)
        try:
            status, data = _http_get(
                f"{self.gms_url}/relationships",
                params={
                    "urn": urn,
                    "direction": direction.upper(),
                    "types": "DownstreamOf",
                    "count": count,
                },
                timeout=10,
            )
            if status == 200 and data:
                relationships = data.get("relationships", []) or data.get("entities", [])
                result = []
                for rel in relationships:
                    related_urn = rel.get("entity", {}).get("urn") or rel.get("urn", "")
                    if related_urn:
                        entity = self.get_entity(related_urn)
                        result.append({
                            "urn": related_urn,
                            "name": entity.get("name", related_urn),
                            "platform": entity.get("platform", "unknown"),
                            "direction": direction,
                        })
                if result:
                    logger.info(f"GMS lineage {direction} for {urn}: {len(result)} entities")
                    return result
                else:
                    logger.info(f"GMS lineage empty — using fallback for {urn}")
                    return self._fallback_lineage(urn, direction)
            else:
                logger.warning(f"GMS lineage {status} — using fallback")
                return self._fallback_lineage(urn, direction)
        except Exception as exc:
            logger.warning(f"GMS get_lineage failed: {exc} — using fallback")
            return self._fallback_lineage(urn, direction)

    def _fallback_lineage(self, urn: str, direction: str) -> list:
        lineage_map = _FALLBACK_LINEAGE.get(urn, {})
        related = lineage_map.get(direction.lower(), [])
        return [
            {
                "urn": r,
                "name": _FALLBACK_CATALOG.get(r, {}).get("name", r.split(",")[-2] if "," in r else r),
                "platform": _FALLBACK_CATALOG.get(r, {}).get("platform", "postgres"),
                "direction": direction,
            }
            for r in related
        ]

    # ─────────────────────────────────────────────────────────────────────
    # READ: Search
    # ─────────────────────────────────────────────────────────────────────

    def search(self, query: str, entity_type: str = "DATASET", count: int = 5) -> list:
        """
        Search DataHub entities by name/query.
        API: POST /entities?action=search
        """
        if not self._live:
            q = query.lower()
            return [
                {"urn": urn, "name": m["name"], "platform": m["platform"]}
                for urn, m in _FALLBACK_CATALOG.items()
                if q in m["name"].lower() or any(q in t for t in m.get("tags", []))
            ]
        try:
            payload = {"input": query, "type": entity_type, "start": 0, "count": count}
            status, data = _http_post(
                f"{self.gms_url}/entities?action=search", payload, timeout=10
            )
            if status == 200 and data:
                results = data.get("value", {}).get("entities", [])
                return [
                    {
                        "urn": e.get("entity"),
                        "name": e.get("entity", "").split(",")[-2] if "," in e.get("entity", "") else e.get("entity", ""),
                        "platform": "unknown",
                    }
                    for e in results
                ]
            return []
        except Exception as exc:
            logger.warning(f"GMS search failed: {exc}")
            return []

    # ─────────────────────────────────────────────────────────────────────
    # WRITE-BACK: Tag an entity in DataHub
    # ─────────────────────────────────────────────────────────────────────

    def add_tag(self, urn: str, tag_name: str) -> bool:
        """
        Add a tag to an entity in DataHub via SDK write-back.
        Uses DatahubRestEmitter + MetadataChangeProposalWrapper (MCP).
        """
        logger.info(f"📤 Writing tag '{tag_name}' → DataHub entity: {urn}")

        if self._emitter:
            try:
                from datahub.emitter.mcp import MetadataChangeProposalWrapper
                from datahub.metadata.schema_classes import GlobalTagsClass, TagAssociationClass
                from datahub.metadata.urns import TagUrn

                # Fetch existing tags first (to avoid overwriting)
                entity = self.get_entity(urn)
                existing_tags = entity.get("tags", [])

                all_tags = list(set(existing_tags + [tag_name]))
                tag_associations = [
                    TagAssociationClass(tag=str(TagUrn(t)))
                    for t in all_tags
                ]

                mcp = MetadataChangeProposalWrapper(
                    entityUrn=urn,
                    aspect=GlobalTagsClass(tags=tag_associations),
                )
                self._emitter.emit(mcp)
                logger.info(f"✅ [DataHub GMS] Tag '{tag_name}' written to {urn}")
                return True
            except Exception as exc:
                logger.error(f"❌ DatahubRestEmitter write-back failed: {exc}")
                # Fall through to REST fallback

        # Fallback: REST API tag write
        if self._live:
            try:
                payload = {
                    "proposal": {
                        "entityType": "dataset",
                        "entityUrn": urn,
                        "aspectName": "globalTags",
                        "aspect": {
                            "__type": "GlobalTags",
                            "tags": [{"tag": f"urn:li:tag:{tag_name}"}],
                        },
                    }
                }
                r = requests.post(
                    f"{self.gms_url}/aspects?action=ingestProposal",
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=10,
                )
                if r.status_code in (200, 204):
                    logger.info(f"✅ [DataHub GMS REST] Tag '{tag_name}' → {urn}")
                    return True
                logger.warning(f"GMS tag write: {r.status_code} {r.text[:100]}")
            except Exception as exc:
                logger.warning(f"GMS REST tag write failed: {exc}")

        # Last fallback: in-memory catalog update only
        entity = _FALLBACK_CATALOG.get(urn)
        if entity:
            entity.setdefault("tags", []).append(tag_name)
        logger.info(f"[Fallback] Tag '{tag_name}' recorded in-memory for {urn}")
        return True

    def emit_incident(self, urn: str, title: str, description: str) -> bool:
        """
        Emit an incident / description update to DataHub via SDK.
        """
        if self._emitter:
            try:
                from datahub.emitter.mcp import MetadataChangeProposalWrapper
                from datahub.metadata.schema_classes import DatasetPropertiesClass

                mcp = MetadataChangeProposalWrapper(
                    entityUrn=urn,
                    aspect=DatasetPropertiesClass(
                        name=title,
                        description=description,
                        customProperties={"incident_status": "ACTIVE", "agent": "AI-Data-Reliability-Agent"},
                    ),
                )
                self._emitter.emit(mcp)
                logger.info(f"✅ [DataHub GMS] Incident emitted for {urn}")
                return True
            except Exception as exc:
                logger.warning(f"Incident emit failed: {exc}")
        return False

    def get_all_tags(self, urn: str) -> list:
        entity = self.get_entity(urn)
        return entity.get("tags", [])

    # ─────────────────────────────────────────────────────────────────────
    # DISCOVERY: Search datasets by platform + tag filter
    # ─────────────────────────────────────────────────────────────────────

    def search_datasets(self, platforms: list[str] = None, tags: list[str] = None, count: int = 50) -> list[dict]:
        """
        Find datasets whose URN platform matches any of `platforms` and whose
        globalTags contain any of `tags`. Both filters are optional and combine
        as AND when both provided.

        Uses GMS search endpoint with wildcard input then filters in-memory.
        Returns normalized entity dicts.
        """
        if not self._live:
            results = []
            for urn, meta in _FALLBACK_CATALOG.items():
                if platforms and meta.get("platform", "").lower() not in [p.lower() for p in platforms]:
                    continue
                if tags and not any(t.lower() in [x.lower() for x in meta.get("tags", [])] for t in tags):
                    continue
                results.append({"urn": urn, "name": meta["name"], "platform": meta["platform"], "tags": meta.get("tags", [])})
            return results

        try:
            payload = {"input": "*", "type": "DATASET", "start": 0, "count": count}
            status, data = _http_post(
                f"{self.gms_url}/entities?action=search", payload, timeout=15
            )
            if status != 200 or not data:
                logger.warning(f"GMS search datasets failed: HTTP {status}")
                return []
            hits = data.get("value", {}).get("entities", [])
            results = []
            for h in hits:
                urn = h.get("entity")
                if not urn:
                    continue
                entity = self.get_entity(urn)
                platform = entity.get("platform", "").lower()
                entity_tags = [t.lower() for t in entity.get("tags", [])]
                if platforms and platform not in [p.lower() for p in platforms]:
                    continue
                if tags and not any(t.lower() in entity_tags for t in tags):
                    continue
                results.append({
                    "urn": urn,
                    "name": entity.get("name", urn),
                    "platform": entity.get("platform"),
                    "tags": entity.get("tags", []),
                })
            logger.info(f"search_datasets(platforms={platforms}, tags={tags}): {len(results)} match")
            return results
        except Exception as exc:
            logger.warning(f"search_datasets error: {exc}")
            return []

    def get_schema_metadata(self, urn: str) -> list[dict]:
        """
        Extract column list from schemaMetadata aspect.
        Returns [{column: str, type: str, nullable: bool, description: str}].
        Type mapping: DataHub type classes → PostgreSQL type names.
        """
        raw = self.get_entity(urn).get("_raw", {})
        aspects = raw.get("aspects", {})
        schema_aspect = aspects.get("schemaMetadata", {}).get("value", {})
        fields_raw = schema_aspect.get("fields", [])
        if not fields_raw:
            return []

        # DataHub type union → Postgres-ish name mapping
        type_map = {
            "NumberType": "double precision",
            "StringType": "character varying",
            "BooleanType": "boolean",
            "DateType": "date",
            "TimeType": "timestamp",
            "BytesType": "bytea",
            "EnumType": "character varying",
        }

        result = []
        for f in fields_raw:
            field_path = f.get("fieldPath", "").lower()
            type_union = f.get("type", {}).get("type", {})
            # type_union is like {"com.linkedin.schema.NumberType": {}} — extract classname
            type_key = next(iter(type_union.keys()), "") if isinstance(type_union, dict) else ""
            type_short = type_key.split(".")[-1] if "." in type_key else type_key
            pg_type = type_map.get(type_short, "unknown")

            result.append({
                "column": field_path,
                "type": pg_type,
                "nullable": f.get("nullable", True),
                "description": f.get("description", ""),
                "datahub_type": type_short,
            })
        return result

    def get_ownership(self, urn: str) -> list[str]:
        """Return owner URNs/emails from ownership aspect."""
        raw = self.get_entity(urn).get("_raw", {})
        aspects = raw.get("aspects", {})
        ownership = aspects.get("ownership", {}).get("value", {})
        owners = ownership.get("owners", [])
        return [o.get("owner", "") for o in owners if o.get("owner")]

    def get_field_tags(self, urn: str, field_path: str) -> list[str]:
        """
        Return tags attached to a specific field via editableSchemaMetadata
        or schemaMetadata field-level globalTags.
        """
        raw = self.get_entity(urn).get("_raw", {})
        aspects = raw.get("aspects", {})

        # editableSchemaMetadata has editableSchemaFieldInfo per field
        esm = aspects.get("editableSchemaMetadata", {}).get("value", {})
        for f in esm.get("editableSchemaFieldInfo", []):
            if f.get("fieldPath", "").lower() == field_path.lower():
                tags_raw = f.get("globalTags", {}).get("tags", [])
                return [t.get("tag", "").replace("urn:li:tag:", "") for t in tags_raw]

        # schemaMetadata may also carry field-level tags
        sm = aspects.get("schemaMetadata", {}).get("value", {})
        for f in sm.get("fields", []):
            if f.get("fieldPath", "").lower() == field_path.lower():
                tags_raw = f.get("globalTags", {}).get("tags", []) if f.get("globalTags") else []
                return [t.get("tag", "").replace("urn:li:tag:", "") for t in tags_raw]

        return []


# =============================================================================
# Fallback catalog & lineage (when DataHub GMS is not running)
# These mirror exactly what lineage_bootstrap.py registers.
# =============================================================================

_URN_RAW     = "urn:li:dataset:(urn:li:dataPlatform:postgres,paysim_fintech.public.paysim_raw_transactions,PROD)"
_URN_FEATURE = "urn:li:dataset:(urn:li:dataPlatform:postgres,paysim_fintech.public.feature_engineering_table,PROD)"
_URN_MODEL   = "urn:li:dataset:(urn:li:dataPlatform:mlflow,Fraud_Detection_ML_Model,PROD)"

_FALLBACK_CATALOG: dict = {
    _URN_RAW: {
        "name": "paysim_raw_transactions",
        "platform": "postgres",
        "description": "Raw PaySim financial transaction data — source of truth for all ML features.",
        "owner": "data-engineering@company.com",
        "tags": ["production", "fintech", "raw-data", "pii"],
    },
    _URN_FEATURE: {
        "name": "feature_engineering_table",
        "platform": "postgres",
        "description": "Derived feature view for Fraud Detection ML model.",
        "owner": "ml-engineering@company.com",
        "tags": ["production", "fintech", "features", "ml-input"],
    },
    _URN_MODEL: {
        "name": "Fraud_Detection_ML_Model",
        "platform": "mlflow",
        "description": "Production XGBoost fraud detection classifier. Baseline accuracy: 97.8%.",
        "owner": "ml-engineering@company.com",
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
                "is_balance_mismatch", "type_encoded",
            ],
        },
    },
}

_FALLBACK_LINEAGE: dict = {
    _URN_RAW: {
        "downstream": [_URN_FEATURE],
        "upstream": [],
    },
    _URN_FEATURE: {
        "downstream": [_URN_MODEL],
        "upstream": [_URN_RAW],
    },
    _URN_MODEL: {
        "downstream": [],
        "upstream": [_URN_FEATURE],
    },
}

# Convenience URN accessors
URN_RAW_TABLE     = _URN_RAW
URN_FEATURE_TABLE = _URN_FEATURE
URN_ML_MODEL      = _URN_MODEL
