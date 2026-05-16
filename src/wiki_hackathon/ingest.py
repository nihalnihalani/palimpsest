"""The ingest worker. One message at a time — no overlap (Cognee Kuzu lock)."""
from __future__ import annotations
import json
import time
from typing import Any

from . import cognee_io, gemini_io, redis_bus, wiki_io
from .prompts import CONCEPT_RENDER


def _parse_item(fields: dict[str, str]) -> dict[str, Any]:
    """Stream fields are strings; recover JSON-encoded values if any."""
    out = {}
    for k, v in fields.items():
        try:
            out[k] = json.loads(v)
        except (json.JSONDecodeError, TypeError):
            out[k] = v
    return out


def process_one(item: dict[str, Any]) -> dict[str, Any]:
    """Ingest one item end-to-end. Returns dict with touched slugs +
    self-corrected slugs."""
    from . import query  # local import to avoid cycle on cold start
    text = f"{item.get('title','')}\n\n{item.get('body','')}"
    source = item.get("source", "unknown")
    item_id = item.get("id") or redis_bus.sha(text)

    if not redis_bus.mark_seen(item_id):
        return {"slugs": [], "self_corrected": []}

    cognee_io.run(cognee_io.add(text, source))
    cognee_io.run(cognee_io.cognify())

    # Cognee 0.5.8 dropped SearchType.INSIGHTS; top_concepts now uses
    # TRIPLET_COMPLETION which returns LLM-composed text, so triplet parsing
    # may yield no concepts. Fall back to a direct Gemini extraction.
    concepts = cognee_io.run(cognee_io.top_concepts(text, k=3))
    if not concepts:
        concepts = gemini_io.extract_concepts(
            item.get("title", ""), item.get("body", ""))

    touched: list[str] = []
    self_corrected: list[str] = []
    for name in concepts:
        slug = wiki_io.slugify(name)
        if not slug:
            continue

        # Self-correction check FIRST: does the new item contradict the
        # existing page? If yes, write the SUPERSEDES edge + rewrite.
        verdict = query.check_contradiction(slug, text, item_id=item_id)
        if query.self_improve(slug, verdict, source):
            touched.append(slug)
            self_corrected.append(slug)
            continue

        # Otherwise, normal concept render (could be initial or refinement).
        existing = wiki_io.read_concept(slug) or "(empty)"
        page = gemini_io.generate_text(CONCEPT_RENDER.format(
            title=name, existing=existing, source=source,
            item_title=item.get("title", ""), item_body=item.get("body", ""),
            item_url=item.get("url", ""),
        ))
        wiki_io.write_concept(slug, name, page,
                              sources=[item.get("url") or source])
        redis_bus.rewrite_concept(slug, page, reason=None, source=source)
        touched.append(slug)

    wiki_io.append_log(
        f"[{int(time.time())}] INGEST {item_id} → {touched} "
        f"(self_corrected={self_corrected})"
    )
    return {"slugs": touched, "self_corrected": self_corrected}


def run_once(block_ms: int = 5_000) -> int:
    """Pull one message, process, ack. Returns count processed."""
    redis_bus.ensure_group()
    msgs = redis_bus.claim_items(count=1, block_ms=block_ms)
    if not msgs:
        return 0
    mid, fields = msgs[0]
    try:
        process_one(_parse_item(fields))
    finally:
        redis_bus.ack(mid)
    return 1


def run_forever() -> None:
    redis_bus.ensure_group()
    print("ingest worker running. Ctrl-C to stop.")
    while True:
        try:
            n = run_once(block_ms=5_000)
            if n == 0:
                continue
        except KeyboardInterrupt:
            break
        except Exception as e:  # keep worker alive during demo
            print(f"ingest error (continuing): {e!r}")
            time.sleep(1)
