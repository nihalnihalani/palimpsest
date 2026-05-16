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
def ask(question: str) -> None:
    """Ask the wiki a question (uses Cognee KG + wiki + Gemini)."""
    from . import query as query_mod  # lazy: pulls in Cognee + Gemini
    click.echo(query_mod.ask(question))


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


# ---- lint --------------------------------------------------------------

@cli.command()
def lint() -> None:
    """Run structural + knowledge lint, write report."""
    from . import lint as lint_mod  # lazy: pulls in Cognee
    p = lint_mod.write_report()
    click.echo(f"wrote {p}")
    click.echo("--- preview ---")
    click.echo(p.read_text())


if __name__ == "__main__":
    cli()
