"""4 fast smoke tests.

The Redis-dependent tests gracefully skip when Redis isn't reachable.
The wiki_io and lint tests run fully offline via monkeypatch + tmp_path.
"""
from __future__ import annotations

import pytest
import redis as redis_lib

from palimpsest import redis_bus, wiki_io, lint


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
    monkeypatch.setattr("palimpsest.wiki_io.CONCEPTS_DIR", tmp_path)
    wiki_io.write_concept("foo", "Foo", "Body of [[bar]]", ["src1"])
    assert (tmp_path / "foo.md").exists()
    assert "[[bar]]" in (tmp_path / "foo.md").read_text()


def test_rethink_offline(monkeypatch) -> None:
    """rethink() should walk the graph, call Gemini per entity, write edges.

    Fully offline: monkeypatches cognee_io helpers + gemini_io.generate_json.
    Forces the manual path (no memify) so we exercise the deterministic
    fallback that ships with the CLI.
    """
    from palimpsest import rethink as rethink_mod
    from palimpsest import cognee_io, gemini_io

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
    from palimpsest import timemachine, redis_bus

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
    from palimpsest import timemachine

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


def test_write_exploration(tmp_path, monkeypatch) -> None:
    from palimpsest import wiki_io, config
    monkeypatch.setattr(config, "EXPLORATIONS_DIR", tmp_path)
    p = wiki_io.write_exploration(
        "What is X?", "X is [[foo]] and [[bar]].",
        citations=["foo", "bar"])
    assert p.exists()
    text = p.read_text()
    assert "[[foo]]" in text
    assert "question:" in text
    assert "asked_at:" in text


def test_find_citations(monkeypatch) -> None:
    from palimpsest import wiki_io
    answer = "See [[Agent Memory]] and [[context-window]] also [[foo|bar]]."
    cits = wiki_io.find_citations_in_text(answer)
    assert "agent-memory" in cits
    assert "context-window" in cits
    assert "foo" in cits  # alias form takes the LHS


def test_lint_report_minimal(monkeypatch, tmp_path) -> None:
    # Stub Cognee calls so test runs without it
    monkeypatch.setattr(lint, "supersedes_summary", lambda: [])
    monkeypatch.setattr(lint, "kg_stats", lambda: {"nodes": 0, "edges": 0})
    monkeypatch.setattr(lint, "CONCEPTS_DIR", tmp_path)
    monkeypatch.setattr(lint, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr("palimpsest.wiki_io.CONCEPTS_DIR", tmp_path)
    (tmp_path / "a.md").write_text("[[b]]")
    p = lint.write_report()
    assert "broken wikilinks" in p.read_text()


def test_fix_broken_wikilinks(tmp_path, monkeypatch) -> None:
    from palimpsest import lint, wiki_io
    monkeypatch.setattr(lint, "CONCEPTS_DIR", tmp_path)
    monkeypatch.setattr("palimpsest.wiki_io.CONCEPTS_DIR", tmp_path)
    # set up: agent-memory.md exists, two other pages reference it differently
    (tmp_path / "agent-memory.md").write_text("Agent memory page")
    (tmp_path / "foo.md").write_text("links to [[Agent_Memory]]")    # case+underscore mismatch
    (tmp_path / "bar.md").write_text("links to [[nonexistent-page]]") # truly broken
    result = lint.fix_broken_wikilinks()
    assert result["fixed"] == 1   # foo.md repointed
    assert result["stripped"] == 1  # bar.md unresolvable
    # foo.md should now contain [[agent-memory]] (real slug)
    assert "[[agent-memory]]" in (tmp_path / "foo.md").read_text()
    # bar.md should NO LONGER have brackets around nonexistent-page
    assert "[[nonexistent-page]]" not in (tmp_path / "bar.md").read_text()
    assert "nonexistent-page" in (tmp_path / "bar.md").read_text()


def test_chat_session_roundtrip(tmp_path, monkeypatch) -> None:
    from palimpsest import chat_session
    monkeypatch.setattr(chat_session, "CHATS_DIR", tmp_path)
    s = chat_session.ChatSession.new(title="hello")
    s.append("user", "first question")
    s.append("model", "first answer")
    p = s.save()
    assert p.exists()
    loaded = chat_session.ChatSession.load(s.id)
    assert loaded.title == "hello"
    assert len(loaded.messages) == 2
    assert loaded.messages[0]["content"] == "first question"


def test_chat_session_listing(tmp_path, monkeypatch) -> None:
    from palimpsest import chat_session
    import time
    monkeypatch.setattr(chat_session, "CHATS_DIR", tmp_path)
    s1 = chat_session.ChatSession.new("first")
    s1.save()
    time.sleep(0.01)
    s2 = chat_session.ChatSession.new("second")
    s2.save()
    listing = chat_session.list_all()
    assert len(listing) == 2
    # newest-first
    assert listing[0].id == s2.id


def test_chat_session_prefix_resolve(tmp_path, monkeypatch) -> None:
    from palimpsest import chat_session
    monkeypatch.setattr(chat_session, "CHATS_DIR", tmp_path)
    s = chat_session.ChatSession.new("p")
    s.save()
    resolved = chat_session.resolve_id(s.id[:8])
    assert resolved == s.id


def test_chat_transcript_md(tmp_path, monkeypatch) -> None:
    from palimpsest import chat_session
    monkeypatch.setattr(chat_session, "CHATS_DIR", tmp_path)
    s = chat_session.ChatSession.new("titled")
    s.append("user", "hello?")
    s.append("model", "hi there")
    md = s.transcript_md()
    assert "# titled" in md
    assert "User" in md
    assert "Wiki" in md
    assert "hello?" in md


def test_lint_report_with_fix_section(tmp_path, monkeypatch) -> None:
    from palimpsest import lint
    monkeypatch.setattr(lint, "supersedes_summary", lambda: [])
    monkeypatch.setattr(lint, "kg_stats", lambda: {"nodes": 0, "edges": 0})
    monkeypatch.setattr(lint, "CONCEPTS_DIR", tmp_path)
    monkeypatch.setattr(lint, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr("palimpsest.wiki_io.CONCEPTS_DIR", tmp_path)
    (tmp_path / "a.md").write_text("[[b]]")
    fix_result = {"fixed": 0, "stripped": 1, "details": ["a: stripped [[b]]"]}
    p = lint.write_report(fix_result=fix_result)
    text = p.read_text()
    assert "Fixes applied" in text
    assert "stripped 1" in text
