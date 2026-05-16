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
