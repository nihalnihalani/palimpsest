"""Chat session model + file persistence.

Sessions live in wiki/chats/<id>.json so Obsidian sees them.
Each session is a list of {role, content} dicts, suitable for both
Gemini's start_chat(history=...) and human reading.
"""
from __future__ import annotations
import json
import secrets
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from .config import WIKI_DIR
from .logs import get_logger

logger = get_logger(__name__)

CHATS_DIR = WIKI_DIR / "chats"


def _new_id() -> str:
    return time.strftime("%Y%m%d-%H%M") + "-" + secrets.token_hex(2)


@dataclass
class ChatSession:
    id: str
    created: float
    updated: float
    title: str
    messages: list[dict]   # [{role: "user"|"model", content: str}, ...]

    @classmethod
    def new(cls, title: str = "(new chat)") -> "ChatSession":
        now = time.time()
        return cls(id=_new_id(), created=now, updated=now,
                   title=title, messages=[])

    @classmethod
    def load(cls, sid: str) -> "ChatSession":
        p = path_for(sid)
        if not p.exists():
            raise FileNotFoundError(f"no chat session: {sid}")
        data = json.loads(p.read_text(encoding="utf-8"))
        return cls(**data)

    def save(self) -> Path:
        CHATS_DIR.mkdir(parents=True, exist_ok=True)
        self.updated = time.time()
        p = path_for(self.id)
        p.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        return p

    def append(self, role: str, content: str) -> None:
        assert role in ("user", "model")
        self.messages.append({"role": role, "content": content})

    def to_gemini_history(self) -> list[dict]:
        """Convert to the shape Gemini's start_chat(history=...) expects.
        Gemini format: [{"role": "user"|"model", "parts": [text]}]"""
        return [{"role": m["role"], "parts": [m["content"]]}
                for m in self.messages]

    def transcript_md(self) -> str:
        """Render the session as markdown for export."""
        lines = [f"# {self.title}", "",
                 f"_id: {self.id}_  _started: "
                 f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(self.created))}_", ""]
        for m in self.messages:
            speaker = "User" if m["role"] == "user" else "Wiki"
            lines.append(f"### {speaker}")
            lines.append(m["content"])
            lines.append("")
        return "\n".join(lines)


def path_for(sid: str) -> Path:
    return CHATS_DIR / f"{sid}.json"


def list_all() -> list[ChatSession]:
    """Return all sessions sorted newest-first."""
    if not CHATS_DIR.exists():
        return []
    sessions: list[ChatSession] = []
    for p in CHATS_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            sessions.append(ChatSession(**data))
        except Exception as e:
            logger.warning(f"skipping unreadable session {p.name}: {e}")
    sessions.sort(key=lambda s: s.updated, reverse=True)
    return sessions


def latest() -> Optional[ChatSession]:
    sessions = list_all()
    return sessions[0] if sessions else None


def resolve_id(prefix: str) -> str:
    """Allow unique-prefix matching for the resume / delete CLI."""
    if prefix in ("latest", "last", ""):
        s = latest()
        if not s:
            raise FileNotFoundError("no sessions yet")
        return s.id
    matches = [s.id for s in list_all() if s.id.startswith(prefix)]
    if not matches:
        raise FileNotFoundError(f"no session matches prefix: {prefix}")
    if len(matches) > 1:
        raise ValueError(f"ambiguous prefix {prefix}: matches {matches}")
    return matches[0]


def delete(sid: str) -> None:
    path_for(sid).unlink(missing_ok=True)
