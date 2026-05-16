"""Lint = structural (forked OpenKB pattern) + knowledge (Cognee Cypher)."""
from __future__ import annotations
import re
import time
from pathlib import Path

from . import wiki_io
from .config import CONCEPTS_DIR, REPORTS_DIR

_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def find_broken_wikilinks() -> list[tuple[str, str]]:
    """Pairs of (source_slug, broken_target)."""
    existing = {p.stem for p in CONCEPTS_DIR.glob("*.md")}
    out: list[tuple[str, str]] = []
    for p in CONCEPTS_DIR.glob("*.md"):
        for m in _WIKILINK_RE.finditer(p.read_text(encoding="utf-8")):
            target = wiki_io.slugify(m.group(1).split("|")[0])
            if target and target not in existing:
                out.append((p.stem, target))
    return out


def find_orphans() -> list[str]:
    """Concept pages no other page links to."""
    incoming: dict[str, int] = {p.stem: 0 for p in CONCEPTS_DIR.glob("*.md")}
    for p in CONCEPTS_DIR.glob("*.md"):
        for m in _WIKILINK_RE.finditer(p.read_text(encoding="utf-8")):
            target = wiki_io.slugify(m.group(1).split("|")[0])
            if target in incoming:
                incoming[target] += 1
    return sorted(s for s, n in incoming.items() if n == 0)


def supersedes_summary() -> list[dict]:
    from . import cognee_io  # lazy: pulls in Cognee
    return cognee_io.run(cognee_io.list_supersedes())


def kg_stats() -> dict:
    from . import cognee_io  # lazy: pulls in Cognee
    return cognee_io.run(cognee_io.graph_stats())


def write_report() -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d-%H%M%S")
    p = REPORTS_DIR / f"lint-{ts}.md"

    broken = find_broken_wikilinks()
    orphans = find_orphans()
    supers = supersedes_summary()
    stats = kg_stats()
    pages = len(list(CONCEPTS_DIR.glob("*.md")))

    lines: list[str] = []
    lines.append(f"# Lint Report — {ts}\n")
    lines.append("## Metrics\n")
    lines.append("| metric | value |\n|---|---|")
    lines.append(f"| pages | {pages} |")
    lines.append(f"| graph nodes | {stats['nodes']} |")
    lines.append(f"| graph edges | {stats['edges']} |")
    lines.append(f"| SUPERSEDES edges | {len(supers)} |")
    lines.append(f"| broken wikilinks | {len(broken)} |")
    lines.append(f"| orphan concepts | {len(orphans)} |\n")

    if supers:
        lines.append("## Superseded claims\n")
        for s in supers:
            lines.append(f"- src={s.get('source','')} — {s.get('reason','')[:140]}")
        lines.append("")

    if broken:
        lines.append("## Broken wikilinks\n")
        for src, tgt in broken:
            lines.append(f"- [[{tgt}]] referenced by `{src}.md`")
        lines.append("")

    if orphans:
        lines.append("## Orphan concept pages\n")
        for o in orphans:
            lines.append(f"- `{o}.md`")
        lines.append("")

    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p
