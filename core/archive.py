"""
Archive — persistent completed/failed run storage outside of git.

Writes to multiagent/output/archive.json so archive entries survive
branch switches (unlike registry.md which lives in git).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from filelock import FileLock

from ..config import ARCHIVE_FILE

_ARCHIVE_LOCK = FileLock(str(ARCHIVE_FILE) + ".lock", timeout=10)


def _load_entries() -> list[dict]:
    if not ARCHIVE_FILE.exists():
        return []
    try:
        return json.loads(ARCHIVE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save_entries(entries: list[dict]) -> None:
    ARCHIVE_FILE.parent.mkdir(parents=True, exist_ok=True)
    ARCHIVE_FILE.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")


def archive_complete(
    task_id: str,
    title: str,
    branch: str,
    started: str,
    cost_usd: float,
    summary: str,
    user_notice: str = "",
    source_id: str = "default",
) -> None:
    """Record a completed task in archive.json."""
    with _ARCHIVE_LOCK:
        entries = _load_entries()
        # Remove any existing entry for this task
        entries = [e for e in entries if e.get("taskId") != task_id]
        entries.append({
            "taskId": task_id,
            "title": title,
            "status": "done",
            "branch": branch,
            "startedAt": started,
            "finishedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "costUsd": cost_usd,
            "summary": summary[:80],
            "userNotice": user_notice,
            "sourceId": source_id,
        })
        _save_entries(entries)


def archive_fail(
    task_id: str,
    title: str,
    branch: str,
    started: str,
    cost_usd: float,
    reason: str,
    status: str = "failed",
    source_id: str = "default",
) -> None:
    """Record a failed/stopped task in archive.json."""
    with _ARCHIVE_LOCK:
        entries = _load_entries()
        entries = [e for e in entries if e.get("taskId") != task_id]
        entries.append({
            "taskId": task_id,
            "title": title,
            "status": status,
            "branch": branch,
            "startedAt": started,
            "finishedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "costUsd": cost_usd,
            "summary": reason[:80],
            "userNotice": "",
            "sourceId": source_id,
        })
        _save_entries(entries)


def load_archive() -> list[dict]:
    """Load all archive entries."""
    with _ARCHIVE_LOCK:
        return _load_entries()
