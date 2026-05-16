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


if __name__ == "__main__":
    cli()
