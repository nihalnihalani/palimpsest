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
