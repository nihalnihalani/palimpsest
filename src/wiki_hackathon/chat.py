"""Interactive chat REPL. Multi-turn over Gemini with slash commands."""
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
You are the wiki assistant. The user is exploring an LLM-maintained wiki on
'AI agents in 2026'. You have access to its concept pages via the `read_concept`
tool conceptually — when you need wiki content, you should reference it by slug.

Be concise (≤6 sentences per turn unless the user asks for depth). Cite concept
pages in [[wikilinks]] when you use them. If the user asks something the wiki
can't answer, say so plainly.

The user can type these slash commands:
  /help    show all commands
  /status  show KB stats
  /list    list concept pages
  /lint    run a lint pass
  /save    export this transcript to wiki/explorations/
  /clear   start a fresh session (current stays on disk)
  /exit    leave (Ctrl-D also works)
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


def _send_to_gemini(history: list[dict], user_msg: str) -> str:
    """Send one turn to Gemini and return the text reply. We construct a
    fresh chat each turn with full history rather than reusing a long-lived
    chat object — simpler, equally correct, and gives us a clear hook for
    injecting wiki snippets as recent context."""
    import google.generativeai as genai
    from .config import GEMINI_API_KEY
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-3-pro", system_instruction=SYSTEM_PRIMER)
    chat = model.start_chat(history=history)
    # Prepend current wiki snippets to the user message so Gemini stays grounded
    snippets = _read_concept_snippets()
    snippets_text = "\n\n".join(f"### [[{s}]]\n{t}" for s, t in snippets.items())
    enriched = f"WIKI SNIPPETS (for grounding):\n{snippets_text}\n\nUSER:\n{user_msg}"
    t0 = time.time()
    resp = chat.send_message(enriched)
    event(logger, "chat.turn", chars=len(resp.text),
          ms=int((time.time() - t0) * 1000))
    return resp.text.strip()


def _slash_status(console: Console) -> None:
    from .cli import status  # lazy
    from click.testing import CliRunner
    r = CliRunner().invoke(status)
    console.print(r.output)


def _slash_list(console: Console) -> None:
    from .cli import list_cmd
    from click.testing import CliRunner
    r = CliRunner().invoke(list_cmd)
    console.print(r.output)


def _slash_lint(console: Console) -> None:
    from . import lint as lint_mod
    p = lint_mod.write_report()
    console.print(f"[green]wrote {p}[/green]")
    head = p.read_text(encoding="utf-8").splitlines()[:14]
    console.print("\n".join(head))


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
            reply = _send_to_gemini(session.to_gemini_history()[:-1], user_msg)
        except Exception as e:
            console.print(f"[red]gemini error:[/red] {e}")
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
    sid = chat_session.resolve_id(prefix)
    chat_session.delete(sid)
    Console().print(f"[green]deleted {sid}[/green]")
