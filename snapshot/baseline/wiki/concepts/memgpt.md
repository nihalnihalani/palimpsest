---
title: MemGPT
updated: 1747000000
sources:
  - https://news.ycombinator.com/item?id=002
  - https://news.ycombinator.com/item?id=007
  - https://news.ycombinator.com/item?id=012
---

MemGPT is a hierarchical memory architecture for LLM agents. The v3 release
introduces a three-tier memory hierarchy — a small in-context working set,
a larger recall buffer, and an unbounded archival store — and the surrounding
machinery to page information between tiers as the agent needs it. The team
reports a 40% improvement on long-horizon tasks, the headline result that
made MemGPT a reference point for [[agent-memory]] designs.

The core insight is that LLMs can be taught to manage their own memory if
you give them the right tools: explicit read/write calls into the recall
and archival tiers, and a self-edit loop that decides what is worth keeping.
This is conceptually adjacent to the [[karpathy-llm-wiki]] pattern, except
that MemGPT manages an internal store while the Karpathy wiki externalizes
the durable layer as Markdown pages.

MemGPT v3 ships memory primitives compatible with both vector stores and
graph databases, which is why it shows up alongside Letta's runtime and
inside [[cognee]] pipelines. In a wiki-first architecture, MemGPT typically
runs the hot working-memory tier on top of [[redis-memory]], while the
wiki itself holds the durable, contradiction-resolved view.

## Sources
- HN: [MemGPT releases v3](https://news.ycombinator.com/item?id=002)
- HN: [Letta agents go open source](https://news.ycombinator.com/item?id=007)
- HN: [Benchmark: persistent vs stateless](https://news.ycombinator.com/item?id=012)
