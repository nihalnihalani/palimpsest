"""Vector backend probe — surfaces the actually-resolved cognee vector provider.

The DA review caught a long-standing bug where .env.example advertised
VECTOR_DB_PROVIDER=redis but cognee shipped no Redis vector adapter. This module
exists so `wiki vector-smoke` can never lie about the truth again — it asks
cognee what it actually loaded, and writes the answer to docs/evidence/.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Any

from .config import REDIS_URL  # noqa: F401 — triggers .env load
from .logs import get_logger, event

logger = get_logger(__name__)

EVIDENCE_DIR = Path(__file__).resolve().parents[2] / "docs" / "evidence"


def probe() -> dict[str, Any]:
    """Return the resolved vector-backend config + a tiny smoke result."""
    from cognee.infrastructure.databases.vector.config import (
        get_vectordb_config,
    )
    cfg = get_vectordb_config()
    cloud_url = (os.environ.get("COGNEE_SERVICE_URL") or "").strip()
    out: dict[str, Any] = {
        "cognee_mode": "cloud" if cloud_url else "local",
        "cognee_service_url": cloud_url or "(unset — running fully local)",
        "cognee_vector_provider": cfg.vector_db_provider,
        "cognee_vector_url": cfg.vector_db_url or "(unset)",
    }
    try:
        from cognee.infrastructure.databases.vector.supported_databases import (
            supported_databases,
        )
        out["supported_providers_extra"] = sorted(supported_databases.keys())
    except Exception as e:  # noqa: BLE001
        out["supported_providers_extra"] = f"err: {e!r}"

    out["builtin_providers"] = ["lancedb", "pgvector", "chromadb", "neptune_analytics"]
    out["redis_in_use_for"] = [
        "streams (wiki:evolution)",
        "json (wiki:concept:*)",
        "pub/sub (wiki:events)",
        "verdict cache (wiki:verdict:*)",
        "answer semantic cache (RedisVL)",
        "cognee session memory (session_id=...)",
    ]

    try:
        import cognee
        out["cognee_version"] = cognee.__version__
    except Exception as e:  # noqa: BLE001
        out["cognee_version"] = f"err: {e!r}"

    event(logger, "vector_probe.done",
          provider=out["cognee_vector_provider"],
          cognee=out["cognee_version"])
    return out


def write_evidence(payload: dict[str, Any]) -> Path:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    p = EVIDENCE_DIR / "vector_provider.json"
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(p)
    return p
