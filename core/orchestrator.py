"""
Orchestrator entry points.

High-level commands: run next task, run specific task, batch, resume, list.
Delegates actual execution to pipeline.run_task().
"""

from __future__ import annotations

from ..config import AUTONOMY_MODE, HUMAN_CHECKPOINTS

from .pipeline import run_task
from .state import OrchestratorState, load_state
from .task_loader import Task, load_all_tasks, get_next_actionable


async def run_next_task() -> None:
    """Load tasks, pick the next actionable one, and run it."""
    state = load_state()
    tasks = load_all_tasks(audit_history=state.audit_history)

    # Rate-limited tasks can be retried, so don't exclude them
    permanent_failures = {
        tid for tid in state.failed_tasks
        if not (state.current_task and state.current_task.task_id == tid
                and state.current_task.status == "rate_limited")
    }
    done_ids = set(state.completed_tasks) | permanent_failures
    # Audits are re-runnable (cooldown managed by task_loader), so exclude from done_ids filter
    pending = [
        t for t in tasks
        if (t.id not in done_ids or t.source == "audit") and t.is_actionable
    ]

    if not pending:
        print("No actionable tasks found.")
        return

    next_task = pending[0]
    await run_task(next_task, state)


async def run_specific_task(
    task_id: str,
    source_id: str = "default",
    branch_override: str | None = None,
    skip_branch_cleanup: bool = False,
) -> None:
    """Run a specific task by ID."""
    state = load_state()
    tasks = load_all_tasks(audit_history=state.audit_history)

    task = next((t for t in tasks if t.id == task_id), None)
    if not task:
        print(f"Task '{task_id}' not found. Use --list to see available tasks.")
        return

    await run_task(
        task, state, source_id=source_id,
        branch_override=branch_override,
        skip_branch_cleanup=skip_branch_cleanup,
    )


async def run_batch(phase: str | None = None) -> None:
    """Run multiple tasks sequentially."""
    state = load_state()
    tasks = load_all_tasks(audit_history=state.audit_history)

    done_ids = set(state.completed_tasks + state.failed_tasks)
    pending = [t for t in tasks if t.id not in done_ids and t.is_actionable]

    if phase:
        pending = [t for t in pending if t.phase == phase]

    if not pending:
        print(f"No actionable tasks found{f' for phase {phase}' if phase else ''}.")
        return

    print(f"Batch mode: {len(pending)} tasks to run")
    for i, task in enumerate(pending, 1):
        print(f"\n[{i}/{len(pending)}] {task.id}: {task.title}")
        success = await run_task(task, state)
        if not success:
            print(f"\nTask {task.id} failed/rejected. Stopping batch.")
            break
        # Reload state (it was modified by run_task)
        state = load_state()

    print(f"\nBatch complete. Done: {len(state.completed_tasks)} | Failed: {len(state.failed_tasks)}")


async def resume_task() -> None:
    """Resume an interrupted task."""
    state = load_state()
    if not state.current_task:
        print("No interrupted task to resume.")
        return

    tasks = load_all_tasks(audit_history=state.audit_history)
    task = next((t for t in tasks if t.id == state.current_task.task_id), None)
    if not task:
        print(f"Task '{state.current_task.task_id}' no longer in backlog.")
        return

    print(f"Resuming: {task.id} (status: {state.current_task.status})")
    await run_task(task, state)


def list_tasks() -> None:
    """List all tasks with their status."""
    state = load_state()
    tasks = load_all_tasks(audit_history=state.audit_history)
    done_ids = set(state.completed_tasks)
    failed_ids = set(state.failed_tasks)
    current_id = state.current_task.task_id if state.current_task else None

    print(f"\n{'ID':<25} {'Source':<12} {'Pri':<6} {'Cplx':<6} {'Status':<15} {'Title'}")
    print("-" * 100)

    for t in tasks:
        if t.id in done_ids:
            status = "DONE"
        elif t.id in failed_ids:
            status = "FAILED"
        elif t.id == current_id:
            status = "IN PROGRESS"
        elif t.status == "done":
            status = "done (src)"
        else:
            status = t.status

        print(f"{t.id:<25} {t.source:<12} {t.priority:<6.2f} {t.complexity:<6} {status:<15} {t.title[:40]}")

    print(f"\nTotal: {len(tasks)} | Done: {len(done_ids)} | Failed: {len(failed_ids)}")
    print(f"Autonomy mode: {AUTONOMY_MODE}")
    print(f"Human checkpoints: {HUMAN_CHECKPOINTS or 'none (fully autonomous)'}")
    if state.total_cost_usd > 0:
        print(f"Total cost: ${state.total_cost_usd:.4f}")
