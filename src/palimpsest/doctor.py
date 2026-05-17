"""wiki doctor — single-command diagnostic dump for triaging a stuck demo."""
from __future__ import annotations
import os

import click
import redis

from .config import (
    REDIS_URL, STREAM, GROUP, EVOLUTION_STREAM, JSON_KEY_PREFIX,
    VERDICT_PREFIX, DEDUP_PREFIX, PUBSUB_CHANNEL,
    CONCEPTS_DIR, LOG_FILE, REPORTS_DIR, ROOT,
)


def _hdr(title: str) -> None:
    click.echo(click.style(f"\n== {title} ==", bold=True, fg="cyan"))


def _ok(msg: str) -> None:
    click.echo(click.style(f"  + {msg}", fg="green"))


def _fail(msg: str) -> None:
    click.echo(click.style(f"  x {msg}", fg="red"))


def _info(msg: str) -> None:
    click.echo(f"  - {msg}")


def run() -> int:
    """Returns count of failures."""
    failures = 0

    _hdr("Environment")
    for var in ("GEMINI_API_KEY", "LLM_MODEL", "LLM_PROVIDER",
                "EMBEDDING_PROVIDER", "VECTOR_DB_PROVIDER",
                "GRAPH_DATABASE_PROVIDER", "ENABLE_BACKEND_ACCESS_CONTROL",
                "LOG_LEVEL"):
        val = os.environ.get(var, "")
        if var.endswith("API_KEY") and val:
            val = f"set ({len(val)} chars)"
        elif not val:
            val = "(unset)"
        _info(f"{var}={val}")

    _hdr("Redis health")
    try:
        r = redis.Redis.from_url(REDIS_URL, decode_responses=True,
                                 socket_connect_timeout=2)
        pong = r.ping()
        _ok(f"PING -> {pong} at {REDIS_URL}")
    except Exception as e:
        _fail(f"Redis unreachable: {e}")
        failures += 1
        return failures

    _hdr("Redis state")
    try:
        _info(f"stream {STREAM!r} length: {r.xlen(STREAM)}")
        _info(f"stream {EVOLUTION_STREAM!r} length: {r.xlen(EVOLUTION_STREAM)}")
    except redis.ResponseError as e:
        _info(f"stream lengths unavailable: {e}")

    try:
        groups = r.xinfo_groups(STREAM)
        for g in groups:
            _info(
                f"consumer group {g.get('name')}: "
                f"pending={g.get('pending')}, "
                f"last-delivered={g.get('last-delivered-id')}"
            )
    except redis.ResponseError:
        _info(f"no consumer groups on {STREAM}")

    concept_keys = list(r.scan_iter(f"{JSON_KEY_PREFIX}*", count=200))
    verdict_keys = list(r.scan_iter(f"{VERDICT_PREFIX}*", count=200))
    dedup_keys = list(r.scan_iter(f"{DEDUP_PREFIX}*", count=200))
    _info(f"concept JSON keys: {len(concept_keys)}")
    _info(f"verdict cache keys: {len(verdict_keys)}")
    _info(f"dedup keys: {len(dedup_keys)}")

    _hdr("Kuzu lock holders")
    cognee_service_url = (os.environ.get("COGNEE_SERVICE_URL") or "").strip()
    skip_cognee_graph = False
    from . import kuzu_lock
    database_dirs = kuzu_lock.database_dirs(ROOT)
    holders = kuzu_lock.find_all_holders(database_dirs)
    if not holders:
        _ok("no local Kuzu/Ladybug file holders detected")
    else:
        _fail("local Kuzu/Ladybug graph files are already open")
        failures += 1
        skip_cognee_graph = True
        for h in holders:
            _info(
                f"PID {h['pid']} (PPID {h.get('ppid') or '?'}) "
                f"{h.get('command') or h.get('name') or ''}"
            )
            parent = h.get("parent_command")
            if parent:
                _info(f"  parent: {parent}")
            files = ", ".join(os.path.basename(f) for f in h.get("files", []))
            if files:
                _info(f"  files: {files}")
            dirs = ", ".join(h.get("database_dirs", []))
            if dirs:
                _info(f"  dirs: {dirs}")
        _info("stop those processes before running another local Cognee command")

    _hdr("Cognee cloud" if cognee_service_url else "Cognee graph")
    if skip_cognee_graph:
        _info("skipped because local Kuzu is locked by another process")
    elif cognee_service_url:
        try:
            from . import cloud_smoke
            payload = cloud_smoke.run(timeout_sec=90)
            p = cloud_smoke.write_evidence(payload)
            _ok(
                "remember+recall OK "
                f"(token_found={payload.get('token_found')}, evidence={p})"
            )
        except Exception as e:
            _fail(f"Cognee Cloud smoke failed: {type(e).__name__}: {e}")
            failures += 1
    else:
        try:
            from . import cognee_io
            stats, sups = cognee_io.run(cognee_io.doctor_graph_snapshot())
            _ok(
                f"nodes={stats['nodes']}  edges={stats['edges']}  "
                f"SUPERSEDES={len(sups)}"
            )
            for s in sups[:3]:
                _info(
                    f"  SUPERSEDES: src={s.get('source','')} "
                    f"reason={s.get('reason','')[:60]}"
                )
            # Empty-graph guard: if anyone ran seed, the graph should be non-empty.
            # On stage, a silent empty graph is indistinguishable from a healthy one
            # without this check — and it's a demo-killer.
            page_count = len(list(CONCEPTS_DIR.glob("*.md")))
            if stats["nodes"] == 0 and page_count > 0:
                _fail(
                    f"graph is EMPTY but wiki/concepts/ has {page_count} pages — "
                    f"Cognee data was likely wiped without re-seeding"
                )
                failures += 1
        except Exception as e:
            _fail(f"Cognee unreachable: {type(e).__name__}: {e}")
            failures += 1

    _hdr("Filesystem")
    md_files = list(CONCEPTS_DIR.glob("*.md"))
    _info(f"wiki/concepts/ has {len(md_files)} pages")
    for p in sorted(md_files)[:10]:
        _info(f"  - {p.name}  ({p.stat().st_size} bytes)")
    if LOG_FILE.exists():
        _info("wiki/log.md last 5 lines:")
        lines = LOG_FILE.read_text().splitlines()[-5:]
        for ln in lines:
            _info(f"  {ln}")
    else:
        _info("wiki/log.md does not exist yet")
    reports = sorted(REPORTS_DIR.glob("lint-*.md"))
    _info(f"lint reports: {len(reports)}")

    _hdr("Recent evolution events")
    try:
        entries = r.xrevrange(EVOLUTION_STREAM, count=5)
        for mid, fields in entries:
            _info(
                f"  {mid}  slug={fields.get('slug','?')} "
                f"reason={fields.get('reason','')[:60]}"
            )
        if not entries:
            _info("  (no evolution events yet)")
    except redis.ResponseError:
        _info("  (evolution stream empty)")

    click.echo()
    if failures == 0:
        click.secho("doctor: HEALTHY", bold=True, fg="green")
    else:
        click.secho(f"doctor: {failures} FAILURE(S)", bold=True, fg="red")
    return failures
