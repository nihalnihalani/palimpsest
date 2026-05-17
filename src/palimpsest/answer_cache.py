"""Semantic cache for Q&A on top of RedisVL.

Wraps `redisvl.extensions.cache.llm.SemanticCache` with:
- Gemini-backed vectorizer (avoids the 800 MB torch/sentence-transformers
  default).
- Lazy init: importing this module does NOT connect to Redis or Gemini.
- Distinct `wiki:answer-cache` keyspace from existing `wiki:concept:*` keys.
- Strict `distance_threshold=0.10` for factual Q&A.
- 15-minute TTL.
- Hit/miss counters in Redis under `wiki:answer-cache:metrics:{hits|misses}`.

This is layered on `query.ask()` (question -> answer). It is NOT a replacement
for the exact-match contradiction verdict cache in `redis_bus.py`.
"""
from __future__ import annotations

from typing import Any

from .logs import get_logger, event

logger = get_logger(__name__)

CACHE_NAME = "wiki:answer-cache"
METRICS_HITS_KEY = f"{CACHE_NAME}:metrics:hits"
METRICS_MISSES_KEY = f"{CACHE_NAME}:metrics:misses"
DISTANCE_THRESHOLD = 0.10
TTL_SECONDS = 900  # 15 minutes

_cache: Any = None  # lazy `SemanticCache` instance
_vectorizer: Any = None


def _get_cache() -> Any:
    """Lazy-build the SemanticCache + GeminiTextVectorizer.

    Raises on any failure; callers (e.g. `query.ask`) are expected to wrap
    invocations in try/except so a Redis or Gemini outage doesn't break
    the answer path.
    """
    global _cache, _vectorizer
    if _cache is not None:
        return _cache

    # Local imports so this module is import-safe without env vars.
    from redisvl.extensions.cache.llm import SemanticCache

    from .config import (
        EMBEDDING_API_KEY,
        EMBEDDING_DIMENSIONS,
        EMBEDDING_MODEL,
        EMBEDDING_PROVIDER,
        LLM_ENDPOINT,
        REDIS_URL,
    )

    provider = EMBEDDING_PROVIDER.lower()
    if provider == "openai":
        from .openai_vectorizer import OpenAIEmbeddingVectorizer

        _vectorizer = OpenAIEmbeddingVectorizer(
            model=EMBEDDING_MODEL,
            api_key=EMBEDDING_API_KEY,
            base_url=LLM_ENDPOINT,
            dims=EMBEDDING_DIMENSIONS or 1536,
        )
    elif provider == "gemini":
        from .gemini_vectorizer import GeminiTextVectorizer

        model = EMBEDDING_MODEL
        if model.startswith("gemini/"):
            model = model.split("/", 1)[1]
        _vectorizer = GeminiTextVectorizer(model=model or "text-embedding-004")
    else:
        raise RuntimeError(f"Unsupported EMBEDDING_PROVIDER={EMBEDDING_PROVIDER!r}")

    cache_name = f"{CACHE_NAME}:{provider}:{_vectorizer.dims}"
    _cache = SemanticCache(
        name=cache_name,
        distance_threshold=DISTANCE_THRESHOLD,
        ttl=TTL_SECONDS,
        vectorizer=_vectorizer,
        redis_url=REDIS_URL,
    )
    return _cache


def _redis() -> Any:
    """Re-use the existing Redis client from redis_bus for counters."""
    from . import redis_bus
    return redis_bus.client()


def lookup(question: str) -> str | None:
    """Return a cached answer for a semantically similar question, or None.

    Increments the appropriate hit/miss counter in Redis. Logs an event for
    every call.
    """
    if not question or not isinstance(question, str):
        return None
    try:
        cache = _get_cache()
        hits = cache.check(prompt=question, num_results=1)
    except Exception as e:
        # Cache or vectorizer failure must NEVER break the answer path.
        event(logger, "answer_cache.lookup_error", err=str(e)[:120])
        return None

    if hits:
        answer = hits[0].get("response")
        distance = hits[0].get("vector_distance")
        try:
            _redis().incr(METRICS_HITS_KEY)
        except Exception:
            pass
        event(logger, "answer_cache.hit",
              question=question[:60], distance=distance)
        return answer

    try:
        _redis().incr(METRICS_MISSES_KEY)
    except Exception:
        pass
    event(logger, "answer_cache.miss", question=question[:60])
    return None


def store(question: str, answer: str) -> None:
    """Insert a (question, answer) pair into the semantic cache.

    No-ops on empty inputs or any underlying failure.
    """
    if not question or not answer:
        return
    try:
        cache = _get_cache()
        cache.store(prompt=question, response=answer)
        event(logger, "answer_cache.store",
              question=question[:60], answer_chars=len(answer))
    except Exception as e:
        event(logger, "answer_cache.store_error", err=str(e)[:120])


def stats() -> dict:
    """Return {hits, misses, entries}. Best-effort; returns zeros on failure."""
    hits = 0
    misses = 0
    entries = 0
    try:
        r = _redis()
        hits = int(r.get(METRICS_HITS_KEY) or 0)
        misses = int(r.get(METRICS_MISSES_KEY) or 0)
    except Exception:
        pass
    try:
        cache = _get_cache()
        # RedisVL SearchIndex exposes info(); count the indexed docs.
        info = cache.index.info()
        entries = int(info.get("num_docs", 0))
    except Exception:
        pass
    return {"hits": hits, "misses": misses, "entries": entries}


def reset() -> None:
    """Drop all cached entries + zero the counters. Safe to call repeatedly."""
    try:
        cache = _get_cache()
        cache.clear()
        event(logger, "answer_cache.reset")
    except Exception as e:
        event(logger, "answer_cache.reset_error", err=str(e)[:120])
    try:
        r = _redis()
        r.delete(METRICS_HITS_KEY, METRICS_MISSES_KEY)
    except Exception:
        pass
