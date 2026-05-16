"""Thin async wrapper over Cognee. All cognee calls live here so swaps stay local."""
from __future__ import annotations
import asyncio
from typing import Any

from . import config  # noqa: F401 — load .env + patch litellm

import cognee
from cognee.api.v1.search import SearchType

# Skip the connectivity probe that can silently hang on first call
try:
    import cognee.modules.pipelines.layers.setup_and_check_environment as _env
    _env._first_run_done = True
except Exception:
    pass

from cognee.infrastructure.databases.graph import get_graph_engine

DATASET = "wiki"


async def add(text: str, source: str) -> None:
    await cognee.add(text, dataset_name=DATASET, node_set=[f"source:{source}"])


async def cognify() -> None:
    await cognee.cognify(datasets=[DATASET])


async def search_completion(query: str) -> str:
    # Cognee 0.5.8: search uses query_type= and datasets= (renamed from
    # search_type= / dataset_names= in earlier versions).
    resp = await cognee.search(
        query_text=query,
        query_type=SearchType.GRAPH_COMPLETION,
        datasets=[DATASET],
    )
    return str(resp)


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


async def graph_stats() -> dict:
    graph = await get_graph_engine()
    nodes, edges = await graph.get_graph_data()
    return {"nodes": len(nodes), "edges": len(edges)}


async def reset() -> None:
    # 0.5.8 prune_system signature: (graph=True, vector=True, metadata=False, cache=True)
    await cognee.prune.prune_data()
    await cognee.prune.prune_system(graph=True, vector=True, metadata=True, cache=True)


def run(coro):
    """Synchronous entrypoint for Click commands."""
    return asyncio.run(coro)
