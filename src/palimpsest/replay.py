"""Replay a JSONL file into the firehose stream, then run ingest until drained."""
from __future__ import annotations
import json
import time
from pathlib import Path

from . import ingest as ingest_mod, redis_bus


def replay(jsonl_path: Path, pace_sec: float = 0.2) -> int:
    # Create the consumer group BEFORE pushing items. Otherwise
    # ensure_group()'s id="$" anchors the group at the (current) end of the
    # stream, and XREADGROUP ">" misses everything we push afterward. With the
    # group created up-front on an empty stream, every subsequent xadd is
    # immediately visible to the worker via ">".
    redis_bus.ensure_group()
    n = 0
    with jsonl_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            redis_bus.push_item(item)
            n += 1
            time.sleep(pace_sec)
    return n


def drain() -> int:
    """Ingest until the stream is empty."""
    return ingest_mod.drain(block_ms=1_500)
