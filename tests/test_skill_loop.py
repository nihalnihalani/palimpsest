"""Offline tests for the propose-then-apply skill loop.

Every cognee call is mocked. No Redis, no Gemini, no live cognee — the test
runs in <1s on a cold venv. We exercise:

- remember_skills() returns a dict on a happy mocked path.
- record_run(..., apply=False) returns a dict containing 'proposal_id'.
- record_run(..., apply=True) calls improve_skill exactly once.
- apply_proposal() calls improve_skill exactly once.
- status() works even when cognee isn't importable.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from wiki_hackathon import skill_loop


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeRememberResult:
    """Mimics cognee.api.v1.remember.remember.RememberResult.

    Only the bits we read in skill_loop: ``to_dict()``, ``items``, and
    ``dataset_id``.
    """

    def __init__(self, *, dataset_id: str = "11111111-1111-1111-1111-111111111111",
                 items: list[dict] | None = None,
                 items_processed: int = 1):
        self.dataset_id = dataset_id
        self.items = items or []
        self.items_processed = items_processed
        self.status = "completed"
        self.dataset_name = skill_loop.DATASET

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "dataset_name": self.dataset_name,
            "dataset_id": self.dataset_id,
            "items_processed": self.items_processed,
            "items": self.items,
        }


def _patch_sync_runner(monkeypatch) -> None:
    """Replace cognee_io.run with a plain ``asyncio.run``-equivalent that
    drives the coroutine to completion. The real implementation already
    does asyncio.run, so we just import it directly to avoid touching the
    cognee_io module at import time."""
    import asyncio

    def _run(coro):
        return asyncio.run(coro)

    monkeypatch.setattr(skill_loop.cognee_io, "run", _run)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_constants() -> None:
    assert skill_loop.DATASET == "wiki"
    assert skill_loop.SESSION == "wiki-improve"


def test_status_works_without_cognee(tmp_path, monkeypatch) -> None:
    """status() should never raise — even if cognee is broken."""
    monkeypatch.setattr(skill_loop, "STATE_FILE", tmp_path / "skill_runs.json")
    monkeypatch.setattr(skill_loop, "SKILLS_DIR", tmp_path / "my_skills")
    (tmp_path / "my_skills" / "demo-skill").mkdir(parents=True)
    (tmp_path / "my_skills" / "demo-skill" / "SKILL.md").write_text("---\n")
    s = skill_loop.status()
    assert s["dataset"] == "wiki"
    assert s["session"] == "wiki-improve"
    assert "demo-skill" in s["ingested_skills"]
    assert s["last_run"] is None
    assert s["last_proposal"] is None


def test_require_raises_when_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(skill_loop, "SKILL_LOOP_AVAILABLE", False)
    monkeypatch.setattr(skill_loop, "_IMPORT_ERROR", RuntimeError("boom"))
    with pytest.raises(RuntimeError, match="skill_loop is unavailable"):
        skill_loop.remember_skills()
    with pytest.raises(RuntimeError):
        skill_loop.run_skill("x", "y")
    with pytest.raises(RuntimeError):
        skill_loop.record_run("x", "y", "z", 0.5)
    with pytest.raises(RuntimeError):
        skill_loop.apply_proposal("x", "p")


def test_remember_skills_happy_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(skill_loop, "SKILL_LOOP_AVAILABLE", True)
    monkeypatch.setattr(skill_loop, "STATE_FILE", tmp_path / "skill_runs.json")
    _patch_sync_runner(monkeypatch)

    async def fake_setup():
        return None

    async def fake_remember(path, *, dataset_name, content_type, **kw):
        assert dataset_name == skill_loop.DATASET
        assert content_type == "skills"
        return _FakeRememberResult(items_processed=3)

    fake_cognee = SimpleNamespace(remember=fake_remember)
    monkeypatch.setattr(skill_loop, "_cognee", fake_cognee)
    monkeypatch.setattr(skill_loop, "_cognee_setup", fake_setup)

    out = skill_loop.remember_skills()
    assert isinstance(out, dict)
    assert out["dataset_id"] == "11111111-1111-1111-1111-111111111111"
    assert out["items_processed"] == 3

    # State file got written atomically.
    assert (tmp_path / "skill_runs.json").exists()
    s = skill_loop.status()
    assert s["last_remember"]["dataset_id"] == out["dataset_id"]


def test_record_run_propose_only(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(skill_loop, "SKILL_LOOP_AVAILABLE", True)
    monkeypatch.setattr(skill_loop, "STATE_FILE", tmp_path / "skill_runs.json")
    _patch_sync_runner(monkeypatch)

    captured_kwargs: dict = {}

    async def fake_remember(entry, *, dataset_name, session_id,
                            skill_improvement, **kw):
        captured_kwargs["entry"] = entry
        captured_kwargs["dataset_name"] = dataset_name
        captured_kwargs["session_id"] = session_id
        captured_kwargs["skill_improvement"] = skill_improvement
        return _FakeRememberResult(items=[
            {"kind": "skill_run", "run_id": "r-1",
             "selected_skill_id": "wiki-ingest", "success_score": 0.3},
            {"kind": "skill_improvement_proposal",
             "proposal_id": "prop-abc", "skill_name": "wiki-ingest",
             "status": "proposed"},
        ])

    improve_calls: list[tuple] = []

    async def fake_improve(*args, **kwargs):
        improve_calls.append((args, kwargs))

    fake_cognee = SimpleNamespace(remember=fake_remember)
    monkeypatch.setattr(skill_loop, "_cognee", fake_cognee)
    monkeypatch.setattr(skill_loop, "_improve_skill", fake_improve)

    out = skill_loop.record_run(
        skill_name="wiki-ingest",
        task_text="ingest item-42",
        result_summary="missed concept extraction",
        success_score=0.3,
        apply=False,
        score_threshold=0.7,
    )

    assert isinstance(out, dict)
    assert out["proposal_id"] == "prop-abc"
    assert out["applied"] is False
    assert out["feedback"] == -1.0  # score < threshold

    # Propose-only — improve_skill must NOT have been called.
    assert improve_calls == []

    # remember() got the right kwargs
    assert captured_kwargs["dataset_name"] == "wiki"
    assert captured_kwargs["session_id"] == "wiki-improve"
    assert captured_kwargs["skill_improvement"]["apply"] is False
    assert captured_kwargs["skill_improvement"]["score_threshold"] == 0.7
    assert captured_kwargs["skill_improvement"]["skill_name"] == "wiki-ingest"

    # State file remembers the proposal so a follow-up `wiki improve --apply`
    # can find it.
    s = skill_loop.status()
    assert s["last_proposal"]["proposal_id"] == "prop-abc"
    assert s["last_proposal"]["applied"] is False


def test_record_run_apply_true_calls_improve_skill(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(skill_loop, "SKILL_LOOP_AVAILABLE", True)
    monkeypatch.setattr(skill_loop, "STATE_FILE", tmp_path / "skill_runs.json")
    _patch_sync_runner(monkeypatch)

    async def fake_remember(*args, **kwargs):
        return _FakeRememberResult(items=[
            {"kind": "skill_improvement_proposal",
             "proposal_id": "prop-xyz", "skill_name": "wiki-ingest",
             "status": "proposed"},
        ])

    async def fake_resolve(dataset_id):
        user = SimpleNamespace(id="u-1")
        dataset = SimpleNamespace(id=dataset_id, name="wiki")
        return user, [dataset]

    improve_calls: list[dict] = []

    async def fake_improve(skill_name, *, dataset, user, proposal_id, apply):
        improve_calls.append({
            "skill_name": skill_name,
            "proposal_id": proposal_id,
            "apply": apply,
            "user": user,
            "dataset": dataset,
        })

    fake_cognee = SimpleNamespace(remember=fake_remember)
    monkeypatch.setattr(skill_loop, "_cognee", fake_cognee)
    monkeypatch.setattr(skill_loop, "_resolve_authorized_user_datasets",
                        fake_resolve)
    monkeypatch.setattr(skill_loop, "_improve_skill", fake_improve)

    out = skill_loop.record_run(
        skill_name="wiki-ingest",
        task_text="ingest item-99",
        result_summary="hallucinated a citation",
        success_score=0.2,
        apply=True,
        score_threshold=0.7,
    )

    # improve_skill was called EXACTLY once, with the right proposal_id and
    # apply=True (this is the actual on-disk rewrite step).
    assert len(improve_calls) == 1
    assert improve_calls[0]["skill_name"] == "wiki-ingest"
    assert improve_calls[0]["proposal_id"] == "prop-xyz"
    assert improve_calls[0]["apply"] is True

    assert out["applied"] is True
    assert out["proposal_id"] == "prop-xyz"

    # State got persisted with applied=True
    s = skill_loop.status()
    assert s["last_proposal"]["applied"] is True


def test_record_run_apply_true_no_proposal_skips_improve(tmp_path, monkeypatch) -> None:
    """When cognee returns no proposal (score above threshold), apply=True
    must NOT call improve_skill — there's nothing to apply."""
    monkeypatch.setattr(skill_loop, "SKILL_LOOP_AVAILABLE", True)
    monkeypatch.setattr(skill_loop, "STATE_FILE", tmp_path / "skill_runs.json")
    _patch_sync_runner(monkeypatch)

    async def fake_remember(*args, **kwargs):
        return _FakeRememberResult(items=[
            {"kind": "skill_run", "run_id": "r-2",
             "selected_skill_id": "wiki-ingest", "success_score": 0.95},
        ])

    improve_calls: list = []

    async def fake_improve(*a, **k):
        improve_calls.append((a, k))

    fake_cognee = SimpleNamespace(remember=fake_remember)
    monkeypatch.setattr(skill_loop, "_cognee", fake_cognee)
    monkeypatch.setattr(skill_loop, "_improve_skill", fake_improve)

    out = skill_loop.record_run(
        skill_name="wiki-ingest",
        task_text="happy run",
        result_summary="clean",
        success_score=0.95,
        apply=True,
        score_threshold=0.7,
    )
    assert out["proposal_id"] is None
    assert out["applied"] is False
    assert improve_calls == []  # nothing to apply


def test_apply_proposal_calls_improve_skill_once(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(skill_loop, "SKILL_LOOP_AVAILABLE", True)
    monkeypatch.setattr(skill_loop, "STATE_FILE", tmp_path / "skill_runs.json")
    _patch_sync_runner(monkeypatch)

    async def fake_setup():
        return None

    async def fake_remember(*args, **kwargs):
        return _FakeRememberResult()

    async def fake_resolve(dataset_id):
        user = SimpleNamespace(id="u-1")
        dataset = SimpleNamespace(id=dataset_id, name="wiki")
        return user, [dataset]

    improve_calls: list = []

    async def fake_improve(skill_name, *, dataset, user, proposal_id, apply):
        improve_calls.append((skill_name, proposal_id, apply))

    fake_cognee = SimpleNamespace(remember=fake_remember)
    monkeypatch.setattr(skill_loop, "_cognee", fake_cognee)
    monkeypatch.setattr(skill_loop, "_cognee_setup", fake_setup)
    monkeypatch.setattr(skill_loop, "_resolve_authorized_user_datasets",
                        fake_resolve)
    monkeypatch.setattr(skill_loop, "_improve_skill", fake_improve)

    skill_loop.apply_proposal("wiki-ingest", "prop-zzz")
    assert len(improve_calls) == 1
    assert improve_calls[0] == ("wiki-ingest", "prop-zzz", True)
