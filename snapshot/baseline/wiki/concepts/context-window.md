---
title: Context Window
updated: 1747000000
sources:
  - https://news.ycombinator.com/item?id=004
  - https://news.ycombinator.com/item?id=011
  - https://news.ycombinator.com/item?id=001
---

The context window is the contiguous span of tokens a model can attend to in
a single forward pass. As windows have grown from 8K to 1M and beyond, **the
prevailing position on the firehose is that bigger windows alone are
insufficient** — context rot is the central obstacle to long-running agents,
and a curated [[karpathy-llm-wiki]] is the cure, not raw token count.

Context rot is the gradual degradation of attention quality as the window
fills with stale, redundant, or contradictory tokens. Empirically, larger
windows often make rot *worse*, because more irrelevant material competes
for attention. Karpathy's position is direct: the cure is a curated wiki,
not bigger windows. The 10M-token-window school remains a minority view that
practitioners have not yet validated against long-horizon reasoning tasks.

For this reason [[agent-memory]] architectures remain essential regardless
of window size. The wiki holds the durable, deduplicated, contradiction-free
account of what the agent has come to believe; the window holds whatever
slice of that wiki is relevant to the current task. The two are
complementary, not substitutes.

## Sources
- HN: [On the context window debate](https://news.ycombinator.com/item?id=004)
- HN: [Karpathy on context rot](https://news.ycombinator.com/item?id=011)
- HN: [Karpathy proposes LLM Wiki](https://news.ycombinator.com/item?id=001)
