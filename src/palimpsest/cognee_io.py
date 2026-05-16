"""Thin async wrapper over Cognee. All cognee calls live here so swaps stay local."""
from __future__ import annotations
import asyncio
import os
from typing import Any

from . import config  # noqa: F401 — load .env + patch litellm
from .logs import get_logger, event

logger = get_logger(__name__)

# Defensive top-level imports: a broken cognee install (e.g., transitive dep
# breakage like mistralai 2.x) shouldn't crash the entire CLI. We capture the
# import error and fail loudly only when a function that actually needs cognee
# is called.
COGNEE_AVAILABLE = True
_COGNEE_IMPORT_ERROR: Exception | None = None
try:
    import cognee
    from cognee.api.v1.search import SearchType
    try:
        import cognee.modules.pipelines.layers.setup_and_check_environment as _env
        _env._first_run_done = True
    except Exception:
        pass
    from cognee.infrastructure.databases.graph import get_graph_engine
except Exception as _e:  # noqa: BLE001
    COGNEE_AVAILABLE = False
    _COGNEE_IMPORT_ERROR = _e
    cognee = None  # type: ignore[assignment]
    SearchType = None  # type: ignore[assignment]
    get_graph_engine = None  # type: ignore[assignment]
    logger.error(f"cognee import failed at startup: {type(_e).__name__}: {_e}")


def _require_cognee() -> None:
    if not COGNEE_AVAILABLE:
        raise RuntimeError(
            f"cognee is not importable: {type(_COGNEE_IMPORT_ERROR).__name__}: "
            f"{_COGNEE_IMPORT_ERROR}. Run `pip install -e .` to repair the venv."
        )

DATASET = "wiki"


# ---- Cognee Cloud bootstrap ------------------------------------------------
# When COGNEE_SERVICE_URL is set, cognee.serve() redirects all V2 operations
# (remember/recall/improve/forget/visualize) to the managed instance instead
# of running locally. V1 operations (add/cognify/search) may still need the
# local install — cognee's behavior on hybrid mode isn't formally documented.
_cloud_initialized = False


async def _init_cloud_if_configured() -> None:
    """Idempotent: call cognee.serve(url, api_key) on first run if env vars are
    set. Safe to call from every run() — short-circuits after the first call."""
    global _cloud_initialized
    if _cloud_initialized:
        return
    url = (os.environ.get("COGNEE_SERVICE_URL") or "").strip()
    if not url:
        _cloud_initialized = True  # local mode locked in
        return
    if not COGNEE_AVAILABLE:
        return
    key = (os.environ.get("COGNEE_API_KEY") or "").strip() or None
    event(logger, "cognee.cloud.connecting", url=url[:80])
    await cognee.serve(url=url, api_key=key)
    event(logger, "cognee.cloud.connected", url=url[:80])
    _cloud_initialized = True


def is_cloud_mode() -> bool:
    return bool((os.environ.get("COGNEE_SERVICE_URL") or "").strip())


async def add(text: str, source: str) -> None:
    event(logger, "cognee.add.start", chars=len(text), source=source)
    await cognee.add(text, dataset_name=DATASET, node_set=[f"source:{source}"])
    event(logger, "cognee.add.done", source=source)


async def cognify() -> None:
    event(logger, "cognee.cognify.start")
    await cognee.cognify(datasets=[DATASET])
    event(logger, "cognee.cognify.done")


async def search_completion(query: str) -> str:
    # Cognee 0.5.8: search uses query_type= and datasets= (renamed from
    # search_type= / dataset_names= in earlier versions).
    event(logger, "cognee.search_completion.start", chars=len(query))
    resp = await cognee.search(
        query_text=query,
        query_type=SearchType.GRAPH_COMPLETION,
        datasets=[DATASET],
    )
    text = str(resp)
    event(logger, "cognee.search_completion.done", chars=len(text))
    return text


async def search_insights(query: str) -> list[Any]:
    # 0.5.8 does not expose SearchType.INSIGHTS; TRIPLET_COMPLETION returns
    # an LLM answer composed from graph triplets, which is what we want for
    # extracting top concepts.
    return await cognee.search(
        query_text=query,
        query_type=SearchType.TRIPLET_COMPLETION,
        datasets=[DATASET],
    )


async def top_concepts(item_text: str, k: int = 3) -> list[str]:
    """Pull top entity labels for an item. Used to decide which concept pages to touch."""
    insights = await search_insights(item_text)
    seen: dict[str, int] = {}
    for triple in insights or []:
        # Cognee triple format varies by version; defensively extract labels
        if isinstance(triple, dict):
            for key in ("subject", "object", "name", "label"):
                v = triple.get(key)
                if isinstance(v, str):
                    seen[v] = seen.get(v, 0) + 1
        elif isinstance(triple, (list, tuple)) and len(triple) >= 3:
            for v in (triple[0], triple[2]):
                if isinstance(v, str):
                    seen[v] = seen.get(v, 0) + 1
    return sorted(seen, key=seen.get, reverse=True)[:k]


async def write_supersedes_edge(old_claim: str, new_claim: str,
                                source: str, reason: str) -> None:
    """The Cognee differentiator: the graph itself remembers what was true before."""
    graph = await get_graph_engine()
    import time
    old_id = f"claim:{abs(hash(old_claim))}"
    new_id = f"claim:{abs(hash(new_claim))}"
    await graph.add_node(old_id, {"text": old_claim, "kind": "claim"})
    await graph.add_node(new_id, {"text": new_claim, "kind": "claim"})
    await graph.add_edge(
        old_id, new_id,
        relationship_name="SUPERSEDES",
        properties={"source": source, "reason": reason, "ts": time.time()},
    )
    event(logger, "cognee.add_edge.SUPERSEDES",
          old=old_id, new=new_id, reason=reason[:40])


async def list_supersedes() -> list[dict]:
    """For the on-stage `wiki graph supersedes` command.

    Cognee's get_graph_data edge tuple shape varies by version; handle both
    4-tuple (src, dst, rel, props) and 3-tuple (src, dst, props_with_rel).
    """
    graph = await get_graph_engine()
    _, edges = await graph.get_graph_data()
    out: list[dict] = []
    for edge in edges or []:
        if isinstance(edge, (list, tuple)):
            if len(edge) == 4:
                src, dst, rel, props = edge
            elif len(edge) == 3:
                src, dst, props = edge
                rel = (props or {}).get("relationship_name", "")
            else:
                continue
        else:
            continue
        if rel == "SUPERSEDES":
            out.append({"from": src, "to": dst, **(props or {})})
    return out


async def list_entities(limit: int = 50) -> list[tuple[str, dict]]:
    """Return (node_id, properties) for entity-like nodes."""
    graph = await get_graph_engine()
    nodes, _ = await graph.get_graph_data()
    out = []
    for n in nodes or []:
        if isinstance(n, (list, tuple)) and len(n) >= 2:
            nid, props = n[0], n[1]
        elif isinstance(n, dict):
            nid, props = n.get("id"), n
        else:
            continue
        if not nid:
            continue
        out.append((nid, props or {}))
    return out[:limit]


async def list_neighborhood(node_id: str) -> list[tuple[str, str, str]]:
    """Return list of (other_node_id, rel, direction) for one node."""
    graph = await get_graph_engine()
    _, edges = await graph.get_graph_data()
    out = []
    for e in edges or []:
        if isinstance(e, (list, tuple)):
            if len(e) == 4:
                src, dst, rel, _ = e
            elif len(e) == 3:
                src, dst, props = e
                rel = (props or {}).get("relationship_name", "")
            else:
                continue
            if src == node_id:
                out.append((dst, rel, "out"))
            elif dst == node_id:
                out.append((src, rel, "in"))
    return out


async def write_inferred_edge(src: str, dst: str, rel: str,
                              reason: str) -> None:
    """Like write_supersedes_edge but for INFERRED-relationship rethink output."""
    import time
    graph = await get_graph_engine()
    await graph.add_edge(
        src, dst,
        relationship_name=rel or "RELATED_TO",
        properties={"source": "rethink", "reason": reason, "ts": time.time()},
    )
    event(logger, "cognee.add_edge.INFERRED",
          src=src, dst=dst, rel=rel or "RELATED_TO", reason=reason[:40])


async def graph_stats() -> dict:
    graph = await get_graph_engine()
    nodes, edges = await graph.get_graph_data()
    return {"nodes": len(nodes), "edges": len(edges)}


async def reset() -> None:
    # 0.5.8 prune_system signature: (graph=True, vector=True, metadata=False, cache=True)
    await cognee.prune.prune_data()
    await cognee.prune.prune_system(graph=True, vector=True, metadata=True, cache=True)


async def _with_cloud_init(coro):
    """Wrap a coroutine so the cloud bootstrap fires once before it runs."""
    await _init_cloud_if_configured()
    return await coro


def run(coro):
    """Synchronous entrypoint for Click commands."""
    return asyncio.run(_with_cloud_init(coro))
