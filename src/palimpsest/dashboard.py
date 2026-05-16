"""Three-pane rich Layout dashboard. Subscribes to wiki:events for live updates.

Cognee's graph_stats() / list_supersedes() can be slow (>500ms) on first call
and would cause the 2Hz render loop to stutter. We cache both behind a single
5-second time-based cache so the dashboard stays smooth.
"""
from __future__ import annotations
import json
import threading
import time
from collections import deque

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from . import cognee_io, redis_bus, wiki_io
from .config import PUBSUB_CHANNEL

_firehose: deque[str] = deque(maxlen=12)
_audit: deque[str] = deque(maxlen=12)


def _subscribe() -> None:
    ps = redis_bus.client().pubsub()
    ps.subscribe(PUBSUB_CHANNEL)
    for m in ps.listen():
        if m["type"] != "message":
            continue
        try:
            payload = json.loads(m["data"])
        except Exception:
            continue
        slug = payload.get("slug", "?")
        kind = payload.get("type", "?")
        ts = time.strftime("%H:%M:%S")
        _audit.appendleft(f"{ts}  {kind:<8} {slug}")


def _scan_firehose() -> None:
    """Tail wiki:evolution stream once per tick."""
    last = "0"
    while True:
        try:
            entries = redis_bus.client().xrange(
                "wiki:evolution",
                min=f"({last}", max="+", count=20,
            )
            for mid, fields in entries:
                last = mid
                ts = time.strftime("%H:%M:%S")
                _firehose.appendleft(
                    f"{ts}  {fields.get('slug','?'):<24} "
                    f"{fields.get('reason','')[:30]}"
                )
        except Exception:
            pass
        time.sleep(0.5)


def _render(metrics: dict) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="top", ratio=3),
        Layout(name="metrics", size=7),
    )
    layout["top"].split_row(
        Layout(Panel("\n".join(_firehose) or "(idle)",
                     title="Firehose / Evolution")),
        Layout(Panel("\n".join(wiki_io.list_concepts()) or "(no concepts)",
                     title="Concepts")),
        Layout(Panel("\n".join(_audit) or "(silent)",
                     title="Audit (Pub/Sub)")),
    )
    t = Table(show_header=False, expand=True)
    t.add_row("concepts", str(metrics.get("concepts", 0)))
    t.add_row("graph nodes", str(metrics.get("nodes", 0)))
    t.add_row("graph edges", str(metrics.get("edges", 0)))
    t.add_row("SUPERSEDES", str(metrics.get("supersedes", 0)))
    t.add_row("verdict cache", str(metrics.get("verdict_cache_keys", 0)))
    t.add_row("stream length", str(metrics.get("stream_len", 0)))
    layout["metrics"].update(Panel(t, title="Metrics"))
    return layout


def run() -> None:
    threading.Thread(target=_subscribe, daemon=True).start()
    threading.Thread(target=_scan_firehose, daemon=True).start()

    # 5-second cache for Cognee calls. graph_stats() / list_supersedes() can
    # take >500ms; without this the 2Hz render loop stutters visibly.
    _cached_kg: tuple[float, dict, list] | None = None

    def _kg_cached() -> tuple[dict, list]:
        nonlocal _cached_kg
        now = time.time()
        if _cached_kg and now - _cached_kg[0] < 5.0:
            return _cached_kg[1], _cached_kg[2]
        stats = cognee_io.run(cognee_io.graph_stats())
        sups = cognee_io.run(cognee_io.list_supersedes())
        _cached_kg = (now, stats, sups)
        return stats, sups

    console = Console()
    with Live(refresh_per_second=2, console=console, screen=True) as live:
        while True:
            stats, sups = _kg_cached()
            metrics = {
                **stats,
                **redis_bus.metrics(),
                "concepts": len(wiki_io.list_concepts()),
                "supersedes": len(sups),
            }
            live.update(_render(metrics))
            time.sleep(0.5)
