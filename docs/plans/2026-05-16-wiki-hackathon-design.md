# wiki-hackathon — Design Document

**Event:** AI-Memory Hackathon (Cognee + Redis) — Karpathy's LLM Wiki
**Date:** 2026-05-16
**Window:** 1:00 PM → 4:30 PM build · 5:00 PM finalist demos
**Team:** Solo (1 builder)
**Repo:** `/Users/nihalnihalani/Desktop/Github/wiki-hackathon/`
**Reference fork:** `/Users/nihalnihalani/Desktop/Github/OpenKB-main/` (concepts + ~450 lines lifted; rest fresh)

---

## 1. Pitch (one sentence)

A self-correcting LLM wiki — an agent ingests a topic firehose, builds a Karpathy-style markdown wiki, and when contradictions arrive, it **rewrites the offending wiki page AND writes a `:SUPERSEDES` edge in the Cognee knowledge graph with provenance**. Cognee for memory, Redis for the nervous system, Gemini 3 for the brain.

## 2. What makes us win (the differentiator)

The judges' "rip out Cognee, what breaks?" test has a concrete answer:
- A graph edge labeled `:SUPERSEDES {source: HN#47, reason: "newer evidence", ts: ...}`.
- A 2-hop graph query that markdown+grep cannot answer: *"Which concepts have been superseded by sources we already cite elsewhere?"*
- A held-out eval query that flips from 0/3 → 3/3 with citations after ingest.

Without the graph, none of those three artifacts exist. With them, the win is visible in <10 seconds on stage.

## 3. Stack (locked, no fallback)

| Layer | Choice | Config / Pin |
|---|---|---|
| Memory engine | **Cognee** | `cognee>=0.5.2`; `add → cognify → search → memify` flow; direct graph engine for `add_edge` |
| LLM | **Gemini 3** | `gemini/gemini-3-pro` for both Cognee internals and direct app calls. `LLM_INSTRUCTOR_MODE=json_mode` (critical — Gemini does not support `json_schema_mode` or `tool_call`). |
| Embeddings | **Gemini** | `text-embedding-004`, 768d |
| Vector store | **Redis** (via Cognee's Redis adapter) | `VECTOR_DB_PROVIDER=redis`, `VECTOR_DB_URL=redis://localhost:6379` |
| Graph store | **Kuzu** (Cognee default) | Embedded, zero-config |
| Stream/queue | **Redis Streams** | `firehose:items` + consumer group `ingestors` |
| State cache | **RedisJSON** | `wiki:concept:{slug}` with `$.current`, `$.history[]`, `$.contradictions[]` |
| Live nudges | **Redis Pub/Sub** | Channel `wiki:events` |
| Audit trail | **Redis Stream** | `wiki:evolution` (XADD on every rewrite) |
| Redis runtime | **Redis Stack** | `redis/redis-stack:latest` Docker image |
| Python client | `redis-py >= 5.0` with `decode_responses=True` |
| CLI | **Click** | Single entrypoint: `wiki ...` |
| Dashboard | **`rich.Layout`** | No web UI |
| Wiki render | Plain Markdown + `[[wikilinks]]` viewed in **Obsidian** |

**Cut entirely** (vs OpenKB): PageIndex, markitdown, openai-agents SDK, LiteLLM in our code (Cognee uses it internally), watch mode, chat sessions, multi-language, image extraction, summaries/ subdirectory, Reddit, RSS, hybrid live ingestion, Flask UI, prompt caching machinery.

## 4. Architecture

```
data/canned/seed_items.jsonl  (15 synthetic items on "AI agents 2026")
        │
        ▼
   replay.py  ──XADD──► firehose:items (Redis Stream)
                              │
                              ▼ XREADGROUP (worker, no overlap)
                  ┌───────────────────────────┐
                  │ ingest.py                 │
                  │  SETNX seen:{sha}         │
                  │  cognee.add(...)          │
                  │  cognee.cognify()  [slow] │
                  │  search(INSIGHTS) → top3  │
                  │  for slug:                │
                  │    gemini(CONCEPT_RENDER) │
                  │    write concepts/X.md    │
                  │    JSON.SET wiki:concept  │
                  │    XADD wiki:evolution    │
                  │    PUBLISH wiki:events    │
                  │  XACK                     │
                  └────┬──────────────┬───────┘
                       │              │
                       ▼              ▼
                  Cognee KG        Redis (vectors + JSON + streams + pubsub)
                       │
                       ▼
                  ┌───────────────────────────────┐
                  │ query.py — `wiki ask`         │
                  │  cognee.search(GRAPH_COMPLETE)│
                  │  for relevant concept:        │
                  │    Redis GET verdict:{sha}    │
                  │    if miss: gemini(CONTRADICT)│
                  │    if conflict:               │
                  │      rewrite .md              │
                  │      graph.add_edge(          │
                  │        rel=SUPERSEDES,        │
                  │        props={source,reason}) │
                  │      JSON.SET history++       │
                  │      XADD wiki:evolution      │
                  │      PUBLISH wiki:events      │
                  │  return answer w/ citations   │
                  └───────────────────────────────┘

                  ┌───────────────────────────────┐
                  │ lint.py — `wiki lint`         │
                  │  Structural (forked OpenKB):  │
                  │    orphan pages               │
                  │    broken [[wikilinks]]       │
                  │    index sync                 │
                  │  Knowledge (Cognee Cypher):   │
                  │    nodes w/ SUPERSEDES edges  │
                  │    orphan concept nodes       │
                  │    contradicting INSIGHTS     │
                  │  → reports/lint-{ts}.md       │
                  │  with metrics table           │
                  └───────────────────────────────┘

                  ┌───────────────────────────────┐
                  │ dashboard.py (rich.Layout)    │
                  │  Firehose │ Concepts │ Audit  │
                  │  Metrics: concepts/edges/     │
                  │  supersedes/cache_hit/eval    │
                  └───────────────────────────────┘
```

## 5. The three required operations

### 5.1 Ingest
Replay → Stream → worker. Worker: dedup via Redis `SETNX seen:{sha} TTL=1h` → `cognee.add(text, dataset_name="wiki", node_set=["source:hn"])` → `cognee.cognify(datasets=["wiki"])` → pull top entities via `cognee.search(SearchType.INSIGHTS)` → for top 3 concepts, render page via Gemini → write `wiki/concepts/{slug}.md` → `JSON.SET wiki:concept:{slug}` → `XADD wiki:evolution` → `PUBLISH wiki:events` → `XACK`.

### 5.2 Query + Self-Improve
This is the substantive self-improvement loop. Not theater:
1. `cognee.search(query, SearchType.GRAPH_COMPLETION)` → KG answer.
2. For each concept touched, check contradiction (Gemini call, JSON output, cached in Redis `verdict:{sha}` TTL=10min).
3. On conflict: rewrite `.md` AND call `graph_engine.add_edge(source=old_claim, target=new_claim, relationship_name="SUPERSEDES", properties={source, reason, ts})`. The KG itself remembers what was true before.
4. Optionally trigger `cognee.memify(extraction_tasks=[load_contradictions], enrichment_tasks=[resolve_with_gemini])` for batch enrichment passes.
5. Return cited answer.

### 5.3 Lint
Two layers, both write to one `wiki/reports/lint-{ts}.md`:
- **Structural** (lifted from OpenKB `lint.py`): orphans, broken `[[wikilinks]]`, index sync.
- **Knowledge** (Cognee-powered Cypher + `SearchType.INSIGHTS`): triplet count, nodes with SUPERSEDES edges, orphan concept nodes, contradicting triplets.
- Output always includes a metrics table (concepts/edges/supersedes/orphans).

## 6. The hero moment (~30s on stage, deterministic)

| t | Visible | Under the hood |
|---|---|---|
| 0s | Obsidian shows `concepts/agent-memory.md`. RedisInsight tab open. Dashboard running. | Pre-baked state: 12 seed items already ingested via `wiki seed` before showtime. |
| 5s | Run `wiki inject --canned 1` | Pre-vetted contradiction → Redis Stream. |
| 8s | Firehose pane lights up. `XLEN firehose:items` ticks in RedisInsight. | Worker claims item. |
| 18s | Concept row in dashboard highlights. **Obsidian re-renders the page live** (file change on disk). | `cognee.add` + `cognify` complete (~10s, narrated over). Query path detects conflict. Page rewritten. **`graph_engine.add_edge(rel="SUPERSEDES")` fires.** |
| 25s | Run `wiki graph supersedes` → prints the new edge with source citation. | Cypher: `MATCH ()-[r:SUPERSEDES]->() RETURN r.source, r.reason, r.ts` |
| 30s | Switch to Obsidian `log.md`: new line `[14:23] SELF-CORRECT agent-memory — superseded by HN#47`. Audit pane row matches. | Append + XADD. |

## 7. The killer 2-hop query (Cognee's load-bearing moment)

```
wiki ask "Which concepts have been superseded by sources we already cite elsewhere?"
```

Requires graph traversal: SUPERSEDES edges → source nodes → other concepts citing those sources. Markdown+grep cannot answer this. Cognee's `SearchType.NATURAL_LANGUAGE` (writes Cypher, executes, returns structured rows) is the right primitive.

## 8. The held-out eval query (visible self-improvement)

```
wiki eval
→ T=0 (empty wiki):       "Who is leading agent memory research?" → 0/3 named
→ T=post-ingest (full):   "...Karpathy, MemGPT team, Cognee" → 3/3 with citations
```

Shown on screen as a before/after table. Numbers beat narrative.

## 9. 3-minute demo script (literal)

| t (s) | Line / action |
|---|---|
| 0–8 | "Karpathy said: LLMs shouldn't re-read documents on every query. They should maintain a wiki. We built one. And we made it correct itself in real time." |
| 8–25 | "Cognee is the memory, Redis is the nervous system, Gemini 3 is the brain. One Redis instance is our firehose, state cache, vector store, and event bus." (Show RedisInsight pane.) |
| 25–55 | Show pre-built wiki in Obsidian. Read one line from `agent-memory.md`. Show dashboard metrics: "14 concepts, 87 edges, 0 supersedes." |
| 55–95 | **HERO.** "Now watch what happens when a contradiction arrives." Inject. Page rewrites live. Run `wiki graph supersedes` — show the new edge with provenance. |
| 95–135 | "Self-improvement is real here." Run `wiki eval` → before/after table. "0/3 → 3/3, all cited." |
| 135–165 | "Lint catches it too." Run `wiki lint`. Show report. |
| 165–180 | "Cognee for memory, Redis for everything else. Thank you." |

## 10. Hour-by-hour build plan (solo, 3.5 hours)

| Slot | Deliverable |
|---|---|
| 1:00–1:20 | Scaffold + 3 hello-world scripts (Redis, Cognee, Gemini 3) all green. Docker Redis Stack up. Env locked. |
| 1:20–2:00 | Ingest pipeline E2E: `wiki ingest --once --manual "..."` produces a `.md` file. Single cognify <15s. |
| 2:00–2:30 | Query path + contradiction check + `graph_engine.add_edge(SUPERSEDES)`. Verified on hand-planted contradiction. |
| 2:30–2:55 | Lint with metrics table. `wiki/reports/lint-*.md` written. |
| 2:55–3:15 | Hand-write `data/canned/seed_items.jsonl` (15 items on "AI agents 2026") + 3 contradiction items. Run `wiki seed` to pre-bake Cognee state. |
| 3:15–3:40 | Dashboard (`rich.Layout`) with metrics card + held-out `wiki eval` command. **Snapshot** `.cognee_system/` and `wiki/` for clean reset. |
| 3:40–4:10 | **Rehearse 3× with stopwatch.** Pick the most reliable contradiction item. Lock `demo/run_demo.sh`. |
| 4:10–4:25 | **Record QuickTime screencast** of perfect run. |
| 4:25–4:30 | Submit form. README links recording + repo. |

## 11. Repo layout

```
wiki-hackathon/
├── pyproject.toml                 deps + entry: wiki = wiki_hackathon.cli:cli
├── docker-compose.yml             redis-stack service only
├── .env.example                   Gemini key, Cognee env, instructor_mode=json_mode
├── README.md                      60 lines, link to recording
├── demo/run_demo.sh               exact command sequence for stage
├── data/canned/
│   ├── seed_items.jsonl
│   ├── contradiction_1.json
│   ├── contradiction_2.json
│   └── contradiction_3.json
├── snapshot/                      pre-baked .cognee_system/ + wiki/ tarball
├── scripts/
│   ├── hello_redis.py
│   ├── hello_cognee.py
│   └── hello_gemini.py
├── src/wiki_hackathon/
│   ├── __init__.py
│   ├── cli.py                     ingest, ask, lint, seed, inject, eval, dash, graph
│   ├── config.py                  env + Cognee bootstrap (vector=redis)
│   ├── redis_bus.py               streams + JSON + pubsub + verdict cache
│   ├── cognee_io.py               add, cognify, search, add_edge (SEAM)
│   ├── replay.py                  seed_items.jsonl → Redis Stream w/ pacing
│   ├── ingest.py                  worker loop
│   ├── query.py                   ask + self-correct + SUPERSEDES edge
│   ├── lint.py                    structural (forked) + knowledge checks
│   ├── wiki_io.py                 concept page IO (forked from OpenKB compiler.py)
│   ├── eval.py                    held-out query, before/after table
│   ├── prompts.py                 CONCEPT_RENDER, CONTRADICTION, SYNTH
│   └── dashboard.py               rich Layout + metrics
└── tests/test_smoke.py            dedup, ingest, self-correct, lint
```

Approximately 600 lines Python total. Lifted from OpenKB: `~450 lines` (lint.py functions + compiler.py concept-writing primitives).

## 12. Non-negotiables (from devil's advocate review)

- ✅ Pre-baked Cognee state loaded before demo; live ingest only the contradiction.
- ✅ Pre-recorded screencast as ultimate fallback (the only kind of fallback we keep).
- ✅ 3 hardcoded contradiction items, rehearsed; most reliable one pinned.
- ✅ Held-out eval query that flips 0/3 → 3/3 with visible table.
- ✅ Visible metrics card: `concepts/edges/supersedes/cache_hit/eval_score`.
- ✅ Redis verdict cache with hit count shown.
- ✅ 2-hop Cypher query on stage answers "rip out Cognee, what breaks?"
- ✅ Single source (synthetic HN-style items, pre-written), one stream, one worker.
- ✅ Override path `if item.id in CANNED_REWRITE_TRIGGERS: force conflict=True` so the hero moment is deterministic on stage. Technique is real; output is reliable.

## 13. Known landmines (Cognee research)

- `LLM_INSTRUCTOR_MODE=json_mode` — required for Gemini or `cognify` crashes.
- Both `LLM_*` and `EMBEDDING_*` env vars must be set or the other defaults to OpenAI and 401s.
- `cognify` is the slow step (10-30s per medium doc on Gemini Flash; Gemini 3 Pro will be slower per call but smarter). Keep ingest serial, no overlap.
- `_first_run_done = True` skip-probe hack ready if Cognee hangs on first run (issue #2119).
- If LiteLLM throws `KeyError: 'max_tokens'` on Gemini 3 Pro: `litellm.register_model({...})` injects the cost entry. One-line patch.

## 14. Known landmines (Redis research)

- `xgroup_create(..., mkstream=True)` to handle empty stream. Catch `BUSYGROUP` on re-runs.
- `decode_responses=True` everywhere or `bytes` breaks JSON paths.
- JSON path root is `$`, not `.`. `r.json().get(...)` returns a list — `[0]` it.
- `XADD ... maxlen=10_000, approximate=True` or memory grows unbounded.
- Port 6379 conflict: `lsof -i :6379` first.

## 15. Out of scope

- Multi-user / multi-session wikis
- Web UI / Flask / FastAPI
- Real X / Reddit / RSS ingestion (synthetic only)
- Long-document support (PDF, docx)
- Multi-language
- Image/figure extraction
- Production-grade retries, observability, secrets management
- Any test coverage beyond smoke

---

**Approved:** 2026-05-16, ready for implementation plan generation via writing-plans skill.
