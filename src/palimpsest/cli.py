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
@click.option("--fix", "fix", is_flag=True, default=False,
              help="Auto-strip dead wikilinks + fuzzy-repoint case/dash mismatches.")
def lint(fix: bool) -> None:
    """Run structural + knowledge lint, write report."""
    from . import lint as lint_mod  # lazy: pulls in Cognee
    fix_result = None
    if fix:
        fix_result = lint_mod.fix_broken_wikilinks()
        click.secho(
            f"fixed {fix_result['fixed']} broken wikilinks, "
            f"stripped {fix_result['stripped']} unresolvable ones",
            fg="green" if (fix_result["fixed"] + fix_result["stripped"]) > 0 else "yellow")
    p = lint_mod.write_report(fix_result=fix_result)
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

@cli.command(name="list")
def list_cmd() -> None:
    """List all concept pages with metadata (last modified, size, citations)."""
    from rich.console import Console
    from rich.table import Table
    import time
    from .config import CONCEPTS_DIR
    from . import wiki_io

    console = Console()
    pages = sorted(CONCEPTS_DIR.glob("*.md"))
    if not pages:
        console.print("[yellow]no concept pages yet. Run `wiki seed`.[/yellow]")
        return

    t = Table(title=f"Concept pages ({len(pages)})", show_lines=False)
    t.add_column("slug", style="bold cyan")
    t.add_column("size", justify="right")
    t.add_column("modified", style="dim")
    t.add_column("wikilinks", justify="right")
    t.add_column("sources", justify="right", style="dim")

    for p in pages:
        text = p.read_text(encoding="utf-8")
        wikilinks = len(wiki_io.find_citations_in_text(text))
        # count `sources:` items in frontmatter (best effort)
        sources = 0
        for ln in text.splitlines():
            if ln.startswith("  - http") or ln.startswith("  - baseline"):
                sources += 1
        modified = time.strftime("%H:%M:%S",
                                 time.localtime(p.stat().st_mtime))
        t.add_row(p.stem, f"{p.stat().st_size}", modified,
                  str(wikilinks), str(sources))

    console.print(t)


@cli.command()
def status() -> None:
    """Show KB stats: pages, edges, supersedes, stream length, recent log entries."""
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from .config import (CONCEPTS_DIR, REPORTS_DIR, EXPLORATIONS_DIR,
                         LOG_FILE)
    from . import redis_bus, cognee_io

    console = Console()

    # KB stats table
    pages = list(CONCEPTS_DIR.glob("*.md"))
    reports = list(REPORTS_DIR.glob("lint-*.md"))
    explorations = list(EXPLORATIONS_DIR.glob("*.md")) if EXPLORATIONS_DIR.exists() else []

    stats_t = Table(title="Wiki state", show_lines=False)
    stats_t.add_column("metric", style="bold")
    stats_t.add_column("value", justify="right", style="cyan")
    stats_t.add_row("concept pages", str(len(pages)))
    stats_t.add_row("lint reports", str(len(reports)))
    stats_t.add_row("explorations", str(len(explorations)))

    # Cognee graph stats (defensively — may fail if Cognee isn't reachable)
    try:
        gs = cognee_io.run(cognee_io.graph_stats())
        sups = cognee_io.run(cognee_io.list_supersedes())
        stats_t.add_row("graph nodes", str(gs["nodes"]))
        stats_t.add_row("graph edges", str(gs["edges"]))
        stats_t.add_row("SUPERSEDES edges", str(len(sups)))
    except Exception as e:
        stats_t.add_row("[red]Cognee[/red]", f"[red]error: {type(e).__name__}[/red]")

    # Redis stats (defensively)
    try:
        m = redis_bus.metrics()
        stats_t.add_row("firehose stream length", str(m["stream_len"]))
        stats_t.add_row("evolution events", str(m["evolution_len"]))
        stats_t.add_row("verdict cache entries", str(m["verdict_cache_keys"]))
    except Exception as e:
        stats_t.add_row("[red]Redis[/red]", f"[red]error: {type(e).__name__}[/red]")

    console.print(stats_t)

    # Recent log entries
    if LOG_FILE.exists():
        lines = LOG_FILE.read_text(encoding="utf-8").splitlines()[-8:]
        if lines:
            console.print(Panel("\n".join(lines),
                                title="Recent activity (wiki/log.md)",
                                border_style="dim"))
    else:
        console.print("[dim]wiki/log.md is empty[/dim]")


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


# ---- chat --------------------------------------------------------------

@cli.command()
@click.option("--resume", default=None,
              help="Resume a session by id, unique prefix, or 'latest'.")
@click.option("--list", "list_sessions_flag", is_flag=True,
              help="List all chat sessions and exit.")
@click.option("--delete", default=None,
              help="Delete a session by id or unique prefix and exit.")
def chat(resume: str | None, list_sessions_flag: bool,
         delete: str | None) -> None:
    """Interactive multi-turn chat over the wiki."""
    from . import chat as chat_mod
    if list_sessions_flag:
        chat_mod.list_sessions()
        return
    if delete:
        chat_mod.delete_session(delete)
        return
    chat_mod.start_chat(resume=resume)


# ---- vector-smoke (DA-required: prove what vector backend actually loaded) -

@cli.command("vector-smoke")
def vector_smoke_cmd() -> None:
    """Probe cognee's resolved vector backend; write docs/evidence/."""
    from . import vector_probe
    payload = vector_probe.probe()
    p = vector_probe.write_evidence(payload)
    mode = payload.get("cognee_mode", "local")
    mode_color = "magenta" if mode == "cloud" else "cyan"
    click.secho(f"cognee mode:              {mode}", bold=True, fg=mode_color)
    if mode == "cloud":
        click.echo(f"service url:              {payload['cognee_service_url']}")
    click.secho(f"resolved vector provider: {payload['cognee_vector_provider']}",
                bold=True, fg="cyan")
    click.echo(f"cognee version:           {payload['cognee_version']}")
    click.echo(f"vector url:               {payload['cognee_vector_url']}")
    click.echo(f"redis in use for:")
    for use in payload["redis_in_use_for"]:
        click.echo(f"  - {use}")
    click.secho(f"\nwrote {p}", fg="green")


# ---- evidence (run eval N times for noise-resistant before/after) ----------

@cli.command()
@click.option("-n", "runs", default=5, type=int,
              help="How many times to run eval. Default 5.")
@click.option("--label", default="run", type=str,
              help="Persist as docs/evidence/eval_<label>_runs.json.")
def evidence(runs: int, label: str) -> None:
    """Run the held-out eval N times, persist raw + median to docs/evidence/."""
    import json as _json
    from pathlib import Path
    from . import eval as eval_mod

    evidence_dir = Path(__file__).resolve().parents[2] / "docs" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    out: list[dict] = []
    for i in range(runs):
        click.echo(f"run {i + 1}/{runs}…", nl=False)
        r = eval_mod.run()
        out.append(r)
        click.echo(f" score={r['score']}/{r['max']}")
    scores = sorted(r["score"] for r in out)
    median = scores[len(scores) // 2]
    raw_path = evidence_dir / f"eval_{label}_runs.json"
    tmp = raw_path.with_suffix(raw_path.suffix + ".tmp")
    tmp.write_text(_json.dumps(out, indent=2), encoding="utf-8")
    tmp.replace(raw_path)

    summary_path = evidence_dir / "eval_summary.json"
    summary: dict = {}
    if summary_path.exists():
        try:
            summary = _json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            summary = {}
    summary[label] = {
        "n": runs,
        "median_score": median,
        "max_score": out[0]["max"],
        "all_scores": scores,
        "missing_at_median": next(
            (r["missing"] for r in out if r["score"] == median), []),
    }
    tmp = summary_path.with_suffix(summary_path.suffix + ".tmp")
    tmp.write_text(_json.dumps(summary, indent=2), encoding="utf-8")
    tmp.replace(summary_path)

    click.secho(f"\nmedian score: {median}/{out[0]['max']}",
                bold=True,
                fg="green" if median == out[0]["max"] else "yellow")
    click.echo(f"raw:     {raw_path}")
    click.echo(f"summary: {summary_path}")


# ---- improve (the hackathon-required SkillRunEntry propose-apply loop) ----

@cli.command()
@click.option("--remember", "do_remember", is_flag=True,
              help="Ingest ./my_skills into cognee via remember(content_type='skills').")
@click.option("--run", "run_skill_name", default=None,
              help="Run the named skill against --prompt and print the answer.")
@click.option("--prompt", "skill_prompt", default=None,
              help="Prompt to pass when --run is used.")
@click.option("--record", "record_skill_name", default=None,
              help="Record a SkillRunEntry for this skill (propose a rewrite).")
@click.option("--score", "record_score", type=float, default=None,
              help="Score for --record (0..1).")
@click.option("--task-text", "record_task_text", default="",
              help="Free-text task description for --record.")
@click.option("--apply", "apply_proposal_id", default=None,
              help="Apply a previously-proposed rewrite by proposal id.")
@click.option("--status", "show_status", is_flag=True,
              help="Show last proposal + last run.")
def improve(do_remember: bool, run_skill_name: str | None,
            skill_prompt: str | None, record_skill_name: str | None,
            record_score: float | None, record_task_text: str,
            apply_proposal_id: str | None, show_status: bool) -> None:
    """Self-improvement loop (cognee 1.x SkillRunEntry → improve_skill).

    Examples:
        wiki improve --remember
        wiki improve --run code-review --prompt "Review the latest rewrite"
        wiki improve --record code-review --score 0.3 --task-text "..."
        wiki improve --apply <proposal_id>
        wiki improve --status
    """
    from . import skill_loop  # lazy: pulls in cognee 1.x

    if do_remember:
        r = skill_loop.remember_skills()
        click.secho(f"ingested skills → dataset {r.get('dataset_id', '?')[:36]}",
                    fg="green")
        return
    if run_skill_name:
        if not skill_prompt:
            raise click.UsageError("--prompt is required with --run")
        r = skill_loop.run_skill(run_skill_name, skill_prompt)
        click.echo(r.get("answer", str(r)))
        return
    if record_skill_name is not None:
        if record_score is None:
            raise click.UsageError("--score is required with --record")
        r = skill_loop.record_run(
            record_skill_name,
            task_text=record_task_text,
            result_summary="(cli)",
            success_score=record_score,
            apply=False,
        )
        pid = r.get("proposal_id")
        if pid:
            click.secho(f"proposal {pid} ready. Apply with: "
                        f"wiki improve --apply {pid}", fg="cyan")
        else:
            click.echo("(no proposal — score above threshold)")
        return
    if apply_proposal_id:
        # apply_proposal needs the skill name — read it from status
        st = skill_loop.status()
        last = st.get("last_proposal") or {}
        skill = last.get("skill_name")
        if not skill:
            raise click.UsageError(
                "no last_proposal recorded; can't infer skill name")
        skill_loop.apply_proposal(skill, apply_proposal_id)
        click.secho(f"applied {apply_proposal_id} → {skill}", fg="green")
        return
    if show_status:
        import json as _json
        click.echo(_json.dumps(skill_loop.status(), indent=2, default=str))
        return
    raise click.UsageError(
        "pick one: --remember | --run | --record | --apply | --status")


if __name__ == "__main__":
    cli()
