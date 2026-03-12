"""
Spec Groups — sequential execution of related specs on a shared branch.

Persistence: output/groups.json with FileLock for thread safety.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime

from filelock import FileLock

from ..config import OUTPUT_DIR, BRANCH_PREFIX


GROUPS_FILE = OUTPUT_DIR / "groups.json"
_GROUPS_LOCK = FileLock(str(GROUPS_FILE) + ".lock", timeout=10)


@dataclass
class GroupTask:
    task_id: str
    title: str
    source: str       # "feature", "tech-debt", etc.
    source_id: str     # "default" or custom


@dataclass
class GroupTaskResult:
    status: str             # "done" | "failed" | "skipped" | "rate_limited"
    cost_usd: float
    started_at: str
    finished_at: str
    reason: str | None      # Failure reason
    has_changes: bool       # Were there code changes after this task?
    auto_continued: bool    # True if server decided to continue despite failure


@dataclass
class SpecGroup:
    id: str                    # UUID
    name: str                  # User-provided name
    branch: str                # "auto/{slug}"
    tasks: list[GroupTask]     # Ordered list
    current_index: int         # -1=not started, 0..N-1=current, N=done
    status: str                # "idle" | "running" | "paused" | "completed" | "stopped"
    task_results: dict[str, GroupTaskResult] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


def _slugify(name: str) -> str:
    """Convert group name to a branch-safe slug."""
    slug = re.sub(r'[^a-zA-Z0-9]+', '-', name.lower()).strip('-')
    return slug[:40] if slug else "group"


def _group_to_dict(group: SpecGroup) -> dict:
    """Serialize a SpecGroup to a JSON-compatible dict."""
    d = {
        "id": group.id,
        "name": group.name,
        "branch": group.branch,
        "tasks": [asdict(t) for t in group.tasks],
        "current_index": group.current_index,
        "status": group.status,
        "task_results": {
            k: asdict(v) for k, v in group.task_results.items()
        },
        "created_at": group.created_at,
        "updated_at": group.updated_at,
    }
    return d


def _group_from_dict(d: dict) -> SpecGroup:
    """Deserialize a dict into a SpecGroup."""
    tasks = [
        GroupTask(
            task_id=t["task_id"],
            title=t["title"],
            source=t["source"],
            source_id=t["source_id"],
        )
        for t in d.get("tasks", [])
    ]
    task_results = {}
    for k, v in d.get("task_results", {}).items():
        task_results[k] = GroupTaskResult(
            status=v["status"],
            cost_usd=v.get("cost_usd", 0.0),
            started_at=v.get("started_at", ""),
            finished_at=v.get("finished_at", ""),
            reason=v.get("reason"),
            has_changes=v.get("has_changes", False),
            auto_continued=v.get("auto_continued", False),
        )
    return SpecGroup(
        id=d["id"],
        name=d["name"],
        branch=d["branch"],
        tasks=tasks,
        current_index=d.get("current_index", -1),
        status=d.get("status", "idle"),
        task_results=task_results,
        created_at=d.get("created_at", ""),
        updated_at=d.get("updated_at", ""),
    )


def _load_raw() -> list[dict]:
    if not GROUPS_FILE.exists():
        return []
    try:
        return json.loads(GROUPS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save_raw(data: list[dict]) -> None:
    GROUPS_FILE.parent.mkdir(parents=True, exist_ok=True)
    GROUPS_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def load_groups() -> list[SpecGroup]:
    """Load all spec groups from disk."""
    with _GROUPS_LOCK:
        return [_group_from_dict(d) for d in _load_raw()]


def save_groups(groups: list[SpecGroup]) -> None:
    """Save all spec groups to disk."""
    with _GROUPS_LOCK:
        _save_raw([_group_to_dict(g) for g in groups])


def get_group(group_id: str) -> SpecGroup | None:
    """Get a single group by ID."""
    groups = load_groups()
    return next((g for g in groups if g.id == group_id), None)


def create_group(name: str, tasks: list[GroupTask]) -> SpecGroup:
    """Create a new spec group. Auto-generates branch name from slugified name."""
    now = datetime.now().isoformat()
    group = SpecGroup(
        id=str(uuid.uuid4()),
        name=name,
        branch=f"{BRANCH_PREFIX}{_slugify(name)}",
        tasks=tasks,
        current_index=-1,
        status="idle",
        task_results={},
        created_at=now,
        updated_at=now,
    )
    with _GROUPS_LOCK:
        data = _load_raw()
        data.append(_group_to_dict(group))
        _save_raw(data)
    return group


def update_group(group_id: str, **kwargs) -> SpecGroup | None:
    """Update fields on a group. Returns updated group or None if not found."""
    with _GROUPS_LOCK:
        data = _load_raw()
        for i, d in enumerate(data):
            if d["id"] == group_id:
                for key, value in kwargs.items():
                    if key == "tasks" and isinstance(value, list):
                        d["tasks"] = [
                            asdict(t) if isinstance(t, GroupTask) else t
                            for t in value
                        ]
                    elif key == "task_results" and isinstance(value, dict):
                        d["task_results"] = {
                            k: asdict(v) if isinstance(v, GroupTaskResult) else v
                            for k, v in value.items()
                        }
                    else:
                        d[key] = value
                d["updated_at"] = datetime.now().isoformat()
                _save_raw(data)
                return _group_from_dict(d)
        return None


def delete_group(group_id: str) -> bool:
    """Delete a group by ID. Returns True if found and deleted."""
    with _GROUPS_LOCK:
        data = _load_raw()
        new_data = [d for d in data if d["id"] != group_id]
        if len(new_data) == len(data):
            return False
        _save_raw(new_data)
        return True
