"""All Redis ops in one file. Streams + JSON + Pub/Sub + verdict cache + dedup."""
from __future__ import annotations
import hashlib
import json
import time
from typing import Iterable

import redis

from .config import (
    REDIS_URL, STREAM, GROUP, CONSUMER,
    EVOLUTION_STREAM, PUBSUB_CHANNEL,
    DEDUP_PREFIX, JSON_KEY_PREFIX, VERDICT_PREFIX,
)
from .logs import get_logger, event

logger = get_logger(__name__)
logger.debug(f"redis_bus configured for REDIS_URL={REDIS_URL!r}")

_r: redis.Redis | None = None
SUPERSEDES_KEY = "wiki:supersedes"


def client() -> redis.Redis:
    global _r
    if _r is None:
        _r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    return _r


# ---- Mode detection ------------------------------------------------------
# Mirrors cognee_io.is_cloud_mode(). Reports whether REDIS_URL points at a
# local Redis (docker/brew) or a remote one (Redis Cloud, self-hosted server,
# or any non-localhost endpoint). Used by `wiki vector-smoke` to surface the
# resolved deployment shape.

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"}


def redis_info() -> dict:
    """Parse REDIS_URL into a mode summary without connecting. Safe for
    import-time inspection."""
    from urllib.parse import urlparse
    u = urlparse(REDIS_URL)
    host = (u.hostname or "").lower()
    port = u.port or (6380 if u.scheme == "rediss" else 6379)
    is_local = host in _LOCAL_HOSTS or host == ""
    # Recognise the well-known Redis Cloud host suffixes (just for the human-
    # readable hint; functionally we still treat anything non-local as remote)
    cloud_suffixes = (".redislabs.com", ".redns.redis-cloud.com", ".rediscloud.com")
    is_cloud = any(host.endswith(s) for s in cloud_suffixes)
    return {
        "mode": "local" if is_local else "remote",
        "vendor_hint": "redis_cloud" if is_cloud else (
            "local" if is_local else "remote_non_cloud"),
        "host": host or "(missing)",
        "port": port,
        "tls": u.scheme == "rediss",
        "url_scheme": u.scheme,
    }


def is_remote() -> bool:
    """True if REDIS_URL points at a non-localhost endpoint (cloud or self-hosted server)."""
    return redis_info()["mode"] == "remote"


def ensure_group() -> None:
    try:
        client().xgroup_create(STREAM, GROUP, id="$", mkstream=True)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


def push_item(item: dict) -> str:
    """Producer: push one item onto the firehose stream."""
    fields = {k: (json.dumps(v) if not isinstance(v, str) else v)
              for k, v in item.items()}
    return client().xadd(STREAM, fields, maxlen=10_000, approximate=True)


def claim_items(count: int = 1, block_ms: int = 5_000) -> list[tuple[str, dict]]:
    """Consumer: returns list of (msg_id, fields) or [] on timeout."""
    resp = client().xreadgroup(GROUP, CONSUMER, {STREAM: ">"},
                               count=count, block=block_ms)
    out: list[tuple[str, dict]] = []
    for _stream, msgs in resp or []:
        for mid, fields in msgs:
            out.append((mid, fields))
    return out


def ack(msg_id: str) -> None:
    client().xack(STREAM, GROUP, msg_id)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def mark_seen(key: str, ttl_sec: int = 3600) -> bool:
    """SETNX dedup. Returns True if newly seen, False if duplicate."""
    return bool(client().set(f"{DEDUP_PREFIX}{key}", "1",
                             ex=ttl_sec, nx=True))


def init_concept(slug: str) -> None:
    client().json().set(
        f"{JSON_KEY_PREFIX}{slug}", "$",
        {"current": None, "history": [], "contradictions": []},
        nx=True,
    )


def rewrite_concept(slug: str, new_text: str, reason: str | None = None,
                    source: str | None = None) -> None:
    key = f"{JSON_KEY_PREFIX}{slug}"
    init_concept(slug)
    prev = client().json().get(key, "$.current")
    prev_text = prev[0] if prev else None
    if prev_text:
        client().json().arrappend(key, "$.history",
                                  {"text": prev_text, "replaced_at": time.time()})
    client().json().set(key, "$.current", new_text)
    if reason:
        client().json().arrappend(key, "$.contradictions",
                                  {"reason": reason, "source": source,
                                   "ts": time.time()})
    # Version count = history length + 1 (the new current).
    history = client().json().get(key, "$.history") or [[]]
    history_list = history[0] if isinstance(history, list) and history else []
    version = (len(history_list) if isinstance(history_list, list) else 0) + 1
    event(logger, "redis.rewrite_concept", slug=slug, version=version,
          reason=(reason or "ingest")[:40], source=source or "")
    audit({"slug": slug, "reason": reason or "ingest",
           "source": source or "", "ts": str(time.time())})
    publish({"type": "rewrite" if reason else "ingest", "slug": slug})


def audit(row: dict) -> str:
    fields = {k: str(v) for k, v in row.items()}
    event(logger, "redis.audit", slug=row.get("slug", ""),
          reason=str(row.get("reason", ""))[:40])
    return client().xadd(EVOLUTION_STREAM, fields,
                         maxlen=10_000, approximate=True)


def record_supersedes(
    *,
    from_id: str,
    to_id: str,
    old_claim: str,
    new_claim: str,
    source: str,
    reason: str,
) -> dict:
    row = {
        "from": from_id,
        "to": to_id,
        "old_claim": old_claim,
        "new_claim": new_claim,
        "source": source,
        "reason": reason,
        "ts": time.time(),
    }
    client().lpush(SUPERSEDES_KEY, json.dumps(row))
    client().ltrim(SUPERSEDES_KEY, 0, 999)
    publish({"type": "supersedes", "source": source, "reason": reason})
    event(logger, "redis.supersedes", source=source, reason=reason[:40])
    return row


def list_supersedes_records(limit: int = 100) -> list[dict]:
    rows: list[dict] = []
    for raw in client().lrange(SUPERSEDES_KEY, 0, limit - 1):
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def publish(payload: dict) -> int:
    return client().publish(PUBSUB_CHANNEL, json.dumps(payload))


def verdict_get(key: str) -> dict | None:
    raw = client().get(f"{VERDICT_PREFIX}{key}")
    return json.loads(raw) if raw else None


def verdict_set(key: str, value: dict, ttl_sec: int = 600) -> None:
    client().set(f"{VERDICT_PREFIX}{key}", json.dumps(value), ex=ttl_sec)


def metrics() -> dict:
    c = client()
    return {
        "stream_len": c.xlen(STREAM),
        "evolution_len": c.xlen(EVOLUTION_STREAM),
        "concept_keys": len(list(c.scan_iter(f"{JSON_KEY_PREFIX}*"))),
        "verdict_cache_keys": len(list(c.scan_iter(f"{VERDICT_PREFIX}*"))),
    }


def reset_streams() -> None:
    """Used by demo reset. Wipes streams + consumer groups."""
    c = client()
    for s in (STREAM, EVOLUTION_STREAM):
        try:
            c.xtrim(s, maxlen=0)
        except redis.ResponseError:
            pass
    try:
        c.xgroup_destroy(STREAM, GROUP)
    except redis.ResponseError:
        pass
    for k in c.scan_iter(f"{JSON_KEY_PREFIX}*"):
        c.delete(k)
    for k in c.scan_iter(f"{DEDUP_PREFIX}*"):
        c.delete(k)
    for k in c.scan_iter(f"{VERDICT_PREFIX}*"):
        c.delete(k)
    c.delete(SUPERSEDES_KEY)
