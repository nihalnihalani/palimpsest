---
title: Karpathy LLM Wiki
updated: 1747000000
sources:
  - https://news.ycombinator.com/item?id=001
  - https://news.ycombinator.com/item?id=009
  - https://news.ycombinator.com/item?id=011
  - https://news.ycombinator.com/item?id=014
---

The Karpathy LLM Wiki is the proposal — popularized in an Andrej Karpathy
gist — that an LLM should maintain a self-updating, Markdown-shaped wiki
rather than re-retrieving raw documents on every query. The wiki accumulates
knowledge across sessions, is lintable for contradictions, and serves as
the durable substrate of [[agent-memory]].

The shape is intentionally low-tech: one Markdown page per concept, plain
`[[wikilinks]]` between pages, a frontmatter block for metadata, and a
plain-text log of every edit. Each ingestion event triggers a
contradiction check; on conflict the page is rewritten and a
`:SUPERSEDES` edge is written into the knowledge graph so the history is
preserved. This is the pattern the "self-correcting wikis" community has
coalesced around.

The wiki is the answer to [[context-window]] rot: instead of stuffing every
relevant document into a giant prompt, the agent maintains a curated,
de-duplicated wiki and reads only the pages it needs. It pairs with
[[memgpt]]-style hierarchical memory for working state, with [[cognee]] as
the graph engine, and with [[redis-memory]] for the hot path. The lint
discipline — detect contradictions, orphan facts, stale claims, and prune —
is what keeps the wiki honest as it grows.

## Sources
- HN: [Karpathy proposes LLM Wiki](https://news.ycombinator.com/item?id=001)
- HN: [On wiki self-correction](https://news.ycombinator.com/item?id=009)
- HN: [Karpathy on context rot](https://news.ycombinator.com/item?id=011)
- HN: [Lint your agent's memory](https://news.ycombinator.com/item?id=014)
