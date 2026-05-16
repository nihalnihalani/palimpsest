"""Cognee `memify`-style self-improvement pass over the existing graph.

This is the Cognee differentiator that the markdown+grep devil's-advocate
attack can't reproduce: we re-read the graph we already have, ask Gemini to
identify (a) contradictions and (b) implicit relationships among existing
entities, and write new INFERRED edges back into the graph WITHOUT any new
input data.

Two execution paths:

1. **Formal memify path** — if `cognee.memify` is importable on the installed
   Cognee version, we build extraction + enrichment Tasks and let Cognee's
   pipeline runner drive the loop. The extraction task pulls suspect
   neighborhoods; the enrichment task resolves them with Gemini and writes
   edges back via the graph engine.

2. **Manual fallback** — if the API isn't available (or fails at runtime),
   we replicate the same shape by hand: enumerate entities → fetch
   neighborhoods → ask Gemini → write `INFERRED` edges via
   `graph.add_edge(...)`.

Either way the public entrypoint is the synchronous `rethink()` function,
which returns a small stats dict the CLI prints.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from . import cognee_io, gemini_io
from .logs import get_logger, event
from .prompts import RETHINK

logger = get_logger(__name__)


TOP_N_ENTITIES = 8
NEIGHBORHOOD_DISPLAY_LIMIT = 20

# Module-level buffer used by the memify path. memify doesn't surface task
# return values, so the enrichment task pushes its writes here and the outer
# memify driver reads from it.
_MEMIFY_WRITES: list[dict] = []
_MEMIFY_CONTRADICTIONS: list[dict] = []


# ---------------------------------------------------------------------------
# Helpers shared by both paths
# ---------------------------------------------------------------------------

def _entity_label(node_id: str, props: dict) -> str:
    """Pull a human-friendly label out of a node, falling back to its id."""
    for key in ("name", "label", "title", "text"):
        v = (props or {}).get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return str(node_id)


def _format_neighborhood(triples: list[tuple[str, str, str]]) -> str:
    """Turn (other, rel, direction) tuples into the prompt's textual form."""
    lines: list[str] = []
    for other, rel, direction in triples[:NEIGHBORHOOD_DISPLAY_LIMIT]:
        if direction == "out":
            lines.append(f"  - <self> -[{rel or 'RELATED_TO'}]-> {other}")
        else:
            lines.append(f"  - {other} -[{rel or 'RELATED_TO'}]-> <self>")
    return "\n".join(lines) if lines else "  (no relationships)"


def _ask_gemini_for_entity(label: str, neighborhood_text: str) -> dict:
    """Call Gemini once for a single entity. Always returns a dict."""
    try:
        raw = gemini_io.generate_json(
            RETHINK.format(entity=label, neighborhood=neighborhood_text)
        )
    except Exception as exc:  # noqa: BLE001 — Gemini failures shouldn't kill the pass
        return {"contradictions": [], "inferred_edges": [],
                "error": f"gemini: {exc}"}
    if not isinstance(raw, dict):
        return {"contradictions": [], "inferred_edges": []}
    raw.setdefault("contradictions", [])
    raw.setdefault("inferred_edges", [])
    return raw


# ---------------------------------------------------------------------------
# Manual fallback path
# ---------------------------------------------------------------------------

async def _gather_top_entities() -> list[tuple[str, dict, list[tuple[str, str, str]]]]:
    """Return [(node_id, props, neighborhood)] for the top-N most-connected nodes."""
    entities = await cognee_io.list_entities(limit=200)
    enriched: list[tuple[str, dict, list[tuple[str, str, str]]]] = []
    for nid, props in entities:
        try:
            nbh = await cognee_io.list_neighborhood(nid)
        except Exception:
            nbh = []
        if not nbh:
            continue
        enriched.append((nid, props, nbh))
    enriched.sort(key=lambda row: len(row[2]), reverse=True)
    return enriched[:TOP_N_ENTITIES]


async def _manual_rethink() -> dict:
    top = await _gather_top_entities()
    details: list[str] = []
    contradictions_total = 0
    inferred_total = 0

    for nid, props, nbh in top:
        label = _entity_label(nid, props)
        event(logger, "rethink.entity_inspected", label=label,
              node=str(nid)[:32], neighbors=len(nbh))
        nbh_text = _format_neighborhood(nbh)
        result = _ask_gemini_for_entity(label, nbh_text)

        contradictions = result.get("contradictions") or []
        inferred = result.get("inferred_edges") or []
        contradictions_total += len(contradictions)

        for edge in inferred:
            if not isinstance(edge, dict):
                continue
            src = (edge.get("from") or "").strip()
            dst = (edge.get("to") or "").strip()
            rel = (edge.get("rel") or "RELATED_TO").strip()
            reason = (edge.get("reason") or "").strip()
            if not src or not dst:
                continue
            # If Gemini referenced "<self>" or the entity label, anchor to nid
            if src in ("<self>", label):
                src = nid
            if dst in ("<self>", label):
                dst = nid
            try:
                await cognee_io.write_inferred_edge(src, dst, rel, reason)
                event(logger, "rethink.inferred_edge", src=src[:24],
                      dst=dst[:24], rel=rel, reason=reason[:40])
                inferred_total += 1
                details.append(
                    f"{label}: {src[:24]} -[{rel}]-> {dst[:24]} ({reason[:60]})"
                )
            except Exception as exc:  # noqa: BLE001
                details.append(f"{label}: edge write failed — {exc}")

        for c in contradictions:
            if isinstance(c, dict):
                details.append(
                    f"{label}: contradiction — {(c.get('explanation') or '')[:80]}"
                )

    return {
        "entities_inspected": len(top),
        "contradictions_found": contradictions_total,
        "inferred_edges": inferred_total,
        "details": details,
        "path": "manual",
    }


# ---------------------------------------------------------------------------
# Formal cognee.memify path
# ---------------------------------------------------------------------------

def _memify_available() -> bool:
    try:
        import cognee  # noqa: F401
        from cognee.modules.pipelines.tasks.task import Task  # noqa: F401
    except Exception:
        return False
    import cognee as _c
    return callable(getattr(_c, "memify", None))


async def _extract_suspect_neighborhoods(*_args, **_kwargs) -> list[dict]:
    """memify EXTRACTION task: collect the slices Gemini should resolve."""
    top = await _gather_top_entities()
    out = []
    for nid, props, nbh in top:
        out.append({
            "node_id": nid,
            "label": _entity_label(nid, props),
            "neighborhood": nbh,
        })
    return out


async def _resolve_with_gemini(payload: list[dict] | None = None,
                               *_args, **_kwargs) -> list[dict]:
    """memify ENRICHMENT task: ask Gemini per-entity and write INFERRED edges.

    Writes also go to _MEMIFY_WRITES so the outer driver can count them
    (memify doesn't surface task return values to the caller).
    """
    items = payload or []
    writes: list[dict] = []
    for item in items:
        label = item.get("label") or item.get("node_id", "")
        nid = item.get("node_id", "")
        nbh = item.get("neighborhood") or []
        nbh_text = _format_neighborhood(nbh)
        result = _ask_gemini_for_entity(label, nbh_text)
        for edge in result.get("inferred_edges") or []:
            if not isinstance(edge, dict):
                continue
            src = (edge.get("from") or "").strip()
            dst = (edge.get("to") or "").strip()
            rel = (edge.get("rel") or "RELATED_TO").strip()
            reason = (edge.get("reason") or "").strip()
            if not src or not dst:
                continue
            if src in ("<self>", label):
                src = nid
            if dst in ("<self>", label):
                dst = nid
            try:
                await cognee_io.write_inferred_edge(src, dst, rel, reason)
                row = {"src": src, "dst": dst, "rel": rel,
                       "reason": reason, "label": label}
                writes.append(row)
                _MEMIFY_WRITES.append(row)
                event(logger, "rethink.inferred_edge", src=src[:24],
                      dst=dst[:24], rel=rel, reason=reason[:40])
            except Exception:  # noqa: BLE001
                continue
        for c in result.get("contradictions") or []:
            if isinstance(c, dict):
                row = {"contradiction": c.get("explanation", ""),
                       "label": label}
                writes.append(row)
                _MEMIFY_CONTRADICTIONS.append(row)
    return writes


async def _memify_rethink() -> dict:
    """Run the formal cognee.memify pipeline. Falls through to manual on error."""
    import cognee
    from cognee.modules.pipelines.tasks.task import Task

    # Reset the shared buffer so we count this invocation only.
    _MEMIFY_WRITES.clear()
    _MEMIFY_CONTRADICTIONS.clear()

    try:
        await cognee.memify(
            extraction_tasks=[Task(_extract_suspect_neighborhoods)],
            enrichment_tasks=[Task(_resolve_with_gemini)],
            dataset=cognee_io.DATASET,
        )
    except Exception as exc:  # noqa: BLE001 — memify can be picky; fall back
        result = await _manual_rethink()
        result["details"].insert(0, f"memify failed, used manual: {exc}")
        result["path"] = "manual-fallback-from-memify"
        return result

    # The enrichment task pushes to _MEMIFY_WRITES while it runs; read them now.
    details: list[str] = []
    for row in _MEMIFY_WRITES[:10]:
        details.append(
            f"{row['label']}: {row['src'][:24]} -[{row['rel']}]-> "
            f"{row['dst'][:24]} ({row['reason'][:60]})"
        )
    for row in _MEMIFY_CONTRADICTIONS[:5]:
        details.append(
            f"{row['label']}: contradiction — {row['contradiction'][:80]}"
        )

    top = await _gather_top_entities()
    return {
        "entities_inspected": len(top),
        "contradictions_found": len(_MEMIFY_CONTRADICTIONS),
        "inferred_edges": len(_MEMIFY_WRITES),
        "details": details,
        "path": "memify",
    }


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

def rethink() -> dict:
    """Synchronous CLI entrypoint. Picks memify if available, else manual."""
    if _memify_available():
        try:
            return asyncio.run(_memify_rethink())
        except Exception as exc:  # noqa: BLE001
            # Last-ditch fallback so the CLI never blows up
            result = asyncio.run(_manual_rethink())
            result["details"].insert(0, f"memify path raised: {exc}")
            result["path"] = "manual-after-memify-exception"
            return result
    return asyncio.run(_manual_rethink())


__all__ = ["rethink"]
