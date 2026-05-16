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
    resp = await cognee.search(
        query_text=query,
        search_type=SearchType.GRAPH_COMPLETION,
        dataset_names=[DATASET],
    )
    return str(resp)


async def search_insights(query: str) -> list[Any]:
    return await cognee.search(
        query_text=query,
        search_type=SearchType.INSIGHTS,
        dataset_names=[DATASET],
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
    """For the on-stage `wiki graph supersedes` command."""
    graph = await get_graph_engine()
    _, edges = await graph.get_graph_data()
    out: list[dict] = []
    for src, dst, rel, props in edges or []:
        if rel == "SUPERSEDES":
            out.append({"from": src, "to": dst, **(props or {})})
    return out


async def graph_stats() -> dict:
    graph = await get_graph_engine()
    nodes, edges = await graph.get_graph_data()
    return {"nodes": len(nodes), "edges": len(edges)}


async def reset() -> None:
    await cognee.prune.prune_data()
    await cognee.prune.prune_system(metadata=True)


def run(coro):
    """Synchronous entrypoint for Click commands."""
    return asyncio.run(coro)
