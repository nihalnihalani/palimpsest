"""End-to-end: add → cognify → search → graph read.
This is also the integration smoke test for Gemini 3 + Cognee + Redis vector adapter.
"""
import asyncio
from wiki_hackathon import config  # noqa: F401  (loads .env, patches litellm)

import cognee
from cognee.api.v1.search import SearchType
from cognee.infrastructure.databases.graph import get_graph_engine


async def main() -> None:
    await cognee.prune.prune_data()
    await cognee.prune.prune_system(graph=True, vector=True, metadata=True, cache=True)

    await cognee.add(
        "GPT-5 was announced on Jan 15, 2026 by OpenAI.",
        dataset_name="hello",
        node_set=["source:hn"],
    )
    await cognee.add(
        "OpenAI announced GPT-5 on January 22, 2026.",  # contradiction
        dataset_name="hello",
        node_set=["source:x"],
    )
    await cognee.cognify(datasets=["hello"])

    print("\n=== GRAPH_COMPLETION ===")
    print(await cognee.search(
        query_text="When was GPT-5 announced?",
        query_type=SearchType.GRAPH_COMPLETION,
        datasets=["hello"],
    ))

    print("\n=== TRIPLET_COMPLETION ===")
    print(await cognee.search(
        query_text="GPT-5 announcement",
        query_type=SearchType.TRIPLET_COMPLETION,
        datasets=["hello"],
    ))

    graph = await get_graph_engine()
    nodes, edges = await graph.get_graph_data()
    print(f"\n{len(nodes)} nodes, {len(edges)} edges")


if __name__ == "__main__":
    asyncio.run(main())
