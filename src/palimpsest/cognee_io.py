"""Thin async wrapper over Cognee. All cognee calls live here so swaps stay local."""
from __future__ import annotations
import asyncio
import os
from typing import Any

from . import config  # noqa: F401 — load .env + patch litellm
from .logs import get_logger, event

logger = get_logger(__name__)

# Defensive top-level imports: a broken cognee install (e.g., transitive dep
# breakage like mistralai 2.x) shouldn't crash the entire CLI. We capture the
# import error and fail loudly only when a function that actually needs cognee
# is called.
COGNEE_AVAILABLE = True
_COGNEE_IMPORT_ERROR: Exception | None = None
try:
    import cognee
    from cognee.api.v1.search import SearchType
    try:
        import cognee.modules.pipelines.layers.setup_and_check_environment as _env
        _env._first_run_done = True
    except Exception:
        pass
    from cognee.infrastructure.databases.graph import get_graph_engine
except Exception as _e:  # noqa: BLE001
    COGNEE_AVAILABLE = False
    _COGNEE_IMPORT_ERROR = _e
    cognee = None  # type: ignore[assignment]
    SearchType = None  # type: ignore[assignment]
    get_graph_engine = None  # type: ignore[assignment]
    logger.error(f"cognee import failed at startup: {type(_e).__name__}: {_e}")


def _require_cognee() -> None:
    if not COGNEE_AVAILABLE:
        raise RuntimeError(
            f"cognee is not importable: {type(_COGNEE_IMPORT_ERROR).__name__}: "
            f"{_COGNEE_IMPORT_ERROR}. Run `pip install -e .` to repair the venv."
        )

DATASET = "wiki"


# ---- Cognee Cloud bootstrap ------------------------------------------------
# When COGNEE_SERVICE_URL is set, cognee.serve() redirects all V2 operations
# (remember/recall/improve/forget/visualize) to the managed instance instead
# of running locally. V1 operations (add/cognify/search) may still need the
# local install — cognee's behavior on hybrid mode isn't formally documented.
_cloud_initialized = False


async def _init_cloud_if_configured() -> None:
    """Idempotent: call cognee.serve(url, api_key) on first run if env vars are
    set. Safe to call from every run() — short-circuits after the first call."""
    global _cloud_initialized
    if _cloud_initialized:
        return
    url = (os.environ.get("COGNEE_SERVICE_URL") or "").strip()
    if not url:
        _cloud_initialized = True  # local mode locked in
        return
    if not COGNEE_AVAILABLE:
        return
    key = (os.environ.get("COGNEE_API_KEY") or "").strip() or None
    event(logger, "cognee.cloud.connecting", url=url[:80])
    await cognee.serve(url=url, api_key=key)
    event(logger, "cognee.cloud.connected", url=url[:80])
    _cloud_initialized = True


def is_cloud_mode() -> bool:
    return bool((os.environ.get("COGNEE_SERVICE_URL") or "").strip())


async def add(text: str, source: str) -> None:
    """Cognee V2: remember() replaces V1 add+cognify in one call. Routes to
    Cognee Cloud automatically when COGNEE_SERVICE_URL is set (see
    _init_cloud_if_configured)."""
    event(logger, "cognee.remember.start", chars=len(text), source=source)
    # V2 remember does ingest+cognify in one call. The V1 node_set=[f"source:..."]
    # tagging is no longer a public param — source is preserved via the wiki's
    # own source-tracking (wiki_io.write_concept stores sources in frontmatter)
    # and via the SUPERSEDES edge properties when contradictions fire.
    await cognee.remember(text, dataset_name=DATASET)
    event(logger, "cognee.remember.done", source=source)


async def cognify() -> None:
    """No-op in V2: cognee.remember() already does the cognify step. Kept as a
    public function so existing callers in ingest.py keep working unchanged."""
    event(logger, "cognee.cognify.skipped_v2", reason="remember does both")


def _extract_answer_text(entries: Any) -> str:
    """Pull a single answer string out of cognee.recall()'s heterogeneous
    response list. Each entry can be ResponseQAEntry (.answer), ResponseGraphEntry
    (.text), ResponseGraphContextEntry (.content), or ResponseAgentTraceEntry
    (skip). Concatenates QA answers + graph text in order."""
    if not entries:
        return ""
    parts: list[str] = []
    for e in entries:
        for attr in ("answer", "text", "content"):
            v = getattr(e, attr, None)
            if isinstance(v, str) and v.strip():
                parts.append(v.strip())
                break
    if not parts:
        # Last-ditch: stringify each entry
        parts = [str(e) for e in entries if e is not None]
    return "\n\n".join(parts)


async def search_completion(query: str) -> str:
    """Cognee V2: recall() with GRAPH_COMPLETION returns a list of typed
    responses; we extract the answer text and return the concatenation. Routes
    to Cognee Cloud automatically when configured."""
    event(logger, "cognee.recall.start", chars=len(query),
          query_type="GRAPH_COMPLETION")
    entries = await cognee.recall(
        query_text=query,
        query_type=SearchType.GRAPH_COMPLETION,
        datasets=[DATASET],
    )
    text = _extract_answer_text(entries)
    event(logger, "cognee.recall.done", chars=len(text), n_entries=len(entries or []))
    return text


async def search_insights(query: str) -> list[Any]:
    """Cognee V2: recall() with TRIPLET_COMPLETION. Returns the raw response
    list -- callers (top_concepts) defensively parse triples/answer text.

    On a fresh cognee install / cloud tenant, TRIPLET_COMPLETION can raise
    NoDataError until the create_triplet_embeddings memify pipeline has run.
    We catch that here and return [] so top_concepts falls back cleanly to
    Gemini-direct concept extraction (see ingest.py)."""
    try:
        return await cognee.recall(
            query_text=query,
            query_type=SearchType.TRIPLET_COMPLETION,
            datasets=[DATASET],
        )
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        # All "no data yet" shapes -> empty result so callers fall back cleanly:
        # - NoDataError on fresh local graph
        # - "triplet_embeddings" memify pipeline not run yet
        # - Cognee Cloud's 404 "Recall prerequisites not met" before first cognify
        forgiving_signals = (
            "NoDataError", "triplet_embeddings", "TRIPLET_COMPLETION",
            "Recall prerequisites", "Remote recall failed (404)",
        )
        if any(s in msg or s in type(e).__name__ for s in forgiving_signals):
            event(logger, "cognee.recall.no_data_yet", reason=msg[:120])
            return []
        raise


async def top_concepts(item_text: str, k: int = 3) -> list[str]:
    """Pull top entity labels for an item. Used to decide which concept pages to
    touch. V2 recall() returns ResponseQAEntry/ResponseGraphEntry objects;
    triplet structure may live in .structured (ResponseGraphEntry) or be
    embedded in answer text. If extraction yields nothing, the caller falls
    back to a Gemini-direct extractor."""
    entries = await search_insights(item_text)
    seen: dict[str, int] = {}

    def _bump(name: Any) -> None:
        if isinstance(name, str) and name.strip():
            n = name.strip()
            seen[n] = seen.get(n, 0) + 1

    for entry in entries or []:
        # ResponseGraphEntry: .structured can hold parsed triplets
        structured = getattr(entry, "structured", None)
        if isinstance(structured, list):
            for triple in structured:
                if isinstance(triple, dict):
                    for key in ("subject", "object", "name", "label"):
                        _bump(triple.get(key))
                elif isinstance(triple, (list, tuple)) and len(triple) >= 3:
                    _bump(triple[0])
                    _bump(triple[2])
        # Legacy dict/tuple shapes (kept defensively)
        if isinstance(entry, dict):
            for key in ("subject", "object", "name", "label"):
                _bump(entry.get(key))
        elif isinstance(entry, (list, tuple)) and len(entry) >= 3:
            _bump(entry[0])
            _bump(entry[2])
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
    event(logger, "cognee.add_edge.SUPERSEDES",
          old=old_id, new=new_id, reason=reason[:40])


async def list_supersedes() -> list[dict]:
    """For the on-stage `wiki graph supersedes` command.

    Cognee's get_graph_data edge tuple shape varies by version; handle both
    4-tuple (src, dst, rel, props) and 3-tuple (src, dst, props_with_rel).
    """
    graph = await get_graph_engine()
    _, edges = await graph.get_graph_data()
    out: list[dict] = []
    for edge in edges or []:
        if isinstance(edge, (list, tuple)):
            if len(edge) == 4:
                src, dst, rel, props = edge
            elif len(edge) == 3:
                src, dst, props = edge
                rel = (props or {}).get("relationship_name", "")
            else:
                continue
        else:
            continue
        if rel == "SUPERSEDES":
            out.append({"from": src, "to": dst, **(props or {})})
    return out


async def list_entities(limit: int = 50) -> list[tuple[str, dict]]:
    """Return (node_id, properties) for entity-like nodes."""
    graph = await get_graph_engine()
    nodes, _ = await graph.get_graph_data()
    out = []
    for n in nodes or []:
        if isinstance(n, (list, tuple)) and len(n) >= 2:
            nid, props = n[0], n[1]
        elif isinstance(n, dict):
            nid, props = n.get("id"), n
        else:
            continue
        if not nid:
            continue
        out.append((nid, props or {}))
    return out[:limit]


async def list_neighborhood(node_id: str) -> list[tuple[str, str, str]]:
    """Return list of (other_node_id, rel, direction) for one node."""
    graph = await get_graph_engine()
    _, edges = await graph.get_graph_data()
    out = []
    for e in edges or []:
        if isinstance(e, (list, tuple)):
            if len(e) == 4:
                src, dst, rel, _ = e
            elif len(e) == 3:
                src, dst, props = e
                rel = (props or {}).get("relationship_name", "")
            else:
                continue
            if src == node_id:
                out.append((dst, rel, "out"))
            elif dst == node_id:
                out.append((src, rel, "in"))
    return out


async def write_inferred_edge(src: str, dst: str, rel: str,
                              reason: str) -> None:
    """Like write_supersedes_edge but for INFERRED-relationship rethink output."""
    import time
    graph = await get_graph_engine()
    await graph.add_edge(
        src, dst,
        relationship_name=rel or "RELATED_TO",
        properties={"source": "rethink", "reason": reason, "ts": time.time()},
    )
    event(logger, "cognee.add_edge.INFERRED",
          src=src, dst=dst, rel=rel or "RELATED_TO", reason=reason[:40])


async def graph_stats() -> dict:
    graph = await get_graph_engine()
    nodes, edges = await graph.get_graph_data()
    return {"nodes": len(nodes), "edges": len(edges)}


async def doctor_graph_snapshot() -> tuple[dict, list[dict]]:
    """Stats + SUPERSEDES in one asyncio loop.

    Calling ``asyncio.run`` twice in a row (separate ``run()`` invocations)
    can leave Kuzu unable to re-lock its DB file on macOS.
    """
    stats = await graph_stats()
    sups = await list_supersedes()
    return stats, sups


async def reset() -> None:
    # 0.5.8 prune_system signature: (graph=True, vector=True, metadata=False, cache=True)
    await cognee.prune.prune_data()
    await cognee.prune.prune_system(graph=True, vector=True, metadata=True, cache=True)


async def _with_cloud_init(coro):
    """Wrap a coroutine so the cloud bootstrap fires once before it runs."""
    await _init_cloud_if_configured()
    return await coro


# Persistent event loop -- cognee.serve()'s aiohttp session ties itself to the
# loop where it was created. asyncio.run() creates+closes a fresh loop each
# call, which invalidates the cognee cloud client and causes "Event loop is
# closed" RuntimeError on the second cognee call. We instead keep a single
# loop alive for the lifetime of the process.
_LOOP: asyncio.AbstractEventLoop | None = None


def _get_loop() -> asyncio.AbstractEventLoop:
    global _LOOP
    if _LOOP is None or _LOOP.is_closed():
        _LOOP = asyncio.new_event_loop()
    return _LOOP


def run(coro):
    """Synchronous entrypoint for Click commands. Uses a persistent loop so
    cognee's cloud client / aiohttp session survives across multiple calls."""
    loop = _get_loop()
    return loop.run_until_complete(_with_cloud_init(coro))
