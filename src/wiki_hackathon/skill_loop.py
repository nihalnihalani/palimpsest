"""Cognee 1.x propose-then-apply skill loop.

The hackathon's required self-improvement cycle:

    1. ``remember(./my_skills, content_type="skills")`` — ingest skills.
    2. ``cognee.search(AGENTIC_COMPLETION, skills=[...], session_id=...)``
       — let the agent pick a skill and execute the task.
    3. ``remember(SkillRunEntry, ..., skill_improvement={"apply": False})``
       — propose a rewrite (no in-place change yet).
    4. ``improve_skill(skill, proposal_id=..., apply=True)`` — actually
       update the SKILL.md on disk.

All async calls are wrapped through ``cognee_io.run`` so Click commands
stay synchronous. Cognee imports are defensive: if the install is broken,
``SKILL_LOOP_AVAILABLE`` flips to False and every public function raises a
clear RuntimeError instead of an obscure ImportError.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

from . import cognee_io, config  # noqa: F401 — config patches litellm
from .logs import get_logger, event

logger = get_logger(__name__)

# --- Defensive cognee imports ------------------------------------------------
SKILL_LOOP_AVAILABLE = True
_IMPORT_ERROR: Exception | None = None
try:
    import cognee as _cognee
    from cognee.api.v1.search import SearchType as _SearchType
    from cognee.memory import SkillRunEntry as _SkillRunEntry
    from cognee.modules.memify.skill_improvement import (
        improve_skill as _improve_skill,
    )
    from cognee.modules.engine.operations.setup import setup as _cognee_setup
    from cognee.modules.pipelines.layers.resolve_authorized_user_datasets import (
        resolve_authorized_user_datasets as _resolve_authorized_user_datasets,
    )
except Exception as _e:  # noqa: BLE001
    SKILL_LOOP_AVAILABLE = False
    _IMPORT_ERROR = _e
    _cognee = None  # type: ignore[assignment]
    _SearchType = None  # type: ignore[assignment]
    _SkillRunEntry = None  # type: ignore[assignment]
    _improve_skill = None  # type: ignore[assignment]
    _cognee_setup = None  # type: ignore[assignment]
    _resolve_authorized_user_datasets = None  # type: ignore[assignment]
    logger.error(
        f"skill_loop cognee import failed: {type(_e).__name__}: {_e}"
    )


def _require() -> None:
    if not SKILL_LOOP_AVAILABLE:
        raise RuntimeError(
            "skill_loop is unavailable: cognee failed to import "
            f"({type(_IMPORT_ERROR).__name__}: {_IMPORT_ERROR}). "
            "Run `pip install -e .` to repair the venv."
        )


# --- Public constants --------------------------------------------------------
DATASET = "wiki"  # matches cognee_io.DATASET
SESSION = "wiki-improve"

_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = _ROOT / "my_skills"
STATE_FILE = _ROOT / "wiki" / "skill_runs.json"


# --- State persistence (atomic) ----------------------------------------------
def _read_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"skill_loop: state file unreadable ({e}); resetting")
        return {}


def _write_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(STATE_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True),
                   encoding="utf-8")
    os.replace(tmp, STATE_FILE)


# --- Internal async helpers --------------------------------------------------
async def _resolve_user_and_dataset(dataset_id_str: str) -> tuple[Any, Any]:
    """Return (user, dataset) for a given dataset UUID string."""
    dataset_id = UUID(dataset_id_str)
    user, datasets = await _resolve_authorized_user_datasets(dataset_id)
    if not datasets:
        raise RuntimeError(
            f"No authorized datasets found for dataset_id={dataset_id_str}"
        )
    return user, datasets[0]


async def _remember_skills_async() -> dict:
    event(logger, "skill_loop.remember.start", path=str(SKILLS_DIR))
    await _cognee_setup()
    result = await _cognee.remember(
        str(SKILLS_DIR),
        dataset_name=DATASET,
        content_type="skills",
    )
    out = result.to_dict() if hasattr(result, "to_dict") else dict(result)
    event(logger, "skill_loop.remember.done",
          dataset_id=out.get("dataset_id"),
          items=out.get("items_processed"))
    return out


async def _run_skill_async(skill_name: str, prompt: str, max_iter: int) -> dict:
    event(logger, "skill_loop.run.start", skill=skill_name,
          chars=len(prompt), max_iter=max_iter)
    answer = await _cognee.search(
        prompt,
        query_type=_SearchType.AGENTIC_COMPLETION,
        datasets=DATASET,
        skills=[skill_name],
        max_iter=max_iter,
        session_id=SESSION,
    )
    text = str(answer)
    event(logger, "skill_loop.run.done", skill=skill_name, chars=len(text))
    return {"skill": skill_name, "answer": text}


async def _record_run_async(
    skill_name: str,
    task_text: str,
    result_summary: str,
    success_score: float,
    apply: bool,
    score_threshold: float,
) -> dict:
    feedback = -1.0 if success_score < score_threshold else 1.0
    entry = _SkillRunEntry(
        selected_skill_id=skill_name,
        task_text=task_text,
        result_summary=result_summary,
        success_score=success_score,
        feedback=feedback,
    )
    event(logger, "skill_loop.record_run.start",
          skill=skill_name, score=success_score, apply=apply,
          threshold=score_threshold)
    remember_result = await _cognee.remember(
        entry,
        dataset_name=DATASET,
        session_id=SESSION,
        skill_improvement={
            "skill_name": skill_name,
            "apply": False,  # proposal only at this step
            "score_threshold": score_threshold,
        },
    )
    items = list(getattr(remember_result, "items", []) or [])
    proposal_id: Optional[str] = None
    for item in items:
        if isinstance(item, dict) and item.get("kind") == "skill_improvement_proposal":
            proposal_id = item.get("proposal_id")
            break

    out: dict = {
        "skill_name": skill_name,
        "success_score": success_score,
        "feedback": feedback,
        "applied": False,
        "proposal_id": proposal_id,
        "items": items,
    }

    if apply and proposal_id:
        # Need a user + dataset to call improve_skill(..., apply=True).
        dataset_id_str = getattr(remember_result, "dataset_id", None)
        if not dataset_id_str:
            # session_stored path doesn't expose dataset_id directly;
            # fall back to looking it up via a fresh remember of skills.
            ds_meta = await _remember_skills_async()
            dataset_id_str = ds_meta.get("dataset_id")
        if dataset_id_str:
            user, dataset = await _resolve_user_and_dataset(dataset_id_str)
            await _improve_skill(
                skill_name,
                dataset=dataset,
                user=user,
                proposal_id=proposal_id,
                apply=True,
            )
            out["applied"] = True
            event(logger, "skill_loop.apply.done",
                  skill=skill_name, proposal_id=proposal_id)
        else:
            event(logger, "skill_loop.apply.skipped",
                  reason="no_dataset_id", skill=skill_name)

    return out


async def _apply_proposal_async(skill_name: str, proposal_id: str) -> None:
    # We need a (user, dataset) pair. Re-remember to learn the dataset_id.
    await _cognee_setup()
    ds_meta = await _remember_skills_async()
    dataset_id_str = ds_meta.get("dataset_id")
    if not dataset_id_str:
        raise RuntimeError(
            "apply_proposal: could not resolve dataset_id from remember()"
        )
    user, dataset = await _resolve_user_and_dataset(dataset_id_str)
    await _improve_skill(
        skill_name,
        dataset=dataset,
        user=user,
        proposal_id=proposal_id,
        apply=True,
    )
    event(logger, "skill_loop.apply.done",
          skill=skill_name, proposal_id=proposal_id)


# --- Public sync API ---------------------------------------------------------
def remember_skills() -> dict:
    """Ingest ./my_skills into Cognee. Returns dataset metadata dict."""
    _require()
    result = cognee_io.run(_remember_skills_async())
    state = _read_state()
    state["last_remember"] = {
        "ts": time.time(),
        "dataset_id": result.get("dataset_id"),
        "items_processed": result.get("items_processed"),
    }
    _write_state(state)
    return result


def run_skill(skill_name: str, prompt: str, *, max_iter: int = 6) -> dict:
    """Run the agent against one named skill. Returns {skill, answer}."""
    _require()
    return cognee_io.run(_run_skill_async(skill_name, prompt, max_iter))


def record_run(
    skill_name: str,
    task_text: str,
    result_summary: str,
    success_score: float,
    *,
    apply: bool = False,
    score_threshold: float = 0.7,
) -> dict:
    """Write a SkillRunEntry.

    With ``apply=False`` (the default): returns a proposal dict containing
    ``proposal_id`` (or ``None`` if cognee didn't generate one — typically
    when the score was above threshold).

    With ``apply=True``: also calls ``improve_skill(..., apply=True)`` and
    sets ``applied=True`` in the result.
    """
    _require()
    out = cognee_io.run(_record_run_async(
        skill_name=skill_name,
        task_text=task_text,
        result_summary=result_summary,
        success_score=success_score,
        apply=apply,
        score_threshold=score_threshold,
    ))
    state = _read_state()
    state["last_run"] = {
        "ts": time.time(),
        "skill_name": skill_name,
        "task_text": task_text,
        "success_score": success_score,
        "applied": out.get("applied", False),
    }
    if out.get("proposal_id"):
        state["last_proposal"] = {
            "ts": time.time(),
            "skill_name": skill_name,
            "proposal_id": out["proposal_id"],
            "success_score": success_score,
            "applied": out.get("applied", False),
        }
    _write_state(state)
    return out


def apply_proposal(skill_name: str, proposal_id: str) -> None:
    """Apply a previously-proposed rewrite for ``skill_name``."""
    _require()
    cognee_io.run(_apply_proposal_async(skill_name, proposal_id))
    state = _read_state()
    last = state.get("last_proposal")
    if last and last.get("proposal_id") == proposal_id:
        last["applied"] = True
        last["applied_ts"] = time.time()
        state["last_proposal"] = last
    _write_state(state)


def status() -> dict:
    """Return current skill-loop state.

    Does NOT require cognee to be importable — useful for ``wiki doctor``.
    """
    ingested: list[str] = []
    if SKILLS_DIR.is_dir():
        ingested = sorted(
            p.parent.name for p in SKILLS_DIR.glob("*/SKILL.md")
        )
    state = _read_state()
    return {
        "available": SKILL_LOOP_AVAILABLE,
        "dataset": DATASET,
        "session": SESSION,
        "ingested_skills": ingested,
        "last_remember": state.get("last_remember"),
        "last_run": state.get("last_run"),
        "last_proposal": state.get("last_proposal"),
    }
