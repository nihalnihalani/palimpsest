"""Lint = structural (forked OpenKB pattern) + knowledge (Cognee Cypher)."""
from __future__ import annotations
import re
import time
import unicodedata
from pathlib import Path

from . import wiki_io
from .config import CONCEPTS_DIR, REPORTS_DIR

_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def _normalize_slug_key(slug: str) -> str:
    """Aggressive normalization for fuzzy matching: NFKC + lowercase +
    swap _ and - + strip non-alphanumeric."""
    s = unicodedata.normalize("NFKC", slug).lower()
    s = s.replace("_", "-")
    return re.sub(r"[^a-z0-9-]+", "", s)


def fix_broken_wikilinks() -> dict:
    """Repoint broken [[wikilinks]] via fuzzy slug match, or strip brackets.
    Modifies concept files in place. Returns counts + details."""
    existing = {p.stem for p in CONCEPTS_DIR.glob("*.md")}
    norm_to_real: dict[str, str] = {}
    for s in existing:
        norm_to_real[_normalize_slug_key(s)] = s

    fixed = 0
    stripped = 0
    details: list[str] = []

    for p in CONCEPTS_DIR.glob("*.md"):
        text = p.read_text(encoding="utf-8")
        new_text = text
        for m in _WIKILINK_RE.finditer(text):
            full_link = m.group(0)
            inner = m.group(1)
            label = inner.split("|")[0].strip()
            display = inner.split("|", 1)[1].strip() if "|" in inner else label
            target = wiki_io.slugify(label)
            if target in existing:
                continue   # already valid
            # try fuzzy repoint
            key = _normalize_slug_key(label) or _normalize_slug_key(target)
            real = norm_to_real.get(key)
            if real and real != target:
                replacement = f"[[{real}]]" if display == label else f"[[{real}|{display}]]"
                new_text = new_text.replace(full_link, replacement, 1)
                fixed += 1
                details.append(f"{p.stem}: [[{label}]] → [[{real}]]")
            else:
                # strip brackets, keep display text
                new_text = new_text.replace(full_link, display, 1)
                stripped += 1
                details.append(f"{p.stem}: stripped [[{label}]]")
        if new_text != text:
            # Atomic write: tmp file in same dir, then os.replace.
            # Survives Ctrl-C / kill mid-pass without partial-file corruption.
            tmp = p.with_suffix(p.suffix + ".tmp")
            tmp.write_text(new_text, encoding="utf-8")
            import os
            os.replace(tmp, p)

    return {"fixed": fixed, "stripped": stripped, "details": details}


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
    import os
    from . import config  # noqa: F401 - loads .env
    if (os.environ.get("COGNEE_SERVICE_URL") or "").strip():
        from . import redis_bus
        return redis_bus.list_supersedes_records()
    from . import cognee_io  # lazy: pulls in Cognee
    return cognee_io.run(cognee_io.list_supersedes())


def kg_stats() -> dict:
    import os
    from . import config  # noqa: F401 - loads .env
    if (os.environ.get("COGNEE_SERVICE_URL") or "").strip():
        return {"nodes": 0, "edges": 0}
    from . import cognee_io  # lazy: pulls in Cognee
    return cognee_io.run(cognee_io.graph_stats())


def write_report(fix_result: dict | None = None) -> Path:
    """Write lint report. If fix_result is provided, include a
    'Fixes applied' section with counts + sample details."""
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

    if fix_result:
        lines.append("## Fixes applied\n")
        lines.append(
            f"- repointed {fix_result['fixed']} broken wikilinks "
            f"via fuzzy slug match"
        )
        lines.append(
            f"- stripped {fix_result['stripped']} unresolvable wikilinks"
        )
        for d in (fix_result.get("details") or [])[:10]:
            lines.append(f"  - {d}")
        lines.append("")

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
