"""Time-machine query: reconstruct wiki state at a past point in time using
the RedisJSON $.history archive that redis_bus.rewrite_concept maintains."""
from __future__ import annotations
import time
from typing import Optional

from . import cognee_io, gemini_io, redis_bus, wiki_io
from .config import JSON_KEY_PREFIX, EVOLUTION_STREAM
from .logs import get_logger, event
from .prompts import SYNTH_ANSWER

logger = get_logger(__name__)


def _parse_as_of(ref: str) -> float:
    """Accepts:
      - epoch seconds (int/float as string)
      - 'now', 'now-30s', 'now-5m', 'now-1h'
      - 'before-contradictions' / 'pre-ingest' (alias for 0.0 -> reconstruct
        the earliest known state)
      - 'first-rewrite' (just before the first SELF-CORRECT event in evolution stream)
    Returns epoch seconds.
    """
    ref = ref.strip().lower()
    now = time.time()
    if ref in ("now", ""):
        return now
    if ref in ("pre-ingest", "before-contradictions", "0"):
        return 0.0
    if ref == "first-rewrite":
        # Look up earliest entry in EVOLUTION_STREAM whose reason isn't 'ingest'.
        # If Redis is unreachable, fall back to "now" rather than crashing.
        try:
            c = redis_bus.client()
            entries = c.xrange(EVOLUTION_STREAM, min="-", max="+", count=200)
        except Exception:
            return now
        for _mid, fields in entries:
            reason = fields.get("reason", "")
            if reason and reason != "ingest":
                return float(fields.get("ts", now)) - 0.5  # half-second before
        return now
    if ref.startswith("now-"):
        unit = ref[-1]
        try:
            num = float(ref[4:-1])
        except ValueError:
            return now
        if unit == "s":
            return now - num
        if unit == "m":
            return now - num * 60
        if unit == "h":
            return now - num * 3600
    # Try parsing as epoch seconds directly
    try:
        return float(ref)
    except ValueError:
        return now


def snapshot_concept_at(slug: str, as_of_ts: float) -> Optional[str]:
    """Return the markdown that was at wiki/concepts/{slug}.md at as_of_ts,
    using the RedisJSON history archive. Returns None if the concept didn't
    exist yet at as_of_ts."""
    key = f"{JSON_KEY_PREFIX}{slug}"
    c = redis_bus.client()
    if not c.exists(key):
        return None
    # JSON.GET returns a list-wrapped value because of the $ root path
    history = c.json().get(key, "$.history")
    if history and isinstance(history, list) and history:
        history = history[0]
    history = history or []
    # history entries: {"text": "<old md>", "replaced_at": <ts>}
    # Find the most recent entry where replaced_at >= as_of_ts. That entry's
    # text was the *current* text at as_of_ts (it got replaced AFTER as_of_ts).
    candidates = [h for h in history
                  if isinstance(h, dict)
                  and "text" in h
                  and h.get("replaced_at", 0) >= as_of_ts]
    if candidates:
        candidates.sort(key=lambda h: h["replaced_at"])
        return candidates[0]["text"]
    # No archived state newer than as_of_ts -- current text was already there.
    current = c.json().get(key, "$.current")
    if current and isinstance(current, list):
        current = current[0]
    if current is None:
        # The page exists in Redis but $.current is None -- concept exists
        # in name only. Fall back to the on-disk markdown.
        return wiki_io.read_concept(slug)
    return current


def ask_as_of(question: str, ref: str) -> dict:
    as_of_ts = _parse_as_of(ref)
    event(logger, "timemachine.as_of", ref=ref, ts=as_of_ts)
    kg = cognee_io.run(cognee_io.search_completion(question))
    slugs = wiki_io.list_concepts()[:8]
    snippets: dict[str, str] = {}
    for s in slugs:
        snap = snapshot_concept_at(s, as_of_ts)
        if snap is not None:
            snippets[s] = snap[:1500]
    event(logger, "timemachine.snapshot_concepts",
          count=len(snippets), slugs=list(snippets.keys())[:5])
    answer = gemini_io.generate_text(SYNTH_ANSWER.format(
        kg=kg, wiki=snippets, question=question,
    ))
    return {
        "answer": answer,
        "as_of_ts": as_of_ts,
        "as_of_pretty": time.strftime("%Y-%m-%d %H:%M:%S",
                                       time.localtime(as_of_ts))
                        if as_of_ts > 0 else "(beginning of time)",
        "concepts_snapshot": list(snippets.keys()),
    }
