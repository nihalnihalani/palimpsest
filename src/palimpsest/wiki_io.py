"""Wiki markdown writer. Obsidian-compatible: plain .md + [[wikilinks]]."""
from __future__ import annotations
import re
import time
from pathlib import Path

from .config import CONCEPTS_DIR, LOG_FILE, WIKI_DIR

_SLUG_RE = re.compile(r"[^a-z0-9-]+")
_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def slugify(name: str) -> str:
    s = name.lower().strip().replace(" ", "-")
    s = _SLUG_RE.sub("", s)
    return s[:60] or "untitled"


def concept_path(slug: str) -> Path:
    return CONCEPTS_DIR / f"{slug}.md"


def write_concept(slug: str, title: str, body: str, sources: list[str]) -> Path:
    p = concept_path(slug)
    frontmatter = "---\n" + f"title: {title}\n" + f"updated: {int(time.time())}\n"
    if sources:
        frontmatter += "sources:\n" + "".join(f"  - {s}\n" for s in sources)
    frontmatter += "---\n\n"
    p.write_text(frontmatter + body.strip() + "\n", encoding="utf-8")
    return p


def read_concept(slug: str) -> str | None:
    p = concept_path(slug)
    return p.read_text(encoding="utf-8") if p.exists() else None


def extract_wikilinks(text: str) -> list[str]:
    return [m.group(1).split("|")[0].strip() for m in _WIKILINK_RE.finditer(text)]


def append_log(line: str) -> None:
    WIKI_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line.rstrip() + "\n")


def list_concepts() -> list[str]:
    return sorted(p.stem for p in CONCEPTS_DIR.glob("*.md"))


def find_citations_in_text(text: str) -> list[str]:
    """Pull [[wikilinks]] out of an answer text. Returns sorted unique slugs."""
    out = set()
    for m in _WIKILINK_RE.finditer(text):
        target = slugify(m.group(1).split("|")[0])
        if target:
            out.add(target)
    return sorted(out)


def write_exploration(question: str, answer: str,
                      name: str | None = None,
                      citations: list[str] | None = None,
                      as_of: str | None = None) -> Path:
    """Write a query exploration to wiki/explorations/<slug>.md.
    Obsidian-friendly markdown with frontmatter. Returns the path."""
    from .config import EXPLORATIONS_DIR
    EXPLORATIONS_DIR.mkdir(parents=True, exist_ok=True)
    slug = (name and slugify(name)) or slugify(question[:60]) or f"q-{int(time.time())}"
    # Avoid clobbering: append -2, -3 if file exists
    base = slug
    i = 2
    p = EXPLORATIONS_DIR / f"{slug}.md"
    while p.exists():
        slug = f"{base}-{i}"
        p = EXPLORATIONS_DIR / f"{slug}.md"
        i += 1

    frontmatter = "---\n"
    frontmatter += f"question: {question!r}\n"
    frontmatter += f"asked_at: {int(time.time())}\n"
    if as_of:
        frontmatter += f"as_of: {as_of!r}\n"
    if citations:
        frontmatter += "citations:\n"
        for c in citations:
            frontmatter += f"  - {c}\n"
    frontmatter += "---\n\n"

    body = f"# {question}\n\n{answer.strip()}\n"
    if citations:
        body += "\n## Cited concepts\n"
        for c in citations:
            body += f"- [[{c}]]\n"

    p.write_text(frontmatter + body, encoding="utf-8")
    return p
