# Team Submission — Palimpsest

## Team

- Team name: Palimpsest
- Participants: Nihal Nihalani
- Wiki / project name: **Palimpsest** — a self-correcting LLM wiki. The graph itself remembers what was true before via `SUPERSEDES` edges (the palimpsest trace).

## Wiki Overview

A self-correcting LLM wiki: a topic firehose streams in, an ingest worker
maintains a Karpathy-style markdown wiki, and the agent **rewrites pages plus
writes a `:SUPERSEDES` edge into the Cognee knowledge graph** when
contradictions arrive. Self-improvement is concrete: a held-out eval flips
from 0/3 → 3/3 after the new evidence is ingested, with citations.

- **Domain or data sources:** synthetic topic firehose (canned items for the
  demo) — designed to mimic news/social ingestion of AI-memory research.
- **Primary use case:** keep an evolving knowledge wiki coherent without
  manual editing as new (potentially conflicting) evidence arrives.
- **What makes it stand out:** the graph itself records what was true before
  via `SUPERSEDES` edges, so the wiki carries provenance + evolution, not
  just current state. Plus a real cognee 1.x `SkillRunEntry` →
  `improve_skill` propose-then-apply self-improvement loop, exposed as
  `wiki improve`.

## The Three Operations

### Ingest

- **What goes in:** synthetic items `{title, body, source, url}` pushed to a
  Redis Stream (`firehose:items`).
- **How it is captured:** `cognee.add(text, dataset_name="wiki",
  node_set=[f"source:{source}"])` then `cognee.cognify()`. The
  `cognee.remember(..., session_id=...)` path is exercised by the skill
  loop (see below) — that routes the agent's working memory through Redis
  as the hackathon brief's two-tier pattern requires.
- **Code entry point:** `wiki ingest --once` →
  `src/palimpsest/ingest.py::process_one`.

### Query + Self-improve

- **How users query the wiki:** `wiki ask "<question>"` →
  `src/palimpsest/query.py::ask` →
  `cognee.search(GRAPH_COMPLETION)` + Gemini synthesis. Answers are
  semantically cached in Redis via **RedisVL `SemanticCache`**
  (`src/palimpsest/answer_cache.py`) for ~15 minutes, keyed by
  Gemini-embedded question.
- **Where feedback comes from:**
  1. Per-ingest: `query.check_contradiction` (Gemini call, exact-match
     Redis-cached) — when the new item contradicts an existing page, the
     page is rewritten and a `SUPERSEDES` edge is written into the Cognee
     graph itself.
  2. Skill loop: `wiki improve --record <skill_name> --score <0..1>`
     records a `SkillRunEntry` and (if score < threshold) proposes a
     `SKILL.md` rewrite via `cognee.remember(SkillRunEntry, …,
     skill_improvement={apply: False})`. `wiki improve --status` shows
     the latest proposal id; `wiki improve --apply <proposal_id>` then
     commits the rewrite.
- **How feedback updates the wiki:** SUPERSEDES edges, page rewrites,
  Redis Stream `wiki:evolution` (audit), Redis Pub/Sub `wiki:events`,
  and now `SkillRunEntry` + `improve_skill(apply=True)` rewriting
  `my_skills/*/SKILL.md` on disk.
- **Code entry point:** `wiki ask` (`query.ask`); `wiki improve`
  (`src/palimpsest/skill_loop.py`).

### Lint

- **What "linting" means:** dedupe stale Obsidian wikilinks, strip
  invented `[[...]]` references that point at nonexistent slugs, and
  fuzzy-repoint near-misses. Optionally rewrite Markdown in-place via
  `--fix`.
- **How it runs:** on-demand via CLI (`wiki lint --fix`); the report is
  written to `wiki/reports/`.
- **Code entry point:** `src/palimpsest/lint.py`.

## Self-Improvement Evidence

See `docs/evidence/` for raw runs. Held-out eval question:

> "Who is leading agent memory research in 2026? Name specific people,
> projects, or companies." (required terms: Karpathy, MemGPT, Cognee)

### Baseline Run (pre-ingest)

- Query / task: `wiki eval`
- Result: see `docs/evidence/eval_baseline_runs.json` (N=5)
- Score: see median in `docs/evidence/eval_summary.json`
- Recorded feedback:
  ```text
  error_type: missing_required_terms
  error_message: answer omitted required terms (see "missing" field per run)
  feedback: -1.0
  success_score: median(baseline)/3
  ```

### Improved Run (post-ingest)

- Query / task: `wiki eval` (same question)
- Result: see `docs/evidence/eval_improved_runs.json` (N=5)
- Score: see `docs/evidence/eval_summary.json`
- What changed in the wiki between runs: ingested 3 canned contradiction
  items via `wiki inject-canned contradiction_{1,2,3}` followed by
  `wiki ingest`; concept pages updated; `SUPERSEDES` edges written.

```text
Before:
  median score = (see eval_summary.json)
  missing = ["Karpathy", "MemGPT", "Cognee"]
After:
  median score = (see eval_summary.json)
  missing = []
```

## Architecture

```text
                    [ agent / user ]
                           │
                           ▼
          ┌──────────────────────────────────────┐
          │  Redis — session memory               │   fast, ephemeral
          │  • Streams (firehose:items, wiki:evolution)│   per-conversation
          │  • RedisJSON (wiki:concept:<slug>)     │
          │  • Pub/Sub (wiki:events)               │
          │  • RedisVL SemanticCache (wiki:answer) │
          │  • cognee session_id= routing          │
          └────────────────┬─────────────────────┘
                           │  distillation
                           ▼
          ┌──────────────────────────────────────┐
          │  Cognee — permanent memory            │   structured, durable
          │  • Kuzu graph (entities + SUPERSEDES) │   cross-session
          │  • LanceDB embeddings                 │
          │  • SKILL rewriting via improve_skill  │
          └──────────────────────────────────────┘
                           │
                           ▼
                  [ recall / agent loop ]
                           │
                           ▼
                [ feedback → improve (SkillRunEntry) ]
```

### Redis-as-session-memory

- **What the agent writes into Redis:**
  1. Raw incoming items as Stream entries (`firehose:items`).
  2. Each rewrite to a concept page is mirrored to `wiki:concept:<slug>`
     (RedisJSON) and audit-logged to `wiki:evolution` (Stream) with a
     reason field. Dashboard subscribes to `wiki:events` (Pub/Sub) for
     live updates.
  3. Contradiction verdicts (exact-match) cached at `wiki:verdict:*` with
     10-min TTL.
  4. Answer cache (semantic) at `wiki:answer-cache:*` with 15-min TTL,
     vectorised via Gemini's `text-embedding-004`.
  5. `cognee.remember(..., session_id="wiki-improve")` working memory is
     routed through Redis automatically by cognee 1.x when `REDIS_URL`
     is set (`CACHING=true`).
- **How and when content is distilled into the graph:** the ingest
  worker calls `cognee.add` + `cognee.cognify` on every accepted item,
  which performs entity/relationship extraction into the Kuzu graph and
  LanceDB embeddings. Contradiction detection then writes explicit
  `SUPERSEDES` edges between old- and new-claim nodes — that's the
  hackathon's "distillation step" made concrete.
- **What stays in Redis vs. what gets promoted:** Redis keeps the hot
  audit trail (every page version, every event), the verdict cache, the
  semantic answer cache, and session working memory. The graph keeps
  the structured truth: entity nodes, `SUPERSEDES` chains, INFERRED
  edges from `wiki rethink`.
- **How distillation quality improved between baseline and improved
  run:** the held-out eval score moves from baseline median → improved
  median once the canned contradiction items are ingested. See
  `docs/evidence/eval_summary.json`. The `wiki improve` skill-loop
  records each agent run as a `SkillRunEntry`; below threshold scores
  generate `SKILL.md` rewrite proposals that are explicitly applied via
  `improve_skill(apply=True)`.

## Agents / Skills

```text
Skill path(s): my_skills/
Roles:
  - Ingestor:   my_skills/wiki-ingest/SKILL.md   — process raw items into
                                                   concept pages + edges
  - Querier:    my_skills/wiki-query/SKILL.md    — answer questions via
                                                   GRAPH_COMPLETION + synthesis
  - Critic:     my_skills/code-review/SKILL.md   — review wiki self-correction
                                                   edits for accuracy
```

The propose-then-apply loop lives in `src/palimpsest/skill_loop.py`
and exposes:

- `wiki improve --remember`   — ingest `./my_skills` into cognee
- `wiki improve --run <skill> --prompt "..."`  — execute the skill
- `wiki improve --record <skill> --score 0.3 --task-text "..."`  —
  write a `SkillRunEntry` and propose a rewrite
- `wiki improve --apply <proposal_id>`  — commit the rewrite to disk

## Reproduction

```bash
# 1. Clone + set up venv
git clone https://github.com/nihalnihalani/palimpsest.git
cd palimpsest
./run.sh setup        # creates venv + brings up Redis + scaffolds .env

# 2. Add your LLM key
echo "GEMINI_API_KEY=..." >> .env

# 3. End-to-end
./run.sh doctor       # diagnostic
./run.sh verify       # 9-step smoke
./run.sh seed         # wiki reset && wiki seed && wiki load-baseline
./run.sh demo         # interactive 3-min stage flow

# 4. Self-improvement loop
wiki improve --remember
wiki improve --run code-review --prompt "Review the latest concept rewrite"
wiki improve --record code-review --score 0.3 --task-text "Reviewed agent-memory rewrite"
wiki improve --apply <proposal_id-printed-above>

# 5. Evidence
wiki eval                       # held-out eval, run once
python -m palimpsest.cli evidence  # run N=5 and persist
ls docs/evidence/
```

Environment variables required:

```text
GEMINI_API_KEY                 # Gemini for LLM + embeddings
LLM_API_KEY=${GEMINI_API_KEY}  # cognee LiteLLM (alias)
LLM_PROVIDER=gemini
LLM_MODEL=gemini/gemini-3-pro
LLM_ENDPOINT=https://generativelanguage.googleapis.com/
LLM_API_VERSION=v1beta
LLM_INSTRUCTOR_MODE=json_mode
EMBEDDING_PROVIDER=gemini
EMBEDDING_MODEL=text-embedding-004
EMBEDDING_API_KEY=${GEMINI_API_KEY}
EMBEDDING_DIMENSIONS=768
GRAPH_DATABASE_PROVIDER=kuzu
REDIS_URL=redis://localhost:6379
ENABLE_BACKEND_ACCESS_CONTROL=false
CACHING=true                   # cognee 1.x session memory routing to Redis
```

(`VECTOR_DB_PROVIDER` is intentionally unset — cognee 1.x defaults to
LanceDB, which is what we use. The community Redis vector adapter is a
separate package and not used in this submission; see
`docs/plans/2026-05-17-full-hackathon-spec-design.md` for the rationale.)

## Demo

- Live demo link: (record after running `demo/run_demo.sh`)
- 3-minute pitch outline:

```text
1. Problem — Karpathy's LLM wiki, but it has to *correct itself* live.
2. Ingest — `wiki inject-canned contradiction_1 && wiki ingest --once`
   shows the page rewrite in Obsidian + the new SUPERSEDES edge.
3. Query (before improve) — `wiki ask "<question>"` shows the wiki's
   current view (semantic-cached on second call).
4. Self-improve step — `wiki improve --record … --apply` shows the
   SkillRunEntry → improve_skill → SKILL.md rewritten on disk.
5. Query (after improve) — same question, new SKILL produces a more
   accurate answer.
6. What's next — extend to live RSS firehose; multi-graph; Redis Cloud.
```

## Links

- Repo: https://github.com/nihalnihalani/palimpsest
- Design + plan: [`docs/plans/2026-05-17-full-hackathon-spec-design.md`](docs/plans/2026-05-17-full-hackathon-spec-design.md)
- Earlier design: [`docs/plans/2026-05-16-wiki-hackathon-design.md`](docs/plans/2026-05-16-wiki-hackathon-design.md)
- Evidence: [`docs/evidence/`](docs/evidence/)
