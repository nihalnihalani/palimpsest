"""wiki — single Click entrypoint."""
from __future__ import annotations
import json
import time

import click


@click.group()
def cli() -> None:
    """wiki — self-correcting LLM wiki (hackathon)"""


# ---- producers ----------------------------------------------------------

@cli.command()
@click.option("--title", required=True)
@click.option("--body", required=True)
@click.option("--source", default="manual")
@click.option("--url", default="")
def inject(title: str, body: str, source: str, url: str) -> None:
    """Push one manual item onto the firehose."""
    from . import redis_bus  # lazy: requires REDIS_URL but no Cognee
    item = {
        "id": redis_bus.sha(title + body),
        "title": title, "body": body, "source": source,
        "url": url, "ts": str(time.time()),
    }
    mid = redis_bus.push_item(item)
    click.echo(f"queued {item['id']} → stream {mid}")


@cli.command("inject-canned")
@click.argument("name")
def inject_canned(name: str) -> None:
    """Inject a pre-prepared canned item (data/canned/<name>.json)."""
    from . import redis_bus  # lazy
    from .config import CANNED_DIR
    p = CANNED_DIR / f"{name}.json"
    item = json.loads(p.read_text())
    mid = redis_bus.push_item(item)
    click.echo(f"queued {item['id']} → stream {mid}")


# ---- workers ------------------------------------------------------------

@cli.command()
@click.option("--once", is_flag=True, help="Process one message then exit.")
def ingest(once: bool) -> None:
    """Run the ingest worker."""
    from . import ingest as ingest_mod  # lazy: pulls in Cognee
    if once:
        n = ingest_mod.run_once(block_ms=2_000)
        click.echo(f"processed {n} item(s)")
    else:
        ingest_mod.run_forever()


# ---- query / graph ------------------------------------------------------

@cli.command()
@click.argument("question")
@click.option("--as-of", default=None,
              help="Reconstruct wiki state at this point: 'now', 'now-5m', "
                   "'pre-ingest', 'first-rewrite', or epoch seconds.")
@click.option("--save", "save", is_flag=True, default=False,
              help="Write Q+A to wiki/explorations/ (Obsidian-visible).")
@click.option("--name", default=None,
              help="Override the exploration filename slug.")
def ask(question: str, as_of: str | None, save: bool, name: str | None) -> None:
    """Ask the wiki a question, optionally as of a past time."""
    if as_of is None:
        from . import query as query_mod  # lazy: pulls in Cognee + Gemini
        answer = query_mod.ask(question)
        click.echo(answer)
        as_of_label = None
    else:
        from . import timemachine  # lazy: pulls in Cognee + Gemini
        result = timemachine.ask_as_of(question, as_of)
        click.echo(
            f"as of {result['as_of_pretty']} "
            f"({len(result['concepts_snapshot'])} concept pages):\n"
        )
        click.echo(result["answer"])
        answer = result["answer"]
        as_of_label = as_of

    if save:
        from . import wiki_io
        citations = wiki_io.find_citations_in_text(answer)
        p = wiki_io.write_exploration(question, answer,
                                      name=name, citations=citations,
                                      as_of=as_of_label)
        click.secho(f"\nsaved → {p}", fg="green")


@cli.group()
def graph() -> None:
    """Inspect the Cognee knowledge graph."""


@graph.command("supersedes")
def graph_supersedes() -> None:
    """List all SUPERSEDES edges. The killer 2-hop hero is built on this."""
    from . import cognee_io  # lazy: pulls in Cognee
    rows = cognee_io.run(cognee_io.list_supersedes())
    if not rows:
        click.echo("(no SUPERSEDES edges yet)")
        return
    for r in rows:
        click.echo(
            f"{r.get('from','?')[:32]} → {r.get('to','?')[:32]}  "
            f"src={r.get('source','')}  reason={r.get('reason','')[:60]}"
        )


@graph.command("stats")
def graph_stats() -> None:
    """Print node + edge counts for the Cognee graph."""
    from . import cognee_io  # lazy
    s = cognee_io.run(cognee_io.graph_stats())
    click.echo(f"nodes={s['nodes']}  edges={s['edges']}")


# ---- rethink (cognee.memify-style self-improvement) --------------------

@cli.command()
def rethink() -> None:
    """Run Cognee memify-style enrichment on the existing graph. No new ingest."""
    from . import rethink as rethink_mod  # lazy: pulls in Cognee + Gemini
    result = rethink_mod.rethink()
    click.echo(f"inspected {result['entities_inspected']} entities")
    click.echo(f"contradictions surfaced: {result['contradictions_found']}")
    click.echo(f"inferred edges written: {result['inferred_edges']}")
    for d in result.get("details", [])[:5]:
        click.echo(f"  - {d}")


# ---- lint --------------------------------------------------------------

@cli.command()
def lint() -> None:
    """Run structural + knowledge lint, write report."""
    from . import lint as lint_mod  # lazy: pulls in Cognee
    p = lint_mod.write_report()
    click.echo(f"wrote {p}")
    click.echo("--- preview ---")
    click.echo(p.read_text())


# ---- seed / reset ------------------------------------------------------

@cli.command()
@click.option("--path", default=None,
              help="JSONL file. Default: data/canned/seed_items.jsonl")
def seed(path: str | None) -> None:
    """Push the seed JSONL onto the stream and drain it."""
    from pathlib import Path
    from . import replay as replay_mod
    from .config import CANNED_DIR
    p = Path(path) if path else (CANNED_DIR / "seed_items.jsonl")
    n = replay_mod.replay(p, pace_sec=0.05)
    click.echo(f"pushed {n} items; draining...")
    m = replay_mod.drain()
    click.echo(f"processed {m} items")


@cli.command("load-baseline")
def load_baseline_cmd() -> None:
    """Copy hand-authored baseline pages into wiki/concepts/ so the demo
    hero target deterministically exists."""
    import shutil
    from pathlib import Path
    from .config import CONCEPTS_DIR
    baseline_dir = Path(__file__).resolve().parents[2] / "snapshot" / "baseline" / "wiki" / "concepts"
    if not baseline_dir.exists():
        click.echo(f"baseline missing at {baseline_dir}", err=True)
        raise SystemExit(1)
    n = 0
    for src in baseline_dir.glob("*.md"):
        dst = CONCEPTS_DIR / src.name
        shutil.copyfile(src, dst)
        n += 1
    click.echo(f"loaded {n} baseline concept pages")


@cli.command()
def reset() -> None:
    """Wipe Redis + Cognee + wiki for a clean demo run."""
    from . import redis_bus, cognee_io
    from .config import CONCEPTS_DIR, LOG_FILE
    redis_bus.reset_streams()
    cognee_io.run(cognee_io.reset())
    for p in CONCEPTS_DIR.glob("*.md"):
        p.unlink()
    if LOG_FILE.exists():
        LOG_FILE.unlink()
    click.echo("reset complete")


# ---- dashboard ---------------------------------------------------------

@cli.command()
def dash() -> None:
    """Live three-pane terminal dashboard."""
    from . import dashboard  # lazy: pulls in rich + Cognee
    dashboard.run()


# ---- doctor ------------------------------------------------------------

@cli.command()
def doctor() -> None:
    """One-shot diagnostic dump for triaging a stuck demo."""
    from . import doctor as doctor_mod
    failures = doctor_mod.run()
    if failures:
        raise SystemExit(1)


# ---- eval --------------------------------------------------------------

@cli.command(name="eval")
def eval_cmd() -> None:
    """Held-out evaluation: shows score and citations."""
    from . import eval as eval_mod  # lazy: pulls in query + Gemini
    r = eval_mod.run()
    click.secho(f"\nQ: {r['question']}\n", bold=True)
    click.echo(r["answer"])
    click.secho(
        f"\nScore: {r['score']}/{r['max']}",
        bold=True,
        fg="green" if r["score"] == r["max"] else "yellow",
    )
    click.echo(f"Found:   {r['found']}")
    click.echo(f"Missing: {r['missing']}")


if __name__ == "__main__":
    cli()
