# wiki-hackathon

A self-correcting LLM wiki. A topic firehose streams in; the agent maintains a
Karpathy-style markdown wiki and **rewrites pages plus writes a `:SUPERSEDES`
edge in the Cognee knowledge graph** when contradictions arrive.

Built for the AI-Memory Hackathon. Stack: **Cognee 0.5.8 · Redis Stack · Gemini 3 · Python 3.11**.

## Demo

- 🎥 Recording: *(add link after recording)*
- The 3-minute flow lives in [`demo/run_demo.sh`](demo/run_demo.sh).
- Design + plan: [`docs/plans/`](docs/plans/)

## Run locally

```bash
cp .env.example .env                       # then add your GEMINI_API_KEY
docker compose up -d                       # Redis Stack on 6379 + RedisInsight 8001
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e .

wiki seed                                  # ingest 15 synthetic items (~1-2 min)
wiki load-baseline                         # also load hand-authored baseline pages
wiki inject-canned contradiction_1         # inject a planted contradiction
wiki ingest --once                         # page rewrites + SUPERSEDES edge fires
wiki graph supersedes                      # see the new edge
wiki ask "what's the current view on agent memory?"
wiki eval                                  # before/after score (0/3 -> 3/3)
wiki lint                                  # metrics report
wiki dash                                  # live terminal dashboard
```

## Architecture

```
synthetic items ──XADD──► Redis Stream ──► ingest worker
                                            │ cognee.add + cognify
                                            ▼
                                       Cognee KG (Kuzu + LanceDB)
                                            │
                                            ▼  query.py contradiction check
                                       SUPERSEDES edge + page rewrite
                                            │
                                            ▼
                              wiki/concepts/*.md  (Obsidian-friendly)
                              wiki/log.md         (audit trail)
                              wiki:concept:*      (RedisJSON versioned cache)
                              wiki:evolution      (Redis Stream audit)
                              wiki:events         (Redis Pub/Sub)
```

Full design + 3-min demo script: [`docs/plans/2026-05-16-wiki-hackathon-design.md`](docs/plans/2026-05-16-wiki-hackathon-design.md).

## Self-improvement, made concrete

1. `cognee.search(GRAPH_COMPLETION)` retrieves the KG answer.
2. Gemini contradiction-check (Redis-cached, 10-min TTL) compares new evidence
   against the existing concept page.
3. On conflict: **the page is rewritten AND `graph.add_edge(rel="SUPERSEDES", …)`
   is called on the Cognee graph itself.** The KG remembers what was true before.
4. The held-out `wiki eval` query demonstrates the score moving from 0/3 to 3/3
   after ingest, with citations.

## Credits

- Memory engine: [Cognee](https://docs.cognee.ai)
- Streams + state + pub/sub: [Redis Stack](https://redis.io)
- LLM: Gemini 3 (Google AI Studio)
- Inspiration: [Karpathy's LLM Wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) and [OpenKB](https://openkb.ai)
