"""4 fast smoke tests.

The Redis-dependent tests gracefully skip when Redis isn't reachable.
The wiki_io and lint tests run fully offline via monkeypatch + tmp_path.
"""
from __future__ import annotations

import pytest
import redis as redis_lib

from wiki_hackathon import redis_bus, wiki_io, lint


def _redis_available() -> bool:
    try:
        redis_bus.client().ping()
        return True
    except (redis_lib.exceptions.ConnectionError, OSError):
        return False
    except Exception:
        return False


requires_redis = pytest.mark.skipif(
    not _redis_available(), reason="Redis not running"
)


@pytest.fixture(autouse=True)
def _clean_redis():
    if _redis_available():
        redis_bus.reset_streams()
    yield


@requires_redis
def test_dedup_setnx() -> None:
    assert redis_bus.mark_seen("abc") is True
    assert redis_bus.mark_seen("abc") is False


@requires_redis
def test_audit_and_publish() -> None:
    redis_bus.audit({"slug": "x", "reason": "test"})
    assert redis_bus.client().xlen("wiki:evolution") >= 1


def test_wiki_io_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("wiki_hackathon.wiki_io.CONCEPTS_DIR", tmp_path)
    wiki_io.write_concept("foo", "Foo", "Body of [[bar]]", ["src1"])
    assert (tmp_path / "foo.md").exists()
    assert "[[bar]]" in (tmp_path / "foo.md").read_text()


def test_rethink_offline(monkeypatch) -> None:
    """rethink() should walk the graph, call Gemini per entity, write edges.

    Fully offline: monkeypatches cognee_io helpers + gemini_io.generate_json.
    Forces the manual path (no memify) so we exercise the deterministic
    fallback that ships with the CLI.
    """
    from wiki_hackathon import rethink as rethink_mod
    from wiki_hackathon import cognee_io, gemini_io

    # Fake graph: 2 entities, each with a non-empty neighborhood
    fake_entities = [
        ("entity:agents", {"name": "AI Agents"}),
        ("entity:cognee", {"name": "Cognee"}),
        ("entity:lonely", {"name": "Lonely"}),  # no neighborhood — filtered
    ]
    fake_nbh = {
        "entity:agents": [
            ("entity:cognee", "USES", "out"),
            ("entity:gemini", "POWERED_BY", "out"),
        ],
        "entity:cognee": [
            ("entity:agents", "USES", "in"),
            ("entity:graph", "STORES_IN", "out"),
        ],
        "entity:lonely": [],
    }

    async def fake_list_entities(limit: int = 50):
        return fake_entities[:limit]

    async def fake_list_neighborhood(node_id: str):
        return fake_nbh.get(node_id, [])

    write_calls: list[tuple[str, str, str, str]] = []

    async def fake_write_inferred_edge(src, dst, rel, reason):
        write_calls.append((src, dst, rel, reason))

    monkeypatch.setattr(cognee_io, "list_entities", fake_list_entities)
    monkeypatch.setattr(cognee_io, "list_neighborhood", fake_list_neighborhood)
    monkeypatch.setattr(cognee_io, "write_inferred_edge", fake_write_inferred_edge)

    gemini_call_count = {"n": 0}

    def fake_generate_json(prompt: str):
        gemini_call_count["n"] += 1
        # Each call returns one inferred edge + one contradiction so we can
        # verify both branches.
        return {
            "contradictions": [
                {"a": "x", "b": "y", "explanation": "they conflict"}
            ],
            "inferred_edges": [
                {"from": "<self>", "to": "entity:other",
                 "rel": "RELATED_TO", "reason": "obvious"}
            ],
        }

    monkeypatch.setattr(gemini_io, "generate_json", fake_generate_json)

    # Force the manual path so the test never touches a real cognee.memify
    monkeypatch.setattr(rethink_mod, "_memify_available", lambda: False)

    result = rethink_mod.rethink()

    # Only the 2 entities with neighborhoods get inspected; "lonely" is filtered
    assert result["entities_inspected"] == 2
    assert gemini_call_count["n"] == 2
    assert result["contradictions_found"] == 2  # one per entity
    assert result["inferred_edges"] == 2  # one edge each
    assert len(write_calls) == 2
    # And the <self> placeholder got rewritten to the node id
    srcs = {c[0] for c in write_calls}
    assert "entity:agents" in srcs and "entity:cognee" in srcs
    assert result["path"] == "manual"


class _FakeJsonClient:
    """Mimics redis.Redis.json() — JSON-path lookups return list-wrapped values."""

    def __init__(self, store: dict[str, dict]) -> None:
        self._store = store

    def get(self, key: str, path: str):
        doc = self._store.get(key)
        if doc is None:
            return None
        # Strip leading "$." for sub-paths; "$" returns whole doc list-wrapped.
        if path == "$":
            return [doc]
        attr = path[2:] if path.startswith("$.") else path
        if attr in doc:
            return [doc[attr]]
        return []


class _FakeRedis:
    """Minimal stand-in for redis.Redis used by snapshot_concept_at."""

    def __init__(self, store: dict[str, dict]) -> None:
        self._store = store

    def exists(self, key: str) -> int:
        return 1 if key in self._store else 0

    def json(self) -> _FakeJsonClient:
        return _FakeJsonClient(self._store)


def test_timemachine_snapshot(monkeypatch) -> None:
    from wiki_hackathon import timemachine, redis_bus

    fake = _FakeRedis({
        "wiki:concept:foo": {
            "current": "v3-current",
            "history": [
                {"text": "v1-oldest", "replaced_at": 100.0},
                {"text": "v2-middle", "replaced_at": 200.0},
            ],
        }
    })
    monkeypatch.setattr(redis_bus, "client", lambda: fake)

    # as_of 50 (before any rewrite) — earliest archived candidate (v1, replaced at 100)
    assert timemachine.snapshot_concept_at("foo", 50.0) == "v1-oldest"
    # as_of 150 (between two rewrites) — v2 was current then (replaced at 200)
    assert timemachine.snapshot_concept_at("foo", 150.0) == "v2-middle"
    # as_of now (after all rewrites) — current
    import time
    assert timemachine.snapshot_concept_at("foo", time.time()) == "v3-current"

    # Missing concept returns None
    assert timemachine.snapshot_concept_at("does-not-exist", 50.0) is None


def test_timemachine_parse_as_of() -> None:
    import time as _time
    from wiki_hackathon import timemachine

    now = _time.time()
    # 'now' / empty → ~now
    assert abs(timemachine._parse_as_of("now") - now) < 2.0
    assert abs(timemachine._parse_as_of("") - now) < 2.0
    # pre-ingest aliases → 0.0
    assert timemachine._parse_as_of("pre-ingest") == 0.0
    assert timemachine._parse_as_of("before-contradictions") == 0.0
    assert timemachine._parse_as_of("0") == 0.0
    # relative offsets
    assert abs(timemachine._parse_as_of("now-30s") - (now - 30)) < 2.0
    assert abs(timemachine._parse_as_of("now-5m") - (now - 300)) < 2.0
    assert abs(timemachine._parse_as_of("now-1h") - (now - 3600)) < 2.0
    # raw epoch seconds
    assert timemachine._parse_as_of("12345.5") == 12345.5


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
