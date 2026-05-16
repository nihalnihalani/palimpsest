---
title: Redis Memory
updated: 1747000000
sources:
  - https://news.ycombinator.com/item?id=010
  - https://news.ycombinator.com/item?id=013
  - https://news.ycombinator.com/item?id=003
---

Redis Memory refers to the Redis Agent Memory Server, the short-term context
layer Redis ships for autonomous agents. The headline result is the
**5x lower latency benchmark** versus vector-only stores for short-term
agent context — the number that has driven most of the recent interest in
using Redis as a primary [[agent-memory]] substrate.

The Agent Memory Server treats every agent turn as a stream event and keeps
the working set in RedisJSON with HNSW vector indexes alongside. Because the
hot path never leaves Redis, p99 latency stays under a few milliseconds
even with hundreds of concurrent agents. The 5x figure is measured against
representative vector-only baselines on short-term recall workloads, and
Redis has published the methodology and traces.

In production the Redis layer pairs naturally with [[cognee]]: Cognee's
Redis vector adapter writes the durable graph into the same cluster, so
streams in, knowledge graph out, and the entire pipeline answers in
milliseconds. The 5x benchmark remains the canonical reason teams adopt
Redis for the short-term memory tier.

## Sources
- HN: [Redis as agent memory](https://news.ycombinator.com/item?id=010)
- HN: [Cognee + Redis integration](https://news.ycombinator.com/item?id=013)
- HN: [Cognee 0.5 ships](https://news.ycombinator.com/item?id=003)
