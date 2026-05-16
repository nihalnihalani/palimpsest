---
title: Cognee
updated: 1747000000
sources:
  - https://news.ycombinator.com/item?id=003
  - https://news.ycombinator.com/item?id=013
  - https://news.ycombinator.com/item?id=015
---

Cognee 0.5+ is the memory engine that turns a stream of documents into a
queryable knowledge graph. The 0.5 release adds three things that matter
for an agent stack: a **Redis vector adapter**, first-class ontology
support, and the `memify` operation for incremental graph enrichment. The
team positions Cognee as the memory layer for agent frameworks, which is
exactly how it is used in a [[karpathy-llm-wiki]] pipeline.

The pipeline is short: `cognee.add` ingests raw text, `cognee.cognify`
extracts entities and relations into the graph, and `cognee.search` retrieves
either raw text or graph completions. The Redis vector adapter means the
same Redis cluster that fronts [[redis-memory]] for short-term context can
also store Cognee's embeddings, so streams in, knowledge graph out, all
queryable in milliseconds. Cognee + Redis ship a joint reference pipeline
that demonstrates exactly this flow.

For self-correcting wikis, the important Cognee primitives are the typed
edges (`SUPERSEDES`, `CONTRADICTS`, `RELATES_TO`) and the `memify`
operation that re-enriches the graph as new evidence arrives. Cognee also
implements a prune operation that backs a right-to-be-forgotten API, which
is how [[agent-memory]] systems stay ethically tractable as they accumulate
state about real users.

## Sources
- HN: [Cognee 0.5 ships](https://news.ycombinator.com/item?id=003)
- HN: [Cognee + Redis integration](https://news.ycombinator.com/item?id=013)
- HN: [Agent memory ethics](https://news.ycombinator.com/item?id=015)
