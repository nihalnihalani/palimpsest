"""Replay a JSONL file into the firehose stream, then run ingest until drained."""
from __future__ import annotations
import json
import time
from pathlib import Path

from . import ingest as ingest_mod, redis_bus


def replay(jsonl_path: Path, pace_sec: float = 0.2) -> int:
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
    processed = 0
    while True:
        n = ingest_mod.run_once(block_ms=1_500)
        if n == 0:
            break
        processed += n
    return processed
