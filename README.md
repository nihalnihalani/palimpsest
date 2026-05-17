# Palimpsest

> *"writing material … on which the original writing has been effaced … but of which traces remain"*

A self-correcting LLM wiki. A topic firehose streams in; the agent maintains a
Karpathy-style markdown wiki and **rewrites pages plus writes a `:SUPERSEDES`
edge in the Cognee knowledge graph** when contradictions arrive — the graph
itself remembers what was true before. The name comes from medieval
manuscripts that were scraped and rewritten with the earlier text still
faintly visible: every `SUPERSEDES` edge is a palimpsest trace.

Built for the Cognee × Redis AI-Memory Hackathon. Stack: **Cognee 1.1.0 · RedisVL 0.18.2 · Redis Stack · Gemini 3 · Python 3.11**.

## Demo

- 🎥 Recording: *(add link after recording)*
- The 3-minute flow lives in [`demo/run_demo.sh`](demo/run_demo.sh).
- Design + plan: [`docs/plans/`](docs/plans/)

## Run locally

One command does everything:

```bash
./run.sh setup     # venv + Redis bringup + .env scaffold (auto-detects mode)
# edit .env: set GEMINI_API_KEY and REDIS_URL
./run.sh all       # doctor → verify → seed → demo
```

### Pick a Redis runtime

You need **Redis Stack** (Redis + RedisJSON modules). Pick one:

- **Redis Cloud free tier** (no install): sign up at [redis.com](https://redis.com), create a free database with the JSON module enabled, copy the connection URL into `REDIS_URL=` in `.env`. `./run.sh setup` auto-detects cloud URLs and skips local bringup.
- **Homebrew** (no Docker): `brew install redis-stack-server` then `./run.sh setup` starts it for you.
- **Docker**: `docker compose up -d` works if Docker Desktop is running. `./run.sh setup` will use it if available.

### Subcommands

| Cmd | What |
|---|---|
| `setup` | venv + pip install + Redis bringup (auto-detected) + .env scaffold |
| `doctor` | Diagnostic dump |
| `verify` | 9-step live smoke (`scripts/verify_live.sh`) |
| `seed` | `wiki reset && wiki seed && wiki load-baseline` |
| `demo` | Interactive 3-min stage flow |
| `rethink` | Cognee memify graph enrichment |
| `test` | pytest |
| `all` | full chain |

Raw `wiki` CLI subcommands: `ask`, `dash`, `doctor`, `eval`, `graph`, `ingest`, `inject`, `inject-canned`, `lint`, `load-baseline`, `reset`, `rethink`, `seed`.

`wiki ingest` drains queued items and exits. Use `wiki ingest --once` for one
message, or `wiki ingest --watch` for an infinite worker. In local Kuzu mode,
`--watch` keeps the graph database locked while it runs, so stop it before
running `wiki reset`, `wiki seed`, `wiki doctor`, `wiki graph`, or `wiki ask`
from another terminal.

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

## Kuzu lock troubleshooting

Local Cognee uses an embedded Kuzu/Ladybug graph under `.cognee_system/`, which
is effectively single-process. If a command reports a graph lock, run:

```bash
wiki doctor
```

The doctor output includes any PIDs currently holding
`.cognee_system/databases/cognee_graph_kuzu*`. Stop those processes, then rerun
the command. The common accidental holder is `wiki ingest --watch` left running
in another terminal.

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
