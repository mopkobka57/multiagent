"""
Task loader — parses tasks from backlog.md.

Single source of truth for all tasks (features, tech-debt, refactors, audits).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .. import config
from ..config import BACKLOG_FILE, AUDIT_COOLDOWN_DAYS


@dataclass
class Task:
    id: str
    source: str  # "feature" | "tech-debt" | "refactor" | "audit"
    title: str
    description: str
    priority: float  # 0.0 - 1.0 (normalized)
    complexity: int  # 1-5
    status: str  # "pending" | "in_progress" | "done" | "blocked"
    dependencies: list[str] = field(default_factory=list)
    design_doc: str | None = None
    phase: str | None = None
    delegability: str = "high"  # "high" | "medium" | "low"
    human_input: str = "auto"  # "auto" | "design" | "decision"
    source_id: str = "default"  # Which backlog source this task came from

    @property
    def is_actionable(self) -> bool:
        """Task can be started (not blocked, not done)."""
        return self.status == "pending" and not self.dependencies


# Default type map — can be overridden via multiagent.toml [backlog.type_map]
_DEFAULT_TYPE_MAP = {
    "feature": "feature",
    "tech-debt": "tech-debt",
    "refactor": "refactor",
    "audit": "audit",
}

# Phase importance → priority (earlier phases = higher priority)
# Supports single-char ("1", "A") and multi-char ("1a", "BF", "P0") phases.
# Multi-char phases use the first character for base priority with a small
# secondary-character offset so "1a" < "1b" in ordering.
_PHASE_PRIORITY = {
    "1": 0.95,
    "2": 0.85,
    "3": 0.70,
    "4": 0.55,
    "5": 0.40,
    "X": 0.20,
    # Letter-based phases (MVP backlog)
    "A": 0.95,
    "B": 0.85,
    "C": 0.75,
    "D": 0.65,
    "E": 0.55,
    "F": 0.45,
    "G": 0.35,
    "H": 0.25,
    "I": 0.15,
    "J": 0.10,
    "K": 0.05,
}

_DEFAULT_PHASE_PRIORITY = 0.30


def _resolve_phase_priority(phase: str) -> float:
    """Resolve priority for a phase string of any length.

    Single-char phases use direct lookup. Multi-char phases (e.g. "1a", "BF")
    use the first character for base priority, with subsequent characters
    providing a small descending offset (so "1a" > "1b" > "1c" in priority).
    """
    if not phase:
        return _DEFAULT_PHASE_PRIORITY

    # Direct match (covers single-char and any explicitly registered multi-char)
    if phase in _PHASE_PRIORITY:
        return _PHASE_PRIORITY[phase]

    # Multi-char: use first character as base
    base = _PHASE_PRIORITY.get(phase[0].upper(), _DEFAULT_PHASE_PRIORITY)

    # Small offset from second character: a/1→-0.001, b/2→-0.002, etc.
    if len(phase) >= 2:
        second = phase[1].lower()
        if second.isdigit():
            offset = int(second) * 0.001
        elif second.isalpha():
            offset = (ord(second) - ord('a') + 1) * 0.001
        else:
            offset = 0.0
        base -= offset

    return max(0.01, base)


def load_tasks_for_source(
    backlog_path: str | Path,
    source_id: str = "default",
    audit_history: dict[str, list[str]] | None = None,
) -> list[Task]:
    """
    Load tasks from a specific backlog file.

    Args:
        backlog_path: Path to the backlog.md file.
        source_id: ID of the backlog source these tasks belong to.
        audit_history: Optional dict of {task_id: [date_strings]} for audit cooldown.

    Expected format — markdown tables per phase:
    ## Phase N: Title
    | ID | Name | Type | Importance | Complexity | Deleg. | Spec | Human | Description |
    | AU1 | Visual consistency audit | проверка | 1 | 1 | high | full | auto | ... |
    """
    path = Path(backlog_path)
    if not path.exists():
        return []

    content = path.read_text(encoding="utf-8")
    tasks: list[Task] = []
    current_phase = ""

    for line in content.splitlines():
        phase_match = re.match(r"##\s+Phase\s+(\w+)", line)
        if phase_match:
            current_phase = phase_match.group(1)
            continue

        row_match = re.match(
            r"\|\s*([A-Z][A-Z0-9_]*\d+)\s*\|"  # ID: letters/digits/underscores ending with digit (BF1, MVP_BF1)
            r"\s*(.+?)\s*\|"           # Name
            r"\s*(.+?)\s*\|"           # Type
            r"\s*(\d+)\s*\|"           # Importance
            r"\s*(\d+)\s*\|"           # Complexity
            r"\s*(high|medium|low)\s*\|"  # Delegability
            r"\s*(.+?)\s*\|"           # Spec status
            r"\s*(auto|design|decision)\s*\|"  # Human input
            r"\s*(.*?)\s*\|",          # Description
            line,
        )
        if not row_match:
            continue

        task_id = row_match.group(1)
        title = row_match.group(2).strip()
        task_type_raw = row_match.group(3).strip().lower()
        importance = int(row_match.group(4))
        complexity = int(row_match.group(5))
        delegability = row_match.group(6).strip()
        human_input = row_match.group(8).strip()
        description = row_match.group(9).strip() or title

        type_map = config.TYPE_MAP if config.TYPE_MAP else _DEFAULT_TYPE_MAP
        source = type_map.get(task_type_raw, "feature")

        phase_base = _resolve_phase_priority(current_phase)
        importance_bonus = importance * 0.02
        priority = min(1.0, phase_base + importance_bonus)

        tasks.append(Task(
            id=task_id,
            source=source,
            title=title,
            description=description,
            priority=round(priority, 2),
            complexity=complexity,
            status="pending",
            phase=current_phase,
            delegability=delegability,
            human_input=human_input,
            source_id=source_id,
        ))

    # Apply audit cooldown: recently-run audits become non-actionable
    if audit_history:
        cooldown = timedelta(days=AUDIT_COOLDOWN_DAYS)
        now = datetime.now()
        for task in tasks:
            if task.source != "audit":
                continue
            run_dates = audit_history.get(task.id, [])
            if run_dates:
                last_run = datetime.fromisoformat(run_dates[-1])
                if (now - last_run) < cooldown:
                    task.status = "done"  # on cooldown, not actionable

    tasks.sort(key=lambda t: (-t.priority, t.complexity))
    return tasks


def load_all_tasks(audit_history: dict[str, list[str]] | None = None) -> list[Task]:
    """
    Load tasks from all registered backlog sources (default + custom).
    """
    from .sources import load_sources

    all_tasks: list[Task] = []
    for src in load_sources():
        tasks = load_tasks_for_source(src.backlog_file, src.id, audit_history)
        all_tasks.extend(tasks)

    all_tasks.sort(key=lambda t: (-t.priority, t.complexity))
    return all_tasks


def get_next_actionable(tasks: list[Task]) -> Task | None:
    """Get the highest-priority actionable task."""
    for task in tasks:
        if task.is_actionable:
            return task
    return None
