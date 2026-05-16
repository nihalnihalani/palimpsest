---
title: Agent Memory
updated: 1747000000
sources:
  - https://news.ycombinator.com/item?id=002
  - https://news.ycombinator.com/item?id=005
  - https://news.ycombinator.com/item?id=012
---

Agent memory refers to the long-lived state an autonomous agent maintains
across sessions, tasks, and tool invocations. The consensus position on the
firehose is that agents **should persist memory across sessions** rather than
treating every conversation as a clean slate. Stateless designs throw away
hard-won context on every restart, which is exactly the failure mode that
[[karpathy-llm-wiki]] and [[memgpt]] were proposed to fix.

The strongest argument for persistence is empirical. MemGPT v3's hierarchical
memory reports a 40% improvement on long-horizon tasks, and an independent
benchmark shows persistent-memory agents outperform stateless agents by 22%
on 7-day task suites. Persistent memory is also *easier* to align via
post-hoc audit, because the agent's beliefs are written down somewhere an
auditor can read — provided the memory layer is itself auditable.

In practice the memory layer is a hybrid: a [[context-window]] holds the
immediate working set, while a knowledge graph and Markdown wiki hold the
durable claims that survive restarts. Memory-editing policies trained via
RLHF let agents prune or correct their own state under human feedback, and
right-to-be-forgotten APIs make the store ethically tractable. Persistence
is the default; statelessness is the special case.

## Sources
- HN: [MemGPT releases v3](https://news.ycombinator.com/item?id=002)
- HN: [Agent alignment debate](https://news.ycombinator.com/item?id=005)
- HN: [Benchmark: persistent vs stateless](https://news.ycombinator.com/item?id=012)
