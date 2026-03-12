"""
State persistence for the orchestrator.

Saves and restores orchestrator state to survive interruptions.
State is a JSON file at output/state.json.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Callable

from filelock import FileLock

from ..config import STATE_FILE

_STATE_LOCK = FileLock(str(STATE_FILE) + ".lock", timeout=30)


@dataclass
class StepState:
    step_number: int
    description: str
    status: str  # "pending" | "implementing" | "reviewing" | "done" | "failed"
    retries: int = 0
    implementor_output: str = ""
    reviewer_output: str = ""
    gate_output: str = ""


@dataclass
class TaskState:
    task_id: str
    branch: str
    status: str  # "analyzing" | "planning" | "executing" | "finalizing" | "done" | "failed"
    steps: list[StepState] = field(default_factory=list)
    current_step: int = 0
    design_doc: str = ""
    plan: str = ""
    total_tokens: int = 0
    started_at: str = ""
    updated_at: str = ""

    def update_timestamp(self):
        self.updated_at = datetime.now().isoformat()


@dataclass
class OrchestratorState:
    current_task: TaskState | None = None
    completed_tasks: list[str] = field(default_factory=list)
    failed_tasks: list[str] = field(default_factory=list)
    total_cost_usd: float = 0.0
    audit_history: dict[str, list[str]] = field(default_factory=dict)
    # Example: {"AU1": ["2026-02-12", "2026-02-26"], "AU2": ["2026-02-14"]}


def _save_state_unlocked(state: OrchestratorState) -> None:
    """Save state to JSON file. Caller must hold _STATE_LOCK."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "current_task": asdict(state.current_task) if state.current_task else None,
        "completed_tasks": state.completed_tasks,
        "failed_tasks": state.failed_tasks,
        "total_cost_usd": state.total_cost_usd,
        "audit_history": state.audit_history,
    }
    STATE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def save_state(state: OrchestratorState) -> None:
    """Save state to JSON file (thread/process-safe)."""
    with _STATE_LOCK:
        _save_state_unlocked(state)


def atomic_state_update(fn: Callable[[OrchestratorState], None]) -> OrchestratorState:
    """Read state, apply fn, write state — all under lock."""
    with _STATE_LOCK:
        state = load_state()
        fn(state)
        _save_state_unlocked(state)
        return state


def load_state() -> OrchestratorState:
    """Load state from JSON file, or return fresh state."""
    if not STATE_FILE.exists():
        return OrchestratorState()

    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        state = OrchestratorState(
            completed_tasks=data.get("completed_tasks", []),
            failed_tasks=data.get("failed_tasks", []),
            total_cost_usd=data.get("total_cost_usd", 0.0),
            audit_history=data.get("audit_history", {}),
        )

        task_data = data.get("current_task")
        if task_data:
            steps = [StepState(**s) for s in task_data.get("steps", [])]
            state.current_task = TaskState(
                task_id=task_data["task_id"],
                branch=task_data["branch"],
                status=task_data["status"],
                steps=steps,
                current_step=task_data.get("current_step", 0),
                design_doc=task_data.get("design_doc", ""),
                plan=task_data.get("plan", ""),
                total_tokens=task_data.get("total_tokens", 0),
                started_at=task_data.get("started_at", ""),
                updated_at=task_data.get("updated_at", ""),
            )

        return state
    except (json.JSONDecodeError, KeyError, TypeError):
        return OrchestratorState()
