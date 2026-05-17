"""Minimal Cognee Cloud gate: serve -> remember -> recall."""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from . import config  # noqa: F401 - loads .env and repo-local Cognee paths
from .config import ROOT
from . import kuzu_lock

EVIDENCE_DIR = ROOT / "docs" / "evidence"


class CloudSmokeError(RuntimeError):
    pass


def _extract_text(entries: Any) -> str:
    if not entries:
        return ""
    parts: list[str] = []
    for entry in entries if isinstance(entries, list) else [entries]:
        for attr in ("answer", "text", "content"):
            value = getattr(entry, attr, None)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
                break
        else:
            text = str(entry)
            if text.strip():
                parts.append(text.strip())
    return "\n\n".join(parts)


def _local_kuzu_holders() -> list[dict[str, Any]]:
    return kuzu_lock.find_all_holders(kuzu_lock.database_dirs(ROOT))


def _holder_summary(holders: list[dict[str, Any]]) -> str:
    bits = []
    for holder in holders:
        parent = holder.get("parent_command") or holder.get("command") or holder.get("name")
        bits.append(f"PID {holder.get('pid')} parent={parent}")
    return "; ".join(bits)


async def _run_async() -> dict[str, Any]:
    url = (os.environ.get("COGNEE_SERVICE_URL") or "").strip()
    api_key = (os.environ.get("COGNEE_API_KEY") or "").strip()
    if not url:
        raise CloudSmokeError("COGNEE_SERVICE_URL is not set")
    if not api_key:
        raise CloudSmokeError("COGNEE_API_KEY is not set")

    holders = _local_kuzu_holders()
    if holders:
        raise CloudSmokeError(
            "local Kuzu is already open before cloud smoke: "
            f"{_holder_summary(holders)}"
        )

    import cognee
    from cognee.api.v1.search import SearchType

    dataset = f"palimpsest-cloud-smoke-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    token = f"PALIMPSEST_CLOUD_SMOKE_{uuid.uuid4().hex[:10].upper()}"
    text = (
        "Cognee Cloud smoke test memory. "
        f"The required answer token is {token}."
    )

    connected = False
    try:
        await cognee.serve(url=url, api_key=api_key)
        connected = True
        holders = _local_kuzu_holders()
        if holders:
            raise CloudSmokeError(
                "cognee.serve opened local Kuzu in cloud mode: "
                f"{_holder_summary(holders)}"
            )

        await cognee.remember(text, dataset_name=dataset)
        holders = _local_kuzu_holders()
        if holders:
            raise CloudSmokeError(
                "cognee.remember opened local Kuzu in cloud mode: "
                f"{_holder_summary(holders)}"
            )

        entries = await cognee.recall(
            query_text=f"What is the required answer token for {token}?",
            query_type=SearchType.GRAPH_COMPLETION,
            datasets=[dataset],
        )
        holders = _local_kuzu_holders()
        if holders:
            raise CloudSmokeError(
                "cognee.recall opened local Kuzu in cloud mode: "
                f"{_holder_summary(holders)}"
            )

        answer = _extract_text(entries)
        if not answer.strip():
            raise CloudSmokeError("cognee.recall returned an empty response")

        return {
            "mode": "cloud",
            "service_url": url,
            "dataset": dataset,
            "remember_ok": True,
            "recall_ok": True,
            "token_found": token in answer,
            "answer_preview": answer[:500],
            "local_kuzu_holders": [],
            "ts": time.time(),
        }
    finally:
        if connected and hasattr(cognee, "disconnect"):
            await cognee.disconnect(clear_saved=False)


def run(timeout_sec: int = 90) -> dict[str, Any]:
    return asyncio.run(asyncio.wait_for(_run_async(), timeout=timeout_sec))


def write_evidence(payload: dict[str, Any]) -> Path:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE_DIR / "cognee_cloud_smoke.json"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)
    return path
