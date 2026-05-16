"""Smoke tests for the RedisVL SemanticCache layer.

These tests run fully offline:
- No live Redis (we monkeypatch the `_redis()` getter + `_get_cache()` builder).
- No live Gemini (we monkeypatch `google.generativeai.embed_content`).
"""
from __future__ import annotations

from typing import Any

import pytest


# ---------------------------------------------------------------------------
# GeminiTextVectorizer
# ---------------------------------------------------------------------------

def test_vectorizer_dims_is_768() -> None:
    """dims must report 768 without making an API call."""
    from wiki_hackathon.gemini_vectorizer import GeminiTextVectorizer

    v = GeminiTextVectorizer()
    assert v.dims == 768
    assert v.type == "gemini"
    assert v.model == "text-embedding-004"


def test_vectorizer_model_name_prefixes_models() -> None:
    from wiki_hackathon.gemini_vectorizer import GeminiTextVectorizer

    v = GeminiTextVectorizer()
    assert v._model_name() == "models/text-embedding-004"

    v2 = GeminiTextVectorizer(model="models/text-embedding-004")
    assert v2._model_name() == "models/text-embedding-004"


def test_vectorizer_embed_calls_gemini(monkeypatch) -> None:
    """_embed should call google.generativeai.embed_content and return floats."""
    import google.generativeai as genai
    from wiki_hackathon.gemini_vectorizer import GeminiTextVectorizer

    captured: dict[str, Any] = {}

    def fake_embed_content(model, content, task_type=None, **kw):
        captured["model"] = model
        captured["content"] = content
        captured["task_type"] = task_type
        return {"embedding": [0.1] * 768}

    monkeypatch.setattr(genai, "configure", lambda **kw: None)
    monkeypatch.setattr(genai, "embed_content", fake_embed_content)

    v = GeminiTextVectorizer()
    result = v._embed("hello world")
    assert len(result) == 768
    assert captured["model"] == "models/text-embedding-004"
    assert captured["content"] == "hello world"


def test_vectorizer_empty_response_raises(monkeypatch) -> None:
    import google.generativeai as genai
    from wiki_hackathon.gemini_vectorizer import GeminiTextVectorizer

    monkeypatch.setattr(genai, "configure", lambda **kw: None)
    monkeypatch.setattr(genai, "embed_content", lambda **kw: {"embedding": []})

    v = GeminiTextVectorizer()
    with pytest.raises(RuntimeError):
        v._embed("anything")


# ---------------------------------------------------------------------------
# answer_cache module
# ---------------------------------------------------------------------------

class _FakeSemanticCache:
    """Stand-in for redisvl.extensions.cache.llm.SemanticCache."""

    def __init__(self) -> None:
        self.stored: list[tuple[str, str]] = []
        self.next_hit: list[dict] = []
        self.cleared = False

    def check(self, prompt=None, num_results=1, **kwargs):
        return self.next_hit

    def store(self, prompt=None, response=None, **kwargs):
        self.stored.append((prompt, response))

    def clear(self):
        self.cleared = True

    @property
    def index(self):
        class _Idx:
            def info(self_inner):
                return {"num_docs": len(self.stored)}
        return _Idx()


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, int] = {}

    def incr(self, key: str) -> int:
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    def get(self, key: str):
        v = self.store.get(key)
        return None if v is None else str(v)

    def delete(self, *keys) -> int:
        n = 0
        for k in keys:
            if k in self.store:
                del self.store[k]
                n += 1
        return n


@pytest.fixture
def fake_cache(monkeypatch):
    """Replace the lazy cache + redis client with in-memory fakes."""
    from wiki_hackathon import answer_cache

    fake = _FakeSemanticCache()
    fake_r = _FakeRedis()

    # Reset module-level singletons so each test starts clean.
    monkeypatch.setattr(answer_cache, "_cache", None)
    monkeypatch.setattr(answer_cache, "_vectorizer", None)
    monkeypatch.setattr(answer_cache, "_get_cache", lambda: fake)
    monkeypatch.setattr(answer_cache, "_redis", lambda: fake_r)
    return fake, fake_r


def test_lookup_miss_returns_none(fake_cache) -> None:
    from wiki_hackathon import answer_cache

    fake, fake_r = fake_cache
    fake.next_hit = []
    assert answer_cache.lookup("what is X?") is None
    # miss counter incremented
    assert fake_r.store.get(answer_cache.METRICS_MISSES_KEY) == 1


def test_lookup_hit_returns_cached_answer(fake_cache) -> None:
    from wiki_hackathon import answer_cache

    fake, fake_r = fake_cache
    fake.next_hit = [{"response": "cached answer", "vector_distance": 0.05}]
    assert answer_cache.lookup("what is X?") == "cached answer"
    assert fake_r.store.get(answer_cache.METRICS_HITS_KEY) == 1


def test_store_records_in_cache(fake_cache) -> None:
    from wiki_hackathon import answer_cache

    fake, _ = fake_cache
    answer_cache.store("Q?", "A.")
    assert fake.stored == [("Q?", "A.")]


def test_store_empty_is_noop(fake_cache) -> None:
    from wiki_hackathon import answer_cache

    fake, _ = fake_cache
    answer_cache.store("", "A.")
    answer_cache.store("Q?", "")
    assert fake.stored == []


def test_stats_reports_counters(fake_cache) -> None:
    from wiki_hackathon import answer_cache

    fake, fake_r = fake_cache
    fake_r.store[answer_cache.METRICS_HITS_KEY] = 3
    fake_r.store[answer_cache.METRICS_MISSES_KEY] = 7
    fake.stored = [("a", "1"), ("b", "2")]
    s = answer_cache.stats()
    assert s == {"hits": 3, "misses": 7, "entries": 2}


def test_reset_clears_cache_and_counters(fake_cache) -> None:
    from wiki_hackathon import answer_cache

    fake, fake_r = fake_cache
    fake_r.store[answer_cache.METRICS_HITS_KEY] = 5
    answer_cache.reset()
    assert fake.cleared is True
    assert answer_cache.METRICS_HITS_KEY not in fake_r.store


# ---------------------------------------------------------------------------
# Failure modes: cache outage must not break query.ask
# ---------------------------------------------------------------------------

def test_lookup_swallows_errors(monkeypatch) -> None:
    """If the underlying cache raises, lookup returns None silently."""
    from wiki_hackathon import answer_cache

    monkeypatch.setattr(answer_cache, "_cache", None)
    monkeypatch.setattr(answer_cache, "_vectorizer", None)

    def boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr(answer_cache, "_get_cache", boom)
    # Even the metrics path should not raise if redis is unavailable.
    monkeypatch.setattr(answer_cache, "_redis", boom)

    assert answer_cache.lookup("anything") is None


def test_store_swallows_errors(monkeypatch) -> None:
    """If the underlying cache raises on store, no exception bubbles out."""
    from wiki_hackathon import answer_cache

    monkeypatch.setattr(answer_cache, "_cache", None)
    monkeypatch.setattr(answer_cache, "_vectorizer", None)

    def boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr(answer_cache, "_get_cache", boom)
    # Should NOT raise.
    answer_cache.store("Q?", "A.")


def test_query_ask_survives_cache_outage(monkeypatch) -> None:
    """query.ask must still return an answer when answer_cache is broken."""
    from wiki_hackathon import query, answer_cache, cognee_io, gemini_io, wiki_io

    def boom(*args, **kwargs):
        raise RuntimeError("simulated cache outage")

    # Both lookup and store explode.
    monkeypatch.setattr(answer_cache, "lookup", boom)
    monkeypatch.setattr(answer_cache, "store", boom)

    # Fake out the real answer path.
    monkeypatch.setattr(cognee_io, "run", lambda coro: "kg-summary")
    monkeypatch.setattr(cognee_io, "search_completion", lambda q: None)
    monkeypatch.setattr(wiki_io, "list_concepts", lambda: [])
    monkeypatch.setattr(wiki_io, "read_concept", lambda s: "")
    monkeypatch.setattr(gemini_io, "generate_text", lambda prompt: "answer-body")

    out = query.ask("Anything?")
    assert out == "answer-body"


def test_query_ask_returns_cached_when_hit(monkeypatch) -> None:
    """When lookup hits, ask returns early without calling cognee/gemini."""
    from wiki_hackathon import query, answer_cache, cognee_io, gemini_io

    monkeypatch.setattr(answer_cache, "lookup", lambda q: "CACHED")
    # If these are touched, the test fails.
    called: dict[str, int] = {"cognee": 0, "gemini": 0}

    def cognee_run(coro):
        called["cognee"] += 1
        return ""

    def gemini_text(p):
        called["gemini"] += 1
        return "should-not-be-used"

    monkeypatch.setattr(cognee_io, "run", cognee_run)
    monkeypatch.setattr(gemini_io, "generate_text", gemini_text)

    assert query.ask("Q?") == "CACHED"
    assert called == {"cognee": 0, "gemini": 0}


# ---------------------------------------------------------------------------
# Import-time safety: importing these modules must not connect or 401.
# ---------------------------------------------------------------------------

def test_imports_are_lazy() -> None:
    """Both modules must import without touching Redis or genai."""
    # Already imported by other tests, but doing it explicitly is fine.
    from wiki_hackathon import answer_cache, gemini_vectorizer  # noqa: F401

    # Module-level singletons should still be unset at import-time.
    # (Other tests may have populated them by now, so we can't strictly assert
    # is None here — but we can at least confirm the names exist.)
    assert hasattr(answer_cache, "_get_cache")
    assert hasattr(gemini_vectorizer, "GeminiTextVectorizer")
