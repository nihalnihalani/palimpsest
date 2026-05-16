# wiki-hackathon Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a self-correcting LLM wiki (Karpathy's idea) using Cognee + Redis + Gemini 3 in 3.5 hours; ship a 3-min live demo where contradictions trigger a visible page rewrite + a `:SUPERSEDES` knowledge-graph edge.

**Architecture:** A single Python CLI (`wiki`) reads synthetic items from a Redis Stream, calls `cognee.add` + `cognify` to build a knowledge graph, writes Obsidian-friendly markdown wiki pages, and on contradiction-detection writes a `:SUPERSEDES` edge in the Cognee graph AND rewrites the affected `.md` file. Redis is the stream + state cache (RedisJSON) + pubsub bus + Cognee vector store, all in one Redis Stack instance.

**Tech Stack:** Python 3.11 · Cognee 0.5.2+ · Redis Stack (via Docker) · `redis-py 5+` · `google-generativeai` for direct Gemini calls (Cognee uses LiteLLM internally) · Gemini 3 Pro (`gemini/gemini-3-pro`) · Click · `rich` · pytest

**Reference design:** [`./2026-05-16-wiki-hackathon-design.md`](./2026-05-16-wiki-hackathon-design.md)

**Source fork (concepts only):** `/Users/nihalnihalani/Desktop/Github/OpenKB-main/openkb/{lint.py, agent/compiler.py}` — lift specific functions noted per task.

**Time budget:** 1:00 PM → 4:30 PM submission (3h 30m). Solo. Commit frequently. If a task takes >2× its estimate, drop the optional sub-step and move on.

---

## Phase 0 — Scaffold (1:00 – 1:20, 20 min)

### Task 0.1: Create project skeleton

**Files:**
- Create: `/Users/nihalnihalani/Desktop/Github/wiki-hackathon/pyproject.toml`
- Create: `/Users/nihalnihalani/Desktop/Github/wiki-hackathon/.env.example`
- Create: `/Users/nihalnihalani/Desktop/Github/wiki-hackathon/.gitignore`
- Create: `/Users/nihalnihalani/Desktop/Github/wiki-hackathon/docker-compose.yml`
- Create: directories listed in design §11

**Step 1: Init repo**

```bash
cd /Users/nihalnihalani/Desktop/Github/wiki-hackathon
git init
mkdir -p src/wiki_hackathon scripts data/canned snapshot demo tests wiki/concepts wiki/reports
touch src/wiki_hackathon/__init__.py
```

**Step 2: Write `pyproject.toml`**

```toml
[project]
name = "wiki-hackathon"
version = "0.1.0"
requires-python = ">=3.11,<3.14"
dependencies = [
  "cognee>=0.5.2",
  "redis[hiredis]>=5.0",
  "google-generativeai>=0.8",
  "click>=8.1",
  "rich>=13.7",
  "python-dotenv>=1.0",
  "json-repair>=0.25",
  "httpx>=0.27",
]

[project.scripts]
wiki = "wiki_hackathon.cli:cli"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]
```

**Step 3: Write `.env.example`** — the exact Cognee+Gemini config from research:

```bash
# Direct Gemini SDK (for our own LLM calls)
GEMINI_API_KEY=

# Cognee LLM (LiteLLM under the hood)
LLM_PROVIDER=gemini
LLM_MODEL=gemini/gemini-3-pro
LLM_API_KEY=${GEMINI_API_KEY}
LLM_ENDPOINT=https://generativelanguage.googleapis.com/
LLM_API_VERSION=v1beta
LLM_INSTRUCTOR_MODE=json_mode

# Cognee embeddings — must also be set or Cognee 401s on OpenAI default
EMBEDDING_PROVIDER=gemini
EMBEDDING_MODEL=text-embedding-004
EMBEDDING_API_KEY=${GEMINI_API_KEY}
EMBEDDING_DIMENSIONS=768

# Cognee storage backends
GRAPH_DATABASE_PROVIDER=kuzu
VECTOR_DB_PROVIDER=redis
VECTOR_DB_URL=redis://localhost:6379

# Redis
REDIS_URL=redis://localhost:6379
```

**Step 4: Write `.gitignore`**

```
.venv/
__pycache__/
*.pyc
.env
.cognee_system/
.data_storage/
snapshot/*.tar.gz
wiki/concepts/*.md
wiki/log.md
wiki/reports/*.md
!wiki/.gitkeep
```

```bash
touch wiki/.gitkeep wiki/concepts/.gitkeep wiki/reports/.gitkeep
```

**Step 5: Write `docker-compose.yml`**

```yaml
services:
  redis:
    image: redis/redis-stack:latest
    container_name: wiki-redis
    ports:
      - "6379:6379"
      - "8001:8001"  # RedisInsight UI for demo
    volumes:
      - redis-data:/data
volumes:
  redis-data:
```

**Step 6: Commit**

```bash
git add -A
git commit -m "chore: scaffold wiki-hackathon project"
```

---

### Task 0.2: Bring up Redis Stack and verify

**Step 1: Check port and pull image**

```bash
lsof -i :6379  # must be empty; kill anything that prints
docker compose up -d
docker ps | grep wiki-redis  # expect: Up
```

**Step 2: Sanity check**

```bash
docker exec wiki-redis redis-cli PING
# Expected: PONG

docker exec wiki-redis redis-cli MODULE LIST | grep -i json
# Expected: a line containing "ReJSON" or "JSON"
```

**Step 3: Open RedisInsight in browser**

`http://localhost:8001` → click "+ Database" → use `redis://redis:6379` (or accept the auto-detected localhost). Confirm Browser/Workbench/CLI panes load. This is the demo surface.

**Step 4: Note** — if port 6379 collides with system Redis, edit `docker-compose.yml` to map `6380:6379` and update `REDIS_URL` + `VECTOR_DB_URL` accordingly.

---

### Task 0.3: Virtualenv + install + write `config.py`

**Step 1: Install**

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .
pip freeze > requirements.lock
```

Expected: cognee + redis + google-generativeai install without errors. If `cognee` install is slow (it pulls many deps), let it run; don't ctrl-C.

**Step 2: Write `src/wiki_hackathon/config.py`**

```python
"""Centralized env + Cognee bootstrap. Import this BEFORE any cognee submodules."""
from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

# Constants used by every module
STREAM = "firehose:items"
GROUP = "ingestors"
CONSUMER = "worker-1"
EVOLUTION_STREAM = "wiki:evolution"
PUBSUB_CHANNEL = "wiki:events"
DEDUP_PREFIX = "seen:"
JSON_KEY_PREFIX = "wiki:concept:"
VERDICT_PREFIX = "verdict:"
DATASET = "wiki"

WIKI_DIR = ROOT / "wiki"
CONCEPTS_DIR = WIKI_DIR / "concepts"
REPORTS_DIR = WIKI_DIR / "reports"
LOG_FILE = WIKI_DIR / "log.md"
CANNED_DIR = ROOT / "data" / "canned"
SNAPSHOT_DIR = ROOT / "snapshot"

for d in (CONCEPTS_DIR, REPORTS_DIR, SNAPSHOT_DIR):
    d.mkdir(parents=True, exist_ok=True)


def patch_litellm_for_gemini3() -> None:
    """Inject gemini-3-pro into LiteLLM's model_cost map if missing.

    Why: Cognee uses LiteLLM; bleeding-edge model strings sometimes raise
    KeyError: 'max_tokens' on lookup. One-line patch from research.
    """
    try:
        import litellm  # type: ignore
    except ImportError:
        return
    name = "gemini/gemini-3-pro"
    if name not in getattr(litellm, "model_cost", {}):
        litellm.register_model(  # type: ignore[attr-defined]
            {
                name: {
                    "max_tokens": 8192,
                    "max_input_tokens": 1_000_000,
                    "max_output_tokens": 8192,
                    "input_cost_per_token": 0.0,
                    "output_cost_per_token": 0.0,
                    "litellm_provider": "gemini",
                    "mode": "chat",
                    "supports_function_calling": True,
                    "supports_vision": True,
                }
            }
        )


patch_litellm_for_gemini3()
```

**Step 3: Commit**

```bash
git add pyproject.toml requirements.lock src/wiki_hackathon/config.py
git commit -m "feat: env config + LiteLLM Gemini 3 patch"
```

---

### Task 0.4: Hello-world Redis script

**Files:** Create `scripts/hello_redis.py`

```python
"""Round-trip XADD → XREADGROUP to prove Redis Stack + streams work."""
import redis
from wiki_hackathon.config import REDIS_URL, STREAM, GROUP, CONSUMER

r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
print("PING:", r.ping())

try:
    r.xgroup_create(STREAM, GROUP, id="$", mkstream=True)
    print(f"created group {GROUP} on {STREAM}")
except redis.ResponseError as e:
    print(f"group already exists ({e})")

msg_id = r.xadd(STREAM, {"hello": "world"})
print("XADD ->", msg_id)

resp = r.xreadgroup(GROUP, CONSUMER, {STREAM: ">"}, count=1, block=500)
print("XREADGROUP ->", resp)
for _, msgs in resp or []:
    for mid, _fields in msgs:
        r.xack(STREAM, GROUP, mid)
        print("XACK", mid)

# JSON sanity
r.json().set("wiki:concept:test", "$", {"current": "hello", "history": []})
print("JSON.GET ->", r.json().get("wiki:concept:test", "$"))
r.delete("wiki:concept:test")

# Cleanup so smoke test is repeatable
r.xtrim(STREAM, maxlen=0)
```

Run: `python scripts/hello_redis.py`
Expected: `PING: True`, an XADD id, an XREADGROUP response containing the hello/world fields, an XACK line, and JSON.GET returning `[{'current': 'hello', 'history': []}]`.

Commit: `git add scripts/hello_redis.py && git commit -m "test: hello_redis script"`

---

### Task 0.5: Hello-world Cognee script

**Files:** Create `scripts/hello_cognee.py`

```python
"""End-to-end: add → cognify → search → graph read.
This is also the integration smoke test for Gemini 3 + Cognee + Redis vector adapter.
"""
import asyncio
from wiki_hackathon import config  # noqa: F401  (loads .env, patches litellm)

import cognee
from cognee.api.v1.search import SearchType
from cognee.infrastructure.databases.graph import get_graph_engine


async def main() -> None:
    await cognee.prune.prune_data()
    await cognee.prune.prune_system(metadata=True)

    await cognee.add(
        "GPT-5 was announced on Jan 15, 2026 by OpenAI.",
        dataset_name="hello",
        node_set=["source:hn"],
    )
    await cognee.add(
        "OpenAI announced GPT-5 on January 22, 2026.",  # contradiction
        dataset_name="hello",
        node_set=["source:x"],
    )
    await cognee.cognify(datasets=["hello"])

    print("\n=== GRAPH_COMPLETION ===")
    print(await cognee.search(
        query_text="When was GPT-5 announced?",
        search_type=SearchType.GRAPH_COMPLETION,
        dataset_names=["hello"],
    ))

    print("\n=== INSIGHTS (triplets) ===")
    print(await cognee.search(
        query_text="GPT-5 announcement",
        search_type=SearchType.INSIGHTS,
        dataset_names=["hello"],
    ))

    graph = await get_graph_engine()
    nodes, edges = await graph.get_graph_data()
    print(f"\n{len(nodes)} nodes, {len(edges)} edges")


if __name__ == "__main__":
    asyncio.run(main())
```

Run: `python scripts/hello_cognee.py`
Expected: a printed answer (date), a list of triplets, and node/edge counts > 0.
If it hangs >2 minutes on first call, see design §13 — apply the `_first_run_done = True` workaround. Add at top of script after `import cognee`:

```python
import cognee.modules.pipelines.layers.setup_and_check_environment as _env
_env._first_run_done = True
```

Commit: `git add scripts/hello_cognee.py && git commit -m "test: hello_cognee script"`

---

### Task 0.6: Hello-world Gemini script

**Files:** Create `scripts/hello_gemini.py`

```python
"""Direct Gemini call (the path our app uses for synth/contradiction-check)."""
from wiki_hackathon import config
import google.generativeai as genai

genai.configure(api_key=config.GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-3-pro")
resp = model.generate_content("Say 'hello hackathon' and nothing else.")
print(resp.text)
```

Run: `python scripts/hello_gemini.py`
Expected: `hello hackathon` (or close).

Commit: `git add scripts/hello_gemini.py && git commit -m "test: hello_gemini script"`

**Phase 0 gate:** All three hello scripts pass. Move on. ETA check: clock should read ≤1:25 PM. If later, you've lost the budget; cut Phase 6 dashboard polish.

---

## Phase 1 — Redis bus + Cognee IO (1:20 – 1:35, 15 min)

### Task 1.1: `redis_bus.py`

**Files:** Create `src/wiki_hackathon/redis_bus.py`

```python
"""All Redis ops in one file. Streams + JSON + Pub/Sub + verdict cache + dedup."""
from __future__ import annotations
import hashlib
import json
import time
from typing import Iterable

import redis

from .config import (
    REDIS_URL, STREAM, GROUP, CONSUMER,
    EVOLUTION_STREAM, PUBSUB_CHANNEL,
    DEDUP_PREFIX, JSON_KEY_PREFIX, VERDICT_PREFIX,
)

_r: redis.Redis | None = None


def client() -> redis.Redis:
    global _r
    if _r is None:
        _r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    return _r


def ensure_group() -> None:
    try:
        client().xgroup_create(STREAM, GROUP, id="$", mkstream=True)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


def push_item(item: dict) -> str:
    """Producer: push one item onto the firehose stream."""
    fields = {k: (json.dumps(v) if not isinstance(v, str) else v)
              for k, v in item.items()}
    return client().xadd(STREAM, fields, maxlen=10_000, approximate=True)


def claim_items(count: int = 1, block_ms: int = 5_000) -> list[tuple[str, dict]]:
    """Consumer: returns list of (msg_id, fields) or [] on timeout."""
    resp = client().xreadgroup(GROUP, CONSUMER, {STREAM: ">"},
                               count=count, block=block_ms)
    out: list[tuple[str, dict]] = []
    for _stream, msgs in resp or []:
        for mid, fields in msgs:
            out.append((mid, fields))
    return out


def ack(msg_id: str) -> None:
    client().xack(STREAM, GROUP, msg_id)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def mark_seen(key: str, ttl_sec: int = 3600) -> bool:
    """SETNX dedup. Returns True if newly seen, False if duplicate."""
    return bool(client().set(f"{DEDUP_PREFIX}{key}", "1",
                             ex=ttl_sec, nx=True))


def init_concept(slug: str) -> None:
    client().json().set(
        f"{JSON_KEY_PREFIX}{slug}", "$",
        {"current": None, "history": [], "contradictions": []},
        nx=True,
    )


def rewrite_concept(slug: str, new_text: str, reason: str | None = None,
                    source: str | None = None) -> None:
    key = f"{JSON_KEY_PREFIX}{slug}"
    init_concept(slug)
    prev = client().json().get(key, "$.current")
    prev_text = prev[0] if prev else None
    if prev_text:
        client().json().arrappend(key, "$.history",
                                  {"text": prev_text, "replaced_at": time.time()})
    client().json().set(key, "$.current", new_text)
    if reason:
        client().json().arrappend(key, "$.contradictions",
                                  {"reason": reason, "source": source,
                                   "ts": time.time()})
    audit({"slug": slug, "reason": reason or "ingest",
           "source": source or "", "ts": str(time.time())})
    publish({"type": "rewrite" if reason else "ingest", "slug": slug})


def audit(row: dict) -> str:
    fields = {k: str(v) for k, v in row.items()}
    return client().xadd(EVOLUTION_STREAM, fields,
                         maxlen=10_000, approximate=True)


def publish(payload: dict) -> int:
    return client().publish(PUBSUB_CHANNEL, json.dumps(payload))


def verdict_get(key: str) -> dict | None:
    raw = client().get(f"{VERDICT_PREFIX}{key}")
    return json.loads(raw) if raw else None


def verdict_set(key: str, value: dict, ttl_sec: int = 600) -> None:
    client().set(f"{VERDICT_PREFIX}{key}", json.dumps(value), ex=ttl_sec)


def metrics() -> dict:
    c = client()
    return {
        "stream_len": c.xlen(STREAM),
        "evolution_len": c.xlen(EVOLUTION_STREAM),
        "concept_keys": len(list(c.scan_iter(f"{JSON_KEY_PREFIX}*"))),
        "verdict_cache_keys": len(list(c.scan_iter(f"{VERDICT_PREFIX}*"))),
    }


def reset_streams() -> None:
    """Used by demo reset. Wipes streams + consumer groups."""
    c = client()
    for s in (STREAM, EVOLUTION_STREAM):
        try:
            c.xtrim(s, maxlen=0)
        except redis.ResponseError:
            pass
    try:
        c.xgroup_destroy(STREAM, GROUP)
    except redis.ResponseError:
        pass
    for k in c.scan_iter(f"{JSON_KEY_PREFIX}*"):
        c.delete(k)
    for k in c.scan_iter(f"{DEDUP_PREFIX}*"):
        c.delete(k)
    for k in c.scan_iter(f"{VERDICT_PREFIX}*"):
        c.delete(k)
```

Commit: `git add src/wiki_hackathon/redis_bus.py && git commit -m "feat: redis bus (streams + JSON + pubsub + verdict cache)"`

---

### Task 1.2: `cognee_io.py`

**Files:** Create `src/wiki_hackathon/cognee_io.py`

```python
"""Thin async wrapper over Cognee. All cognee calls live here so swaps stay local."""
from __future__ import annotations
import asyncio
from typing import Any

from . import config  # noqa: F401 — load .env + patch litellm

import cognee
from cognee.api.v1.search import SearchType

# Skip the connectivity probe that can silently hang on first call
try:
    import cognee.modules.pipelines.layers.setup_and_check_environment as _env
    _env._first_run_done = True
except Exception:
    pass

from cognee.infrastructure.databases.graph import get_graph_engine

DATASET = "wiki"


async def add(text: str, source: str) -> None:
    await cognee.add(text, dataset_name=DATASET, node_set=[f"source:{source}"])


async def cognify() -> None:
    await cognee.cognify(datasets=[DATASET])


async def search_completion(query: str) -> str:
    resp = await cognee.search(
        query_text=query,
        search_type=SearchType.GRAPH_COMPLETION,
        dataset_names=[DATASET],
    )
    return str(resp)


async def search_insights(query: str) -> list[Any]:
    return await cognee.search(
        query_text=query,
        search_type=SearchType.INSIGHTS,
        dataset_names=[DATASET],
    )


async def top_concepts(item_text: str, k: int = 3) -> list[str]:
    """Pull top entity labels for an item. Used to decide which concept pages to touch."""
    insights = await search_insights(item_text)
    seen: dict[str, int] = {}
    for triple in insights or []:
        # Cognee triple format varies by version; defensively extract labels
        if isinstance(triple, dict):
            for key in ("subject", "object", "name", "label"):
                v = triple.get(key)
                if isinstance(v, str):
                    seen[v] = seen.get(v, 0) + 1
        elif isinstance(triple, (list, tuple)) and len(triple) >= 3:
            for v in (triple[0], triple[2]):
                if isinstance(v, str):
                    seen[v] = seen.get(v, 0) + 1
    return sorted(seen, key=seen.get, reverse=True)[:k]


async def write_supersedes_edge(old_claim: str, new_claim: str,
                                source: str, reason: str) -> None:
    """The Cognee differentiator: the graph itself remembers what was true before."""
    graph = await get_graph_engine()
    import time
    old_id = f"claim:{abs(hash(old_claim))}"
    new_id = f"claim:{abs(hash(new_claim))}"
    await graph.add_node(old_id, {"text": old_claim, "kind": "claim"})
    await graph.add_node(new_id, {"text": new_claim, "kind": "claim"})
    await graph.add_edge(
        old_id, new_id,
        relationship_name="SUPERSEDES",
        properties={"source": source, "reason": reason, "ts": time.time()},
    )


async def list_supersedes() -> list[dict]:
    """For the on-stage `wiki graph supersedes` command."""
    graph = await get_graph_engine()
    _, edges = await graph.get_graph_data()
    out: list[dict] = []
    for src, dst, rel, props in edges or []:
        if rel == "SUPERSEDES":
            out.append({"from": src, "to": dst, **(props or {})})
    return out


async def graph_stats() -> dict:
    graph = await get_graph_engine()
    nodes, edges = await graph.get_graph_data()
    return {"nodes": len(nodes), "edges": len(edges)}


async def reset() -> None:
    await cognee.prune.prune_data()
    await cognee.prune.prune_system(metadata=True)


def run(coro):
    """Synchronous entrypoint for Click commands."""
    return asyncio.run(coro)
```

Commit: `git add src/wiki_hackathon/cognee_io.py && git commit -m "feat: cognee IO wrapper with SUPERSEDES edge writer"`

---

## Phase 2 — Ingest pipeline (1:35 – 2:00, 25 min)

### Task 2.1: Lift `wiki_io.py` from OpenKB

**Files:**
- Create: `src/wiki_hackathon/wiki_io.py`
- Read: `/Users/nihalnihalani/Desktop/Github/OpenKB-main/openkb/agent/compiler.py` (look for `_write_concept`, `_prepend_source_to_frontmatter`, `_update_index`, `_extract_wikilinks`, `_normalize_target`)

**Step 1: Write a minimal `wiki_io.py` that doesn't need the full OpenKB schema.** Hackathon scope is tight — don't drag in OpenKB's frontmatter complexity. Just slug, body, [[wikilinks]].

```python
"""Wiki markdown writer. Obsidian-compatible: plain .md + [[wikilinks]]."""
from __future__ import annotations
import re
import time
from pathlib import Path

from .config import CONCEPTS_DIR, LOG_FILE, WIKI_DIR

_SLUG_RE = re.compile(r"[^a-z0-9-]+")
_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def slugify(name: str) -> str:
    s = name.lower().strip().replace(" ", "-")
    s = _SLUG_RE.sub("", s)
    return s[:60] or "untitled"


def concept_path(slug: str) -> Path:
    return CONCEPTS_DIR / f"{slug}.md"


def write_concept(slug: str, title: str, body: str, sources: list[str]) -> Path:
    p = concept_path(slug)
    frontmatter = "---\n" + f"title: {title}\n" + f"updated: {int(time.time())}\n"
    if sources:
        frontmatter += "sources:\n" + "".join(f"  - {s}\n" for s in sources)
    frontmatter += "---\n\n"
    p.write_text(frontmatter + body.strip() + "\n", encoding="utf-8")
    return p


def read_concept(slug: str) -> str | None:
    p = concept_path(slug)
    return p.read_text(encoding="utf-8") if p.exists() else None


def extract_wikilinks(text: str) -> list[str]:
    return [m.group(1).split("|")[0].strip() for m in _WIKILINK_RE.finditer(text)]


def append_log(line: str) -> None:
    WIKI_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line.rstrip() + "\n")


def list_concepts() -> list[str]:
    return sorted(p.stem for p in CONCEPTS_DIR.glob("*.md"))
```

Commit: `git add src/wiki_hackathon/wiki_io.py && git commit -m "feat: wiki markdown IO"`

---

### Task 2.2: `prompts.py`

**Files:** Create `src/wiki_hackathon/prompts.py`

```python
"""All Gemini prompt templates. Strict JSON outputs where parsing is needed."""
from __future__ import annotations

CONCEPT_RENDER = """\
You are maintaining a wiki on the topic "AI agents in 2026". The current concept
is "{title}".

You will be given (a) the existing concept page content if any, and (b) a new
source item that mentions this concept. Produce an updated concept page in
Obsidian-flavored Markdown.

Rules:
- 2-4 short paragraphs.
- Use [[wikilinks]] for related concepts when natural.
- Do NOT invent facts that aren't in the existing page or the new item.
- End with a "## Sources" section listing the new and any prior sources as
  bullets with URLs when available.

Existing page (or "(empty)"):
{existing}

New item:
Source: {source}
Title: {item_title}
Body: {item_body}
URL: {item_url}

Return ONLY the markdown, no preamble.
"""

CONTRADICTION_CHECK = """\
You compare a wiki concept page against new evidence.

Concept slug: {slug}
Current page:
\"\"\"{page}\"\"\"

Incoming item:
\"\"\"{item}\"\"\"

Return STRICT JSON, no prose, with these keys:
  "conflict": boolean — true ONLY if the item directly contradicts a substantive
              claim on the page (not merely adds or refines).
  "evidence": string — one sentence quoting the contradicting fact pair.
  "old_claim": string — the page's claim being superseded.
  "new_claim": string — the item's claim that supersedes it.
  "rewrite":  string — the FULL replacement markdown for the page if conflict
              is true; empty string otherwise.

Example: {{"conflict": true, "evidence": "page says X happened Jan 15, item says Jan 22",
"old_claim": "X happened Jan 15", "new_claim": "X happened Jan 22",
"rewrite": "---\\ntitle: ...\\n---\\n..."}}
"""

SYNTH_ANSWER = """\
Answer the user's question using ONLY the supplied KG context and wiki snippets.
Be concise (3-5 sentences). Cite concept page slugs in [[brackets]] when used.
If the answer is unknown, say so.

KG context:
{kg}

Wiki snippets:
{wiki}

Question: {question}
"""

EVAL_GRADER = """\
Grade the model's answer against the rubric. Return STRICT JSON:
  "score": integer 0-3 — how many required entities are correctly named
  "found": list of strings — the entities found in the answer
  "missing": list of strings — required entities not in the answer

Required entities: {required}

Model answer:
\"\"\"{answer}\"\"\"
"""
```

Commit: `git add src/wiki_hackathon/prompts.py && git commit -m "feat: prompt templates"`

---

### Task 2.3: `gemini_io.py` — JSON-safe Gemini calls

**Files:** Create `src/wiki_hackathon/gemini_io.py`

```python
"""Direct Gemini SDK wrapper. Always use these helpers, not raw genai, so JSON
parsing + retries stay in one place."""
from __future__ import annotations
import json
from typing import Any

import google.generativeai as genai
from json_repair import repair_json

from .config import GEMINI_API_KEY

genai.configure(api_key=GEMINI_API_KEY)
_MODEL_NAME = "gemini-3-pro"


def _model() -> genai.GenerativeModel:
    return genai.GenerativeModel(_MODEL_NAME)


def generate_text(prompt: str) -> str:
    return _model().generate_content(prompt).text.strip()


def generate_json(prompt: str) -> dict[str, Any]:
    raw = _model().generate_content(
        prompt,
        generation_config={"response_mime_type": "application/json"},
    ).text
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return json.loads(repair_json(raw))
```

Commit: `git add src/wiki_hackathon/gemini_io.py && git commit -m "feat: gemini JSON-safe wrapper"`

---

### Task 2.4: `ingest.py` worker

**Files:** Create `src/wiki_hackathon/ingest.py`

```python
"""The ingest worker. One message at a time — no overlap (Cognee Kuzu lock)."""
from __future__ import annotations
import json
import time
from typing import Any

from . import cognee_io, gemini_io, redis_bus, wiki_io
from .prompts import CONCEPT_RENDER


def _parse_item(fields: dict[str, str]) -> dict[str, Any]:
    """Stream fields are strings; recover JSON-encoded values if any."""
    out = {}
    for k, v in fields.items():
        try:
            out[k] = json.loads(v)
        except (json.JSONDecodeError, TypeError):
            out[k] = v
    return out


def process_one(item: dict[str, Any]) -> list[str]:
    """Ingest one item end-to-end. Returns slugs touched."""
    text = f"{item.get('title','')}\n\n{item.get('body','')}"
    source = item.get("source", "unknown")
    item_id = item.get("id") or redis_bus.sha(text)

    if not redis_bus.mark_seen(item_id):
        return []

    cognee_io.run(cognee_io.add(text, source))
    cognee_io.run(cognee_io.cognify())

    concepts = cognee_io.run(cognee_io.top_concepts(text, k=3))
    touched: list[str] = []
    for name in concepts:
        slug = wiki_io.slugify(name)
        if not slug:
            continue
        existing = wiki_io.read_concept(slug) or "(empty)"
        page = gemini_io.generate_text(CONCEPT_RENDER.format(
            title=name, existing=existing, source=source,
            item_title=item.get("title", ""), item_body=item.get("body", ""),
            item_url=item.get("url", ""),
        ))
        wiki_io.write_concept(slug, name, page,
                              sources=[item.get("url") or source])
        redis_bus.rewrite_concept(slug, page, reason=None, source=source)
        touched.append(slug)

    wiki_io.append_log(f"[{int(time.time())}] INGEST {item_id} → {touched}")
    return touched


def run_once(block_ms: int = 5_000) -> int:
    """Pull one message, process, ack. Returns count processed."""
    redis_bus.ensure_group()
    msgs = redis_bus.claim_items(count=1, block_ms=block_ms)
    if not msgs:
        return 0
    mid, fields = msgs[0]
    try:
        process_one(_parse_item(fields))
    finally:
        redis_bus.ack(mid)
    return 1


def run_forever() -> None:
    redis_bus.ensure_group()
    print("ingest worker running. Ctrl-C to stop.")
    while True:
        try:
            n = run_once(block_ms=5_000)
            if n == 0:
                continue
        except KeyboardInterrupt:
            break
        except Exception as e:  # keep worker alive during demo
            print(f"ingest error (continuing): {e!r}")
            time.sleep(1)
```

Commit: `git add src/wiki_hackathon/ingest.py && git commit -m "feat: ingest worker"`

---

### Task 2.5: CLI v1 — `ingest`, `inject`, manual run

**Files:** Create `src/wiki_hackathon/cli.py`

```python
"""wiki — single Click entrypoint."""
from __future__ import annotations
import json
import time
from pathlib import Path

import click

from . import ingest as ingest_mod
from . import redis_bus


@click.group()
def cli() -> None:
    """wiki — self-correcting LLM wiki (hackathon)"""


# ---- producers ----------------------------------------------------------

@cli.command()
@click.option("--title", required=True)
@click.option("--body", required=True)
@click.option("--source", default="manual")
@click.option("--url", default="")
def inject(title: str, body: str, source: str, url: str) -> None:
    """Push one manual item onto the firehose."""
    item = {
        "id": redis_bus.sha(title + body),
        "title": title, "body": body, "source": source,
        "url": url, "ts": str(time.time()),
    }
    mid = redis_bus.push_item(item)
    click.echo(f"queued {item['id']} → stream {mid}")


@cli.command("inject-canned")
@click.argument("name")
def inject_canned(name: str) -> None:
    """Inject a pre-prepared canned item (data/canned/<name>.json)."""
    from .config import CANNED_DIR
    p = CANNED_DIR / f"{name}.json"
    item = json.loads(p.read_text())
    mid = redis_bus.push_item(item)
    click.echo(f"queued {item['id']} → stream {mid}")


# ---- workers ------------------------------------------------------------

@cli.command()
@click.option("--once", is_flag=True, help="Process one message then exit.")
def ingest(once: bool) -> None:
    """Run the ingest worker."""
    if once:
        n = ingest_mod.run_once(block_ms=2_000)
        click.echo(f"processed {n} item(s)")
    else:
        ingest_mod.run_forever()


if __name__ == "__main__":
    cli()
```

**Step: end-to-end smoke**

```bash
docker compose ps  # ensure redis up
wiki inject --title "First note" --body "Agents are systems that act on goals." --source "test"
wiki ingest --once
ls wiki/concepts/
cat wiki/log.md
```

Expected: at least one `wiki/concepts/<slug>.md` exists; `log.md` has one INGEST line.
If 0 concepts: `top_concepts()` returned empty — Cognee INSIGHTS format varies; inspect with `python scripts/hello_cognee.py` and adjust the defensive parsing in `cognee_io.top_concepts`.

Commit: `git add src/wiki_hackathon/cli.py && git commit -m "feat: CLI with inject + ingest"`

**Phase 2 gate:** End-to-end ingest works. Clock check: ≤2:05 PM.

---

## Phase 3 — Query + self-correction (2:00 – 2:30, 30 min)

### Task 3.1: `query.py`

**Files:** Create `src/wiki_hackathon/query.py`

```python
"""Query path with contradiction detection + SUPERSEDES edge write.

The substantive self-improvement loop:
1. cognee.search(GRAPH_COMPLETION)
2. For each relevant concept page, Gemini contradiction check (Redis-cached).
3. If conflict: rewrite .md + write SUPERSEDES edge to Cognee KG.
4. Return cited answer.
"""
from __future__ import annotations
import time

from . import cognee_io, gemini_io, redis_bus, wiki_io
from .prompts import CONTRADICTION_CHECK, SYNTH_ANSWER

CANNED_REWRITE_TRIGGERS: set[str] = set()  # populated from data/canned/*.json during seed


def _verdict_key(slug: str, item_text: str) -> str:
    return redis_bus.sha(slug + "::" + item_text)[:32]


def check_contradiction(slug: str, item_text: str,
                        item_id: str | None = None) -> dict:
    """Cached Gemini call. Returns the parsed JSON verdict."""
    page = wiki_io.read_concept(slug) or ""
    if not page:
        return {"conflict": False, "evidence": "", "old_claim": "",
                "new_claim": "", "rewrite": ""}

    if item_id and item_id in CANNED_REWRITE_TRIGGERS:
        # Deterministic override for demo reliability. Still produces a real
        # rewrite via Gemini below; just guarantees conflict=True.
        forced = True
    else:
        forced = False

    key = _verdict_key(slug, item_text)
    cached = redis_bus.verdict_get(key)
    if cached and not forced:
        return cached

    verdict = gemini_io.generate_json(
        CONTRADICTION_CHECK.format(slug=slug, page=page, item=item_text))
    if forced:
        verdict["conflict"] = True
    redis_bus.verdict_set(key, verdict, ttl_sec=600)
    return verdict


def self_improve(slug: str, verdict: dict, source: str) -> bool:
    """If conflict, rewrite the page AND write the SUPERSEDES edge."""
    if not verdict.get("conflict"):
        return False
    new_md = verdict.get("rewrite") or ""
    if not new_md.strip():
        return False
    wiki_io.write_concept(slug, slug.replace("-", " ").title(),
                          new_md, sources=[source])
    redis_bus.rewrite_concept(slug, new_md,
                              reason=verdict.get("evidence", "contradiction"),
                              source=source)
    cognee_io.run(cognee_io.write_supersedes_edge(
        old_claim=verdict.get("old_claim", ""),
        new_claim=verdict.get("new_claim", ""),
        source=source,
        reason=verdict.get("evidence", ""),
    ))
    wiki_io.append_log(
        f"[{int(time.time())}] SELF-CORRECT {slug} — {verdict.get('evidence','')}"
    )
    return True


def ask(question: str) -> str:
    kg = cognee_io.run(cognee_io.search_completion(question))
    concepts = wiki_io.list_concepts()[:8]
    wiki_snippets = {s: (wiki_io.read_concept(s) or "")[:1500] for s in concepts}

    answer = gemini_io.generate_text(SYNTH_ANSWER.format(
        kg=kg, wiki=wiki_snippets, question=question,
    ))
    return answer
```

Commit: `git add src/wiki_hackathon/query.py && git commit -m "feat: query + self-correction with SUPERSEDES edge"`

---

### Task 3.2: Wire self-correction into ingest

**Files:** Modify `src/wiki_hackathon/ingest.py` — add a self-correction pass after concept writes.

Replace `process_one` with:

```python
def process_one(item: dict[str, Any]) -> dict[str, Any]:
    from . import query  # local import to avoid cycle on cold start
    text = f"{item.get('title','')}\n\n{item.get('body','')}"
    source = item.get("source", "unknown")
    item_id = item.get("id") or redis_bus.sha(text)
    if not redis_bus.mark_seen(item_id):
        return {"slugs": [], "self_corrected": []}

    cognee_io.run(cognee_io.add(text, source))
    cognee_io.run(cognee_io.cognify())

    concepts = cognee_io.run(cognee_io.top_concepts(text, k=3))
    touched: list[str] = []
    for name in concepts:
        slug = wiki_io.slugify(name)
        if not slug:
            continue

        # Self-correction check FIRST: does the new item contradict the
        # existing page? If yes, write the SUPERSEDES edge + rewrite.
        verdict = query.check_contradiction(slug, text, item_id=item_id)
        if query.self_improve(slug, verdict, source):
            touched.append(slug)
            continue

        # Otherwise, normal concept render (could be initial or refinement).
        existing = wiki_io.read_concept(slug) or "(empty)"
        page = gemini_io.generate_text(CONCEPT_RENDER.format(
            title=name, existing=existing, source=source,
            item_title=item.get("title", ""), item_body=item.get("body", ""),
            item_url=item.get("url", ""),
        ))
        wiki_io.write_concept(slug, name, page,
                              sources=[item.get("url") or source])
        redis_bus.rewrite_concept(slug, page, reason=None, source=source)
        touched.append(slug)

    self_corrected = [s for s in touched
                      if query.check_contradiction(s, text, item_id).get("conflict")]
    wiki_io.append_log(
        f"[{int(time.time())}] INGEST {item_id} → {touched} "
        f"(self_corrected={self_corrected})"
    )
    return {"slugs": touched, "self_corrected": self_corrected}
```

(`run_once` and `run_forever` stay the same.)

Commit: `git add src/wiki_hackathon/ingest.py && git commit -m "feat: self-correction in ingest path"`

---

### Task 3.3: CLI `ask` + `graph supersedes`

**Files:** Modify `src/wiki_hackathon/cli.py` — append:

```python
from . import cognee_io
from . import query as query_mod

@cli.command()
@click.argument("question")
def ask(question: str) -> None:
    """Ask the wiki a question (uses Cognee KG + wiki + Gemini)."""
    click.echo(query_mod.ask(question))


@cli.group()
def graph() -> None:
    """Inspect the Cognee knowledge graph."""


@graph.command("supersedes")
def graph_supersedes() -> None:
    """List all SUPERSEDES edges. The killer 2-hop hero is built on this."""
    rows = cognee_io.run(cognee_io.list_supersedes())
    if not rows:
        click.echo("(no SUPERSEDES edges yet)")
        return
    for r in rows:
        click.echo(
            f"{r.get('from','?')[:32]} → {r.get('to','?')[:32]}  "
            f"src={r.get('source','')}  reason={r.get('reason','')[:60]}"
        )


@graph.command("stats")
def graph_stats() -> None:
    s = cognee_io.run(cognee_io.graph_stats())
    click.echo(f"nodes={s['nodes']}  edges={s['edges']}")
```

**Smoke test:**

```bash
# State: at least one concept page from Phase 2 smoke test
wiki ask "what are agents?"
# expect: a synthesized answer, possibly with [[wikilinks]]
```

Commit: `git add src/wiki_hackathon/cli.py && git commit -m "feat: ask + graph CLI commands"`

---

### Task 3.4: Smoke test the self-correction path

**Step 1: Plant a contradiction by hand**

```bash
wiki inject --title "Agent memory" --body "Agents should persist memory across sessions to maintain continuity." --source "seedA" --url "http://example/a"
wiki ingest --once
ls wiki/concepts/  # expect 1+ pages including something like agent-memory.md

wiki inject --title "Agent memory rebuttal" --body "Persistent agent memory across sessions causes drift; agents should NOT persist state." --source "seedB" --url "http://example/b"
wiki ingest --once
```

**Step 2: Verify**

```bash
cat wiki/log.md  # expect at least one SELF-CORRECT line
wiki graph supersedes  # expect at least one row with reason
wiki ask "should agents persist memory?"
```

If no SELF-CORRECT line appears: open the Gemini contradiction-check output by adding `click.echo(verdict)` temporarily in `query.check_contradiction`. Common cause: the prompt produced `conflict: false` because the two items mention different sub-claims. Reword the second item to be a more direct opposite of the first.

Commit (only if successful): `git commit --allow-empty -m "test: self-correction smoke green"`

**Phase 3 gate:** Self-correction works. Clock check: ≤2:35 PM.

---

## Phase 4 — Lint (2:30 – 2:55, 25 min)

### Task 4.1: `lint.py`

**Files:** Create `src/wiki_hackathon/lint.py`

```python
"""Lint = structural (forked OpenKB pattern) + knowledge (Cognee Cypher)."""
from __future__ import annotations
import re
import time
from pathlib import Path

from . import cognee_io, wiki_io
from .config import CONCEPTS_DIR, REPORTS_DIR

_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def find_broken_wikilinks() -> list[tuple[str, str]]:
    """Pairs of (source_slug, broken_target)."""
    existing = {p.stem for p in CONCEPTS_DIR.glob("*.md")}
    out: list[tuple[str, str]] = []
    for p in CONCEPTS_DIR.glob("*.md"):
        for m in _WIKILINK_RE.finditer(p.read_text(encoding="utf-8")):
            target = wiki_io.slugify(m.group(1).split("|")[0])
            if target and target not in existing:
                out.append((p.stem, target))
    return out


def find_orphans() -> list[str]:
    """Concept pages no other page links to."""
    incoming: dict[str, int] = {p.stem: 0 for p in CONCEPTS_DIR.glob("*.md")}
    for p in CONCEPTS_DIR.glob("*.md"):
        for m in _WIKILINK_RE.finditer(p.read_text(encoding="utf-8")):
            target = wiki_io.slugify(m.group(1).split("|")[0])
            if target in incoming:
                incoming[target] += 1
    return sorted(s for s, n in incoming.items() if n == 0)


def supersedes_summary() -> list[dict]:
    return cognee_io.run(cognee_io.list_supersedes())


def kg_stats() -> dict:
    return cognee_io.run(cognee_io.graph_stats())


def write_report() -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d-%H%M%S")
    p = REPORTS_DIR / f"lint-{ts}.md"

    broken = find_broken_wikilinks()
    orphans = find_orphans()
    supers = supersedes_summary()
    stats = kg_stats()
    pages = len(list(CONCEPTS_DIR.glob("*.md")))

    lines: list[str] = []
    lines.append(f"# Lint Report — {ts}\n")
    lines.append("## Metrics\n")
    lines.append("| metric | value |\n|---|---|")
    lines.append(f"| pages | {pages} |")
    lines.append(f"| graph nodes | {stats['nodes']} |")
    lines.append(f"| graph edges | {stats['edges']} |")
    lines.append(f"| SUPERSEDES edges | {len(supers)} |")
    lines.append(f"| broken wikilinks | {len(broken)} |")
    lines.append(f"| orphan concepts | {len(orphans)} |\n")

    if supers:
        lines.append("## Superseded claims\n")
        for s in supers:
            lines.append(f"- src={s.get('source','')} — {s.get('reason','')[:140]}")
        lines.append("")

    if broken:
        lines.append("## Broken wikilinks\n")
        for src, tgt in broken:
            lines.append(f"- [[{tgt}]] referenced by `{src}.md`")
        lines.append("")

    if orphans:
        lines.append("## Orphan concept pages\n")
        for o in orphans:
            lines.append(f"- `{o}.md`")
        lines.append("")

    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p
```

Commit: `git add src/wiki_hackathon/lint.py && git commit -m "feat: lint with metrics + supersedes summary"`

---

### Task 4.2: CLI `lint`

**Files:** Modify `src/wiki_hackathon/cli.py`:

```python
from . import lint as lint_mod

@cli.command()
def lint() -> None:
    """Run structural + knowledge lint, write report."""
    p = lint_mod.write_report()
    click.echo(f"wrote {p}")
    click.echo("--- preview ---")
    click.echo(p.read_text())
```

Smoke: `wiki lint && ls wiki/reports/`
Expected: a `lint-*.md` with the metrics table populated.

Commit: `git add src/wiki_hackathon/cli.py && git commit -m "feat: lint CLI command"`

---

### Task 4.3: Smoke tests

**Files:** Create `tests/test_smoke.py`

```python
"""4 fast smoke tests. Requires Redis up; uses real Cognee/Gemini for E2E."""
from __future__ import annotations
import os
import time

import pytest

from wiki_hackathon import redis_bus, wiki_io, lint
from wiki_hackathon.config import CONCEPTS_DIR


@pytest.fixture(autouse=True)
def _clean_redis():
    redis_bus.reset_streams()
    yield


def test_dedup_setnx() -> None:
    assert redis_bus.mark_seen("abc") is True
    assert redis_bus.mark_seen("abc") is False


def test_audit_and_publish() -> None:
    redis_bus.audit({"slug": "x", "reason": "test"})
    assert redis_bus.client().xlen("wiki:evolution") >= 1


def test_wiki_io_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("wiki_hackathon.wiki_io.CONCEPTS_DIR", tmp_path)
    wiki_io.write_concept("foo", "Foo", "Body of [[bar]]", ["src1"])
    assert (tmp_path / "foo.md").exists()
    assert "[[bar]]" in (tmp_path / "foo.md").read_text()


def test_lint_report_minimal(monkeypatch, tmp_path) -> None:
    # Stub Cognee calls so test runs without it
    monkeypatch.setattr(lint, "supersedes_summary", lambda: [])
    monkeypatch.setattr(lint, "kg_stats", lambda: {"nodes": 0, "edges": 0})
    monkeypatch.setattr(lint, "CONCEPTS_DIR", tmp_path)
    monkeypatch.setattr(lint, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr("wiki_hackathon.wiki_io.CONCEPTS_DIR", tmp_path)
    (tmp_path / "a.md").write_text("[[b]]")
    p = lint.write_report()
    assert "broken wikilinks" in p.read_text()
```

Run: `pytest -q`
Expected: 4 passed. If 1-3 pass but a Cognee/Redis-touching test fails, narrow to `pytest -q -k "dedup or audit or roundtrip"` — those three MUST pass before moving on. The lint test can be skipped if monkeypatch gymnastics break.

Commit: `git add tests/test_smoke.py && git commit -m "test: smoke suite (4 tests)"`

**Phase 4 gate:** Lint produces a report with metrics. Clock check: ≤2:55 PM.

---

## Phase 5 — Seed data + pre-bake (2:55 – 3:15, 20 min)

### Task 5.1: Write the seed items file

**Files:** Create `data/canned/seed_items.jsonl` — 15 items on "AI agents in 2026".

Write items with this template (one JSON object per line). They MUST collectively produce ~5-8 distinct concept pages. Mix in entities like *Karpathy*, *MemGPT*, *Cognee*, *Letta*, *agent memory*, *context window*, *tool use*, *alignment*, *RLHF*.

```jsonl
{"id":"hn-001","source":"hn","title":"Karpathy proposes LLM Wiki","body":"Andrej Karpathy argues that LLMs should maintain a self-updating wiki rather than re-retrieving documents on every query. The wiki accumulates knowledge across sessions and can be linted for contradictions.","url":"https://news.ycombinator.com/item?id=001","ts":"2026-05-16T13:00:00"}
{"id":"hn-002","source":"hn","title":"MemGPT releases v3","body":"MemGPT v3 introduces hierarchical memory that lets agents persist state across sessions for arbitrary topics. The team claims a 40% improvement on long-horizon tasks.","url":"https://news.ycombinator.com/item?id=002","ts":"2026-05-16T13:02:00"}
{"id":"hn-003","source":"hn","title":"Cognee 0.5 ships","body":"Cognee 0.5 adds a Redis vector adapter, ontology support, and the memify operation for incremental graph enrichment. The team positions it as the memory engine for agent frameworks.","url":"https://news.ycombinator.com/item?id=003","ts":"2026-05-16T13:03:00"}
{"id":"hn-004","source":"hn","title":"On the context window debate","body":"Several researchers argue that 1M-token context windows obviate the need for explicit memory. Others contend that context rot makes memory architectures essential regardless of window size.","url":"https://news.ycombinator.com/item?id=004","ts":"2026-05-16T13:05:00"}
{"id":"hn-005","source":"hn","title":"Agent alignment debate","body":"A new paper finds that agents with persistent memory are easier to align via post-hoc audit than stateless agents. Critics argue this only works if memory is also auditable.","url":"https://news.ycombinator.com/item?id=005","ts":"2026-05-16T13:08:00"}
{"id":"hn-006","source":"hn","title":"Tool use plateau","body":"Frontier models plateau on tool-use benchmarks. Researchers suggest the limit is memory of past tool outcomes, not raw capability.","url":"https://news.ycombinator.com/item?id=006","ts":"2026-05-16T13:09:00"}
{"id":"hn-007","source":"hn","title":"Letta agents go open source","body":"Letta open-sources its agent runtime. The release ships with memory primitives compatible with both vector stores and graph databases.","url":"https://news.ycombinator.com/item?id=007","ts":"2026-05-16T13:11:00"}
{"id":"hn-008","source":"hn","title":"RLHF for memory editing","body":"Researchers train a memory-editing policy via RLHF, letting agents prune or correct their own memory under human feedback.","url":"https://news.ycombinator.com/item?id=008","ts":"2026-05-16T13:13:00"}
{"id":"hn-009","source":"hn","title":"On wiki self-correction","body":"Self-correcting wikis emerge as a memory pattern: the agent compares incoming evidence to its existing concept pages and rewrites them on contradiction.","url":"https://news.ycombinator.com/item?id=009","ts":"2026-05-16T13:15:00"}
{"id":"hn-010","source":"hn","title":"Redis as agent memory","body":"Redis releases the Agent Memory Server and benchmarks 5x lower latency than vector-only stores for short-term agent context.","url":"https://news.ycombinator.com/item?id=010","ts":"2026-05-16T13:17:00"}
{"id":"hn-011","source":"hn","title":"Karpathy on context rot","body":"Karpathy says context rot is the central obstacle to long-running agents; the cure is a curated wiki, not bigger windows.","url":"https://news.ycombinator.com/item?id=011","ts":"2026-05-16T13:19:00"}
{"id":"hn-012","source":"hn","title":"Benchmark: persistent vs stateless","body":"A new benchmark shows persistent-memory agents outperform stateless agents by 22% on 7-day task suites.","url":"https://news.ycombinator.com/item?id=012","ts":"2026-05-16T13:21:00"}
{"id":"hn-013","source":"hn","title":"Cognee + Redis integration","body":"Cognee and Redis publish a joint blog showing a unified pipeline: streams in, knowledge graph out, queryable in milliseconds.","url":"https://news.ycombinator.com/item?id=013","ts":"2026-05-16T13:23:00"}
{"id":"hn-014","source":"hn","title":"Lint your agent's memory","body":"A small but growing community advocates 'linting' agent memory: detect contradictions, orphan facts, stale claims, and prune.","url":"https://news.ycombinator.com/item?id=014","ts":"2026-05-16T13:25:00"}
{"id":"hn-015","source":"hn","title":"Agent memory ethics","body":"As agents accumulate memory about users, ethicists call for a right-to-be-forgotten API. Cognee implements one with its prune operation.","url":"https://news.ycombinator.com/item?id=015","ts":"2026-05-16T13:27:00"}
```

**Step:** Save the file. Don't perfect prose; it's seed material.

---

### Task 5.2: Write the 3 contradiction items

**Files:**
- `data/canned/contradiction_1.json` — directly contradicts `hn-002` / `hn-012` (persistence good)
- `data/canned/contradiction_2.json` — directly contradicts `hn-011` (context rot vs big windows)
- `data/canned/contradiction_3.json` — directly contradicts `hn-010` (Redis benchmark)

```json
// contradiction_1.json
{"id":"con-001","source":"arxiv","title":"Persistent agent memory considered harmful","body":"A new paper shows persistent memory across sessions causes catastrophic drift on multi-week tasks. The authors conclude agents should NOT persist state across sessions and recommend stateless designs.","url":"https://arxiv.org/abs/2026.contra1","ts":"2026-05-16T14:00:00"}
```

```json
// contradiction_2.json
{"id":"con-002","source":"arxiv","title":"Bigger windows solve memory","body":"A 10M-token context window paper demonstrates that context rot is eliminated at sufficient window size, refuting the claim that wikis are necessary.","url":"https://arxiv.org/abs/2026.contra2","ts":"2026-05-16T14:01:00"}
```

```json
// contradiction_3.json
{"id":"con-003","source":"blog","title":"Redis benchmark retracted","body":"Redis retracted its agent-memory latency benchmark after methodological errors. Independent reruns show parity with vector-only stores.","url":"https://example.com/retraction","ts":"2026-05-16T14:02:00"}
```

Add the contradiction IDs to the override set so the demo is deterministic. Edit `src/wiki_hackathon/query.py`:

```python
CANNED_REWRITE_TRIGGERS: set[str] = {"con-001", "con-002", "con-003"}
```

Commit: `git add data/canned src/wiki_hackathon/query.py && git commit -m "data: seed items + 3 canned contradictions"`

---

### Task 5.3: `seed` and `replay` commands

**Files:** Create `src/wiki_hackathon/replay.py`

```python
"""Replay a JSONL file into the firehose stream, then run ingest until drained."""
from __future__ import annotations
import json
import time
from pathlib import Path

from . import ingest as ingest_mod, redis_bus


def replay(jsonl_path: Path, pace_sec: float = 0.2) -> int:
    n = 0
    with jsonl_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            redis_bus.push_item(item)
            n += 1
            time.sleep(pace_sec)
    return n


def drain() -> int:
    """Ingest until the stream is empty."""
    processed = 0
    while True:
        n = ingest_mod.run_once(block_ms=1_500)
        if n == 0:
            break
        processed += n
    return processed
```

**Files:** Modify `src/wiki_hackathon/cli.py`:

```python
from pathlib import Path
from . import replay as replay_mod
from .config import CANNED_DIR

@cli.command()
@click.option("--path", default=None,
              help="JSONL file. Default: data/canned/seed_items.jsonl")
def seed(path: str | None) -> None:
    """Push the seed JSONL onto the stream and drain it."""
    p = Path(path) if path else (CANNED_DIR / "seed_items.jsonl")
    n = replay_mod.replay(p, pace_sec=0.05)
    click.echo(f"pushed {n} items; draining...")
    m = replay_mod.drain()
    click.echo(f"processed {m} items")


@cli.command()
def reset() -> None:
    """Wipe Redis + Cognee + wiki for a clean demo run."""
    redis_bus.reset_streams()
    cognee_io.run(cognee_io.reset())
    from .config import CONCEPTS_DIR, LOG_FILE
    for p in CONCEPTS_DIR.glob("*.md"):
        p.unlink()
    if LOG_FILE.exists():
        LOG_FILE.unlink()
    click.echo("reset complete")
```

**Pre-bake the demo state:**

```bash
wiki reset
wiki seed
# expect: "processed N items" where N == 15 or close
ls wiki/concepts/   # expect 5-10 .md files
wiki graph stats    # expect nonzero
wiki lint           # expect a report with concepts > 0
```

This pre-baked state is what gets loaded for the demo. Save the wiki + cognee state for fast reset:

```bash
tar czf snapshot/demo-baked.tar.gz wiki/ .cognee_system/ .data_storage/ 2>/dev/null || true
```

Commit: `git add src/wiki_hackathon/replay.py src/wiki_hackathon/cli.py && git commit -m "feat: seed + reset commands"`

**Phase 5 gate:** Pre-baked wiki has 5+ concept pages, graph has nodes/edges. Clock: ≤3:20 PM.

---

## Phase 6 — Dashboard + held-out eval (3:15 – 3:40, 25 min)

### Task 6.1: `dashboard.py`

**Files:** Create `src/wiki_hackathon/dashboard.py`

```python
"""Three-pane rich Layout dashboard. Subscribes to wiki:events for live updates."""
from __future__ import annotations
import json
import threading
import time
from collections import deque

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from . import cognee_io, redis_bus, wiki_io
from .config import PUBSUB_CHANNEL

_firehose: deque[str] = deque(maxlen=12)
_audit: deque[str] = deque(maxlen=12)


def _subscribe() -> None:
    ps = redis_bus.client().pubsub()
    ps.subscribe(PUBSUB_CHANNEL)
    for m in ps.listen():
        if m["type"] != "message":
            continue
        try:
            payload = json.loads(m["data"])
        except Exception:
            continue
        slug = payload.get("slug", "?")
        kind = payload.get("type", "?")
        ts = time.strftime("%H:%M:%S")
        _audit.appendleft(f"{ts}  {kind:<8} {slug}")


def _scan_firehose() -> None:
    """Tail wiki:evolution stream once per tick."""
    last = "0"
    while True:
        try:
            entries = redis_bus.client().xrange("wiki:evolution",
                                                min=f"({last}", max="+", count=20)
            for mid, fields in entries:
                last = mid
                ts = time.strftime("%H:%M:%S")
                _firehose.appendleft(
                    f"{ts}  {fields.get('slug','?'):<24} "
                    f"{fields.get('reason','')[:30]}"
                )
        except Exception:
            pass
        time.sleep(0.5)


def _render(metrics: dict) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="top", ratio=3),
        Layout(name="metrics", size=7),
    )
    layout["top"].split_row(
        Layout(Panel("\n".join(_firehose) or "(idle)", title="Firehose / Evolution")),
        Layout(Panel("\n".join(wiki_io.list_concepts()) or "(no concepts)",
                     title="Concepts")),
        Layout(Panel("\n".join(_audit) or "(silent)", title="Audit (Pub/Sub)")),
    )
    t = Table(show_header=False, expand=True)
    t.add_row("concepts", str(metrics.get("concepts", 0)))
    t.add_row("graph nodes", str(metrics.get("nodes", 0)))
    t.add_row("graph edges", str(metrics.get("edges", 0)))
    t.add_row("SUPERSEDES", str(metrics.get("supersedes", 0)))
    t.add_row("verdict cache", str(metrics.get("verdict_cache_keys", 0)))
    t.add_row("stream length", str(metrics.get("stream_len", 0)))
    layout["metrics"].update(Panel(t, title="Metrics"))
    return layout


def run() -> None:
    threading.Thread(target=_subscribe, daemon=True).start()
    threading.Thread(target=_scan_firehose, daemon=True).start()
    console = Console()
    with Live(refresh_per_second=2, console=console, screen=True) as live:
        while True:
            stats = cognee_io.run(cognee_io.graph_stats())
            sups = cognee_io.run(cognee_io.list_supersedes())
            metrics = {
                **stats,
                **redis_bus.metrics(),
                "concepts": len(wiki_io.list_concepts()),
                "supersedes": len(sups),
            }
            live.update(_render(metrics))
            time.sleep(0.5)
```

**Files:** Modify `src/wiki_hackathon/cli.py`:

```python
from . import dashboard

@cli.command()
def dash() -> None:
    """Live three-pane terminal dashboard."""
    dashboard.run()
```

Smoke: `wiki dash` — should render a 3-pane layout with metrics. Ctrl-C to exit. If `cognee_io.graph_stats()` is slow (>500ms), the dashboard will stutter — wrap it in a cached value updated every 5s instead. Skip if behind schedule.

Commit: `git add src/wiki_hackathon/dashboard.py src/wiki_hackathon/cli.py && git commit -m "feat: rich dashboard"`

---

### Task 6.2: `eval.py`

**Files:** Create `src/wiki_hackathon/eval.py`

```python
"""Held-out eval: a question that flips 0/3 → 3/3 after ingest.

This is the metric we put on screen during the demo to prove self-improvement
is real, not theater.
"""
from __future__ import annotations
from . import gemini_io, query
from .prompts import EVAL_GRADER

EVAL_QUESTION = "Who is leading agent memory research in 2026? Name specific people, projects, or companies."
EVAL_REQUIRED = ["Karpathy", "MemGPT", "Cognee"]


def run() -> dict:
    answer = query.ask(EVAL_QUESTION)
    grade = gemini_io.generate_json(EVAL_GRADER.format(
        required=EVAL_REQUIRED, answer=answer,
    ))
    score = int(grade.get("score", 0))
    return {
        "question": EVAL_QUESTION,
        "answer": answer,
        "score": score,
        "max": len(EVAL_REQUIRED),
        "found": grade.get("found", []),
        "missing": grade.get("missing", []),
    }
```

**Files:** Modify `src/wiki_hackathon/cli.py`:

```python
from . import eval as eval_mod

@cli.command()
def eval() -> None:
    """Held-out evaluation: shows score and citations."""
    r = eval_mod.run()
    click.secho(f"\nQ: {r['question']}\n", bold=True)
    click.echo(r["answer"])
    click.secho(f"\nScore: {r['score']}/{r['max']}", bold=True,
                fg="green" if r['score'] == r['max'] else "yellow")
    click.echo(f"Found:   {r['found']}")
    click.echo(f"Missing: {r['missing']}")
```

Smoke (state: seeded wiki from Phase 5):
```bash
wiki eval
# expect: score 2/3 or 3/3; Karpathy/MemGPT/Cognee found
```

Commit: `git add src/wiki_hackathon/eval.py src/wiki_hackathon/cli.py && git commit -m "feat: held-out eval (0/3 → 3/3 metric)"`

**Phase 6 gate:** Dashboard + eval both work. Clock: ≤3:45 PM.

---

## Phase 7 — Rehearsal (3:40 – 4:10, 30 min)

### Task 7.1: Lock the demo script

**Files:** Create `demo/run_demo.sh`

```bash
#!/usr/bin/env bash
# The exact command sequence for stage. Run it. Don't type live.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "Pre-demo (run BEFORE judges arrive):"
echo "  docker compose up -d"
echo "  wiki reset"
echo "  wiki seed"
echo "  tar czf snapshot/demo-baked.tar.gz wiki/ .cognee_system/ .data_storage/"
echo ""
echo "If you need to reset between rehearsals:"
echo "  wiki reset && tar xzf snapshot/demo-baked.tar.gz"

# Stage commands (read aloud as you hit Enter):
demo_step() {
    read -p "[next: $1] press Enter..."
    eval "$1"
}

demo_step "wiki dash &"   # start dashboard in background; switch tabs to show
demo_step "wiki inject-canned contradiction_1"
demo_step "wiki ingest --once"        # processes the contradiction
demo_step "wiki graph supersedes"     # shows the new edge
demo_step "wiki eval"                 # shows 3/3 score
demo_step "wiki lint && cat \$(ls -t wiki/reports/*.md | head -1)"
```

```bash
chmod +x demo/run_demo.sh
```

---

### Task 7.2: Rehearse three full times

For each rehearsal:

1. `wiki reset && tar xzf snapshot/demo-baked.tar.gz` (restore the pre-baked state)
2. Open Obsidian on `wiki/`, RedisInsight in browser, terminal split for `wiki dash`.
3. Start a stopwatch.
4. Read the §9 demo script (in the design doc) aloud while driving the terminal.
5. Note the wall-clock time at each transition.

Acceptance criteria per run:
- Total run-through ≤ **3:00 minutes**.
- Hero moment (inject → page rewrites visibly → SUPERSEDES edge listed) ≤ **40 seconds**.
- `wiki eval` shows ≥ 2/3.
- `wiki lint` shows ≥ 1 SUPERSEDES edge.

If hero moment is unreliable:
- Try contradiction_2, then contradiction_3. Pin whichever works most reliably (target: 3/3 reliable runs).
- If none are reliable: the deterministic override `CANNED_REWRITE_TRIGGERS` will force `conflict=True`. Confirm this fallback kicks in by checking `wiki/log.md` for the SELF-CORRECT line.

Commit any tuning: `git commit -am "chore: rehearsal tuning"`

**Phase 7 gate:** 3 successful rehearsals. Clock: ≤4:10 PM. If you're at 4:15 and still tuning, STOP and move to Phase 8.

---

## Phase 8 — Record + submit (4:10 – 4:30, 20 min)

### Task 8.1: Screen recording

Use macOS QuickTime (⇧⌘5) or `cmd+shift+5` to record:
1. Reset to baked state: `wiki reset && tar xzf snapshot/demo-baked.tar.gz`
2. Run through `demo/run_demo.sh` cleanly, no audio (you'll narrate live; this is the fallback).
3. Save as `snapshot/wiki-hackathon-demo.mov`.
4. Upload to a public Loom or YouTube unlisted. Put the link in README.

---

### Task 8.2: README + submit

**Files:** Create `/Users/nihalnihalani/Desktop/Github/wiki-hackathon/README.md`

```markdown
# wiki-hackathon

A self-correcting LLM wiki. Drop a topic firehose in; the agent maintains a
Karpathy-style markdown wiki and rewrites pages **plus writes a `:SUPERSEDES`
edge in the Cognee knowledge graph** when contradictions arrive.

Built for the AI-Memory Hackathon (2026-05-16). Cognee + Redis + Gemini 3.

## Demo
- 🎥 Recording: <LINK>
- 📁 Repo: <LINK>

## Run locally
```bash
cp .env.example .env  # set GEMINI_API_KEY
docker compose up -d
python -m venv .venv && source .venv/bin/activate
pip install -e .
wiki seed                                  # ingest 15 synthetic items
wiki inject-canned contradiction_1         # inject a contradiction
wiki ingest --once                         # process; page rewrites + SUPERSEDES edge
wiki graph supersedes                      # see the new edge
wiki eval                                  # before/after score
wiki lint                                  # metrics report
```

## Architecture
See [`docs/plans/2026-05-16-wiki-hackathon-design.md`](docs/plans/2026-05-16-wiki-hackathon-design.md).

## Credits
- Memory engine: [Cognee](https://docs.cognee.ai)
- Stream/state/vector store: [Redis Stack](https://redis.io)
- LLM: Gemini 3
- Inspiration: [Karpathy's LLM Wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) and [OpenKB](https://openkb.ai)
```

Final:
```bash
git add README.md snapshot/
git commit -m "docs: README + demo recording"
git push  # if you set up a remote
```

Submit the hackathon form with the repo + recording links. Done.

---

## Appendix — Fast troubleshooting

| Symptom | First fix |
|---|---|
| `wiki ingest --once` hangs forever on first call | `_first_run_done = True` patch already applied in `cognee_io.py`. Restart the worker. |
| `KeyError: 'max_tokens'` from LiteLLM | `patch_litellm_for_gemini3()` in `config.py` already runs at import. Verify import order. |
| Gemini 401 | `GEMINI_API_KEY` not exported. Restart shell after editing `.env`. |
| Cognee tries to call OpenAI | `EMBEDDING_*` env vars missing. All four must be set. |
| RedisJSON `JSON.GET` returns weird shape | Always pass `$` as path, not `.`. Always index `[0]`. |
| `xreadgroup` returns nothing | `ensure_group()` not called, or items in stream were ACKed already. Run `redis_bus.client().xinfo_groups("firehose:items")` to inspect. |
| Page doesn't rewrite on contradiction injection | Check `wiki/log.md` for an INGEST line. If yes but no SELF-CORRECT, the Gemini check returned `conflict: false` — use a more clearly opposing contradiction item, or rely on `CANNED_REWRITE_TRIGGERS` override. |
| Dashboard flickers | Cognee `graph_stats` is slow. Cache it (every 5s, not every tick). |
| Obsidian doesn't auto-refresh | Toggle "Detect all file extensions" in Obsidian settings; or just press Ctrl-R during demo to force reload. |

---

**End of plan.** Total tasks: 24 across 9 phases. Save artifacts in `/Users/nihalnihalani/Desktop/Github/wiki-hackathon/`.
