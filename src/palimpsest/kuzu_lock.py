"""Helpers for diagnosing local Kuzu/Ladybug file lock holders."""
from __future__ import annotations

import subprocess
from importlib.util import find_spec
from pathlib import Path
from typing import Any


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
        ).stdout
    except (FileNotFoundError, OSError):
        return ""


def _ps_commands(pids: set[int]) -> dict[int, dict[str, Any]]:
    if not pids:
        return {}
    out = _run([
        "ps", "-p", ",".join(str(pid) for pid in sorted(pids)),
        "-o", "pid=", "-o", "ppid=", "-o", "command=",
    ])
    rows: dict[int, dict[str, Any]] = {}
    for line in out.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        rows[pid] = {
            "ppid": ppid,
            "command": parts[2] if len(parts) > 2 else "",
        }
    return rows


def find_holders(database_dir: Path) -> list[dict[str, Any]]:
    """Return processes with the local Kuzu graph files open.

    Uses lsof when available. Empty output means either no holder exists or the
    platform cannot report one.
    """
    if not database_dir.exists():
        return []

    out = _run(["lsof", "-F", "pcn", "+D", str(database_dir)])
    by_pid: dict[int, dict[str, Any]] = {}
    current_pid: int | None = None

    for raw in out.splitlines():
        if not raw:
            continue
        tag, value = raw[0], raw[1:]
        if tag == "p":
            try:
                current_pid = int(value)
            except ValueError:
                current_pid = None
                continue
            by_pid.setdefault(current_pid, {"pid": current_pid, "files": []})
        elif tag == "c" and current_pid is not None:
            by_pid.setdefault(
                current_pid, {"pid": current_pid, "files": []}
            )["name"] = value
        elif tag == "n" and current_pid is not None:
            by_pid.setdefault(
                current_pid, {"pid": current_pid, "files": []}
            )["files"].append(value)

    by_pid = {
        pid: holder for pid, holder in by_pid.items()
        if any(
            Path(path).name.startswith(("cognee_graph_kuzu", "cognee_graph_ladybug"))
            for path in holder.get("files", [])
        )
    }
    if not by_pid:
        return []

    ps_rows = _ps_commands(set(by_pid))
    parent_rows = _ps_commands({
        row["ppid"] for row in ps_rows.values()
        if isinstance(row.get("ppid"), int) and row["ppid"] > 1
    })

    holders: list[dict[str, Any]] = []
    for pid, holder in sorted(by_pid.items()):
        row = ps_rows.get(pid, {})
        ppid = row.get("ppid")
        holders.append({
            "pid": pid,
            "ppid": ppid,
            "name": holder.get("name", ""),
            "command": row.get("command") or holder.get("name", ""),
            "parent_command": (
                parent_rows.get(ppid, {}).get("command")
                if isinstance(ppid, int) else None
            ),
            "files": sorted(set(holder.get("files", []))),
        })
    return holders


def database_dirs(root: Path) -> list[Path]:
    """Local graph directories that may exist for this project.

    Current code forces Cognee into the repo-local `.cognee_system`, but older
    runs may have used Cognee's package-local default before config loaded.
    """
    dirs = [root / ".cognee_system" / "databases"]
    spec = find_spec("cognee")
    if spec and spec.origin:
        package_dir = Path(spec.origin).resolve().parent
        dirs.append(package_dir / ".cognee_system" / "databases")

    out: list[Path] = []
    seen: set[Path] = set()
    for path in dirs:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            out.append(resolved)
    return out


def find_all_holders(database_dirs: list[Path]) -> list[dict[str, Any]]:
    holders_by_pid: dict[int, dict[str, Any]] = {}
    for database_dir in database_dirs:
        for holder in find_holders(database_dir):
            pid = holder["pid"]
            existing = holders_by_pid.setdefault(pid, {**holder, "files": []})
            existing["files"] = sorted(set(existing["files"]) | set(holder.get("files", [])))
            existing.setdefault("database_dirs", [])
            existing["database_dirs"].append(str(database_dir))
    return [holders_by_pid[pid] for pid in sorted(holders_by_pid)]
