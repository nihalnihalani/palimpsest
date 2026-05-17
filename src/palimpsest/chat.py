"""Interactive chat REPL. Multi-turn over the configured LLM with slash commands."""
from __future__ import annotations
import time
from typing import Optional

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from . import chat_session, wiki_io
from .logs import get_logger, event

logger = get_logger(__name__)

SYSTEM_PRIMER = """\
You are the wiki assistant for an LLM-maintained wiki on 'AI agents in 2026'.

Each user message will include a "WIKI SNIPPETS" section containing the
current contents of relevant concept pages. Use those snippets as ground
truth; do not invent tools or function calls — there are no tools available.

Be concise (≤6 sentences per turn unless the user asks for depth). When you
reference a concept covered by a snippet, cite it as [[slug-name]]. If the
provided snippets don't cover the question, say so plainly instead of guessing.
"""

SLASH_HELP = """\
Slash commands:
  /help    — this list
  /status  — show KB stats (wraps `wiki status`)
  /list    — list concept pages (wraps `wiki list`)
  /lint    — run a lint pass + show summary
  /save [name] — export transcript to wiki/explorations/<name>.md
  /clear   — start a fresh session (this one stays saved on disk)
  /exit    — leave (Ctrl-D works too)
"""


def _read_concept_snippets(limit: int = 8) -> dict[str, str]:
    """Snippets of current concept pages used as live context for Gemini."""
    slugs = wiki_io.list_concepts()[:limit]
    return {s: (wiki_io.read_concept(s) or "")[:1200] for s in slugs}


def _send_to_llm(history: list[dict], user_msg: str) -> str:
    """Send one turn to the configured LLM and return the text reply."""
    from . import gemini_io

    snippets = _read_concept_snippets()
    snippets_text = "\n\n".join(f"### [[{s}]]\n{t}" for s, t in snippets.items())
    history_text = "\n".join(
        f"{m.get('role', 'user').upper()}: "
        f"{''.join(m.get('parts', [])) if isinstance(m.get('parts'), list) else m.get('content', '')}"
        for m in history[-10:]
    )
    enriched = (
        f"{SYSTEM_PRIMER}\n\n"
        f"RECENT HISTORY:\n{history_text or '(none)'}\n\n"
        f"WIKI SNIPPETS (for grounding):\n{snippets_text}\n\n"
        f"USER:\n{user_msg}"
    )
    t0 = time.time()
    reply = gemini_io.generate_text(enriched)
    event(logger, "chat.turn", chars=len(reply),
          ms=int((time.time() - t0) * 1000))
    return reply.strip()


def _shell_out(cmd: list[str], console: Console) -> None:
    """Run a `wiki ...` subcommand in a subprocess so we don't re-enter the
    event loop from inside the chat REPL (Cognee's asyncio.run wrapper would
    explode if the chat process already has a loop). 1-2s startup cost; safe."""
    import subprocess
    try:
        result = subprocess.run(["wiki", *cmd], capture_output=True,
                                text=True, timeout=60)
        if result.stdout:
            console.print(result.stdout.rstrip())
        if result.stderr:
            console.print(f"[dim red]{result.stderr.rstrip()}[/dim red]")
    except subprocess.TimeoutExpired:
        console.print(f"[red]`wiki {' '.join(cmd)}` timed out after 60s[/red]")
    except FileNotFoundError:
        console.print("[red]wiki CLI not on PATH (forgot `source .venv/bin/activate`?)[/red]")


def _slash_status(console: Console) -> None:
    _shell_out(["status"], console)


def _slash_list(console: Console) -> None:
    _shell_out(["list"], console)


def _slash_lint(console: Console) -> None:
    _shell_out(["lint"], console)


def _slash_save(session: chat_session.ChatSession, name: Optional[str],
                console: Console) -> None:
    from .config import EXPLORATIONS_DIR
    EXPLORATIONS_DIR.mkdir(parents=True, exist_ok=True)
    fname = (name or session.title or session.id).strip().replace(" ", "-")
    slug = wiki_io.slugify(fname) or session.id
    p = EXPLORATIONS_DIR / f"chat-{slug}.md"
    i = 2
    while p.exists():
        p = EXPLORATIONS_DIR / f"chat-{slug}-{i}.md"
        i += 1
    p.write_text(session.transcript_md(), encoding="utf-8")
    console.print(f"[green]exported transcript → {p}[/green]")


def start_chat(resume: Optional[str] = None) -> None:
    """REPL entrypoint."""
    console = Console()

    if resume:
        sid = chat_session.resolve_id(resume)
        session = chat_session.ChatSession.load(sid)
        console.print(Panel(
            f"resumed session {session.id} ({len(session.messages)} prior messages)",
            style="cyan"))
    else:
        session = chat_session.ChatSession.new()
        console.print(Panel(
            f"new session {session.id}. Type / for commands, Ctrl-D to exit.",
            style="cyan"))

    while True:
        try:
            console.print()
            user_msg = console.input("[bold cyan]you ▸ [/bold cyan]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye.[/dim]")
            break

        if not user_msg:
            continue

        # Slash commands
        if user_msg.startswith("/"):
            cmd, *rest = user_msg.split(maxsplit=1)
            arg = rest[0] if rest else ""
            if cmd in ("/exit", "/quit"):
                console.print("[dim]bye.[/dim]")
                break
            if cmd == "/help":
                console.print(SLASH_HELP)
                continue
            if cmd == "/status":
                _slash_status(console); continue
            if cmd == "/list":
                _slash_list(console); continue
            if cmd == "/lint":
                _slash_lint(console); continue
            if cmd == "/save":
                _slash_save(session, arg or None, console); continue
            if cmd == "/clear":
                session.save()
                session = chat_session.ChatSession.new()
                console.print(Panel(f"new session {session.id}", style="cyan"))
                continue
            console.print(f"[yellow]unknown slash command: {cmd}. Try /help[/yellow]")
            continue

        # Regular turn
        session.append("user", user_msg)
        if not session.title or session.title == "(new chat)":
            session.title = user_msg[:60]
        try:
            reply = _send_to_llm(session.to_gemini_history()[:-1], user_msg)
        except Exception as e:
            console.print(f"[red]llm error:[/red] {e}")
            continue
        session.append("model", reply)
        session.save()
        console.print()
        console.print(Markdown(reply))


def list_sessions() -> None:
    console = Console()
    sessions = chat_session.list_all()
    if not sessions:
        console.print("[yellow]no chat sessions yet[/yellow]")
        return
    from rich.table import Table
    t = Table(title=f"Chat sessions ({len(sessions)})")
    t.add_column("id", style="cyan")
    t.add_column("updated", style="dim")
    t.add_column("turns", justify="right")
    t.add_column("title")
    for s in sessions:
        upd = time.strftime("%Y-%m-%d %H:%M", time.localtime(s.updated))
        t.add_row(s.id, upd, str(len(s.messages)), s.title)
    console.print(t)


def delete_session(prefix: str) -> None:
    console = Console()
    try:
        sid = chat_session.resolve_id(prefix)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        return
    except ValueError as e:
        # Ambiguous prefix — show what matched so the user can pick.
        console.print(f"[yellow]{e}[/yellow]")
        return
    chat_session.delete(sid)
    console.print(f"[green]deleted {sid}[/green]")
