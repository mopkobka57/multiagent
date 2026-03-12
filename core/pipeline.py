"""
Standard task execution pipeline.

Runs a task through the full flow:
  Orchestrator → [Product] → Analyst → Implementor → QG → Reviewer → VT → merge → done

Also contains the audit dispatch and human interaction.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from pathlib import Path

from claude_agent_sdk import (
    query,
    ClaudeAgentOptions,
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
)

from ..config import (
    AUTONOMY_MODE,
    BRANCH_PREFIX,
    DEV_BRANCH,
    HUMAN_CHECKPOINTS,
    MAX_TOKENS_PER_TASK,
    ORCHESTRATOR_MODEL,
    TASK_LOGS_DIR,
)

from .agents import create_agents
from .audit import run_audit_task
from .git import git_run, ensure_dev_branch, create_feature_branch, commit_work, checkout_branch
from .guardrails import enforce_guardrails
from .prompt_builder import build_orchestrator_prompt, load_context
from .quality_gates import run_full_gates, capture_screenshots, run_visual_test, DevServer
from .registry import (
    add_insight,
    registry_complete_task,
    registry_fail_task,
    registry_start_task,
    write_task_report,
)
from .archive import archive_complete, archive_fail
from .retry import resilient_stream, is_rate_limit_error
from .state import OrchestratorState, TaskState, save_state
from .task_loader import Task


# ---------------------------------------------------------------------------
# Exit status signaling (for server-level restart)
# ---------------------------------------------------------------------------

def _write_exit_status(
    task_id: str,
    status: str,
    error: str = "",
    branch: str = "",
    source_id: str = "default",
) -> None:
    """
    Write exit_status.json so the server can detect rate_limited exits
    and auto-restart. File: output/logs/{task_id}/exit_status.json
    """
    import json
    status_file = TASK_LOGS_DIR / task_id / "exit_status.json"
    status_file.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "status": status,
        "error": error[:200],
        "branch": branch,
        "source_id": source_id,
    }
    status_file.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Human interaction
# ---------------------------------------------------------------------------

async def request_human_approval(checkpoint: str, details: str) -> bool:
    """
    Request human approval at a checkpoint.
    Respects AUTONOMY_MODE and HUMAN_CHECKPOINTS config.
    """
    if AUTONOMY_MODE == "autonomous":
        print(f"[AUTO-APPROVE] {checkpoint}")
        return True

    if AUTONOMY_MODE == "batch" and checkpoint != "pr_review":
        print(f"[AUTO-APPROVE] {checkpoint}")
        return True

    if checkpoint not in HUMAN_CHECKPOINTS:
        print(f"[AUTO-APPROVE] {checkpoint} (not in HUMAN_CHECKPOINTS)")
        return True

    print(f"\n{'='*60}")
    print(f"HUMAN CHECKPOINT: {checkpoint}")
    print(f"{'='*60}")
    print(details)
    print(f"{'='*60}")

    while True:
        try:
            response = await asyncio.to_thread(
                input, "Approve? [y/n/details]: "
            )
            response = response.strip().lower()
            if response in ("y", "yes", ""):
                return True
            if response in ("n", "no"):
                return False
            if response in ("d", "details"):
                print(details)
        except EOFError:
            print("[AUTO-APPROVE] non-interactive environment")
            return True


# ---------------------------------------------------------------------------
# Task execution
# ---------------------------------------------------------------------------

async def run_task(
    task: Task,
    state: OrchestratorState,
    source_id: str = "default",
    branch_override: str | None = None,
    skip_branch_cleanup: bool = False,
) -> bool:
    """
    Run a single task through the appropriate pipeline.
    Returns True if completed successfully, False if failed/interrupted.

    branch_override: if set, use this branch instead of creating a new one.
    skip_branch_cleanup: if True, don't checkout auto-dev at the end.
    """
    # Dispatch audit tasks to simplified pipeline
    if task.source == "audit":
        return await run_audit_task(
            task, state,
            branch_override=branch_override,
            skip_branch_cleanup=skip_branch_cleanup,
            source_id=source_id,
        )

    print(f"\n{'='*60}")
    print(f"STARTING TASK: {task.id}")
    print(f"TITLE: {task.title}")
    print(f"PRIORITY: {task.priority:.2f} | COMPLEXITY: {task.complexity}/5")
    print(f"{'='*60}\n")

    # --- Human checkpoint: task selection ---
    approved = await request_human_approval(
        "task_selection",
        f"Next task: [{task.id}] {task.title}\n"
        f"Source: {task.source} | Priority: {task.priority:.2f} | Complexity: {task.complexity}/5\n"
        f"Description: {task.description[:500]}"
    )
    if not approved:
        print("Task rejected by human. Skipping.")
        return False

    # --- Ensure git setup ---
    if branch_override:
        # Group mode: use existing branch, skip creation
        ok, _ = git_run(f"checkout {branch_override}")
        if not ok:
            print(f"FATAL: Cannot checkout branch {branch_override}")
            return False
        branch = branch_override
    else:
        if not ensure_dev_branch():
            print(f"FATAL: Cannot set up {DEV_BRANCH} branch.")
            return False

        ok, branch = create_feature_branch(task.id)
        if not ok:
            print(f"FATAL: Cannot create feature branch: {branch}")
            return False

    print(f"Working on branch: {branch}")

    # --- Resolve source registry ---
    source_registry = None
    if source_id != "default":
        from .sources import get_source_by_id as _get_src
        _src = _get_src(source_id)
        if _src and _src.registry_file.exists():
            source_registry = _src.registry_file

    # --- Record task start in registry ---
    registry_start_task(task_id=task.id, title=task.title, branch=branch,
                        registry_path=source_registry)

    # --- Initialize task state ---
    if not state.current_task or state.current_task.task_id != task.id:
        state.current_task = TaskState(
            task_id=task.id,
            branch=branch,
            status="analyzing",
            started_at=datetime.now().isoformat(),
        )
        save_state(state)

    # --- Create task log directory ---
    task_log_dir = TASK_LOGS_DIR / task.id
    task_log_dir.mkdir(parents=True, exist_ok=True)

    # --- Capture baseline screenshots (before changes) ---
    dev_server = DevServer()
    server_started = await dev_server.start()
    if server_started:
        print("Dev server running — capturing baseline screenshots...")
        _, capture_msg, _ = await capture_screenshots(task.id, "before")
        print(f"  {capture_msg}")
    else:
        print("Warning: Could not start dev server — skipping visual baseline.")

    # --- Compute extra search dirs for custom sources ---
    extra_search_dirs = None
    if source_id != "default":
        from .sources import get_source_by_id
        src = get_source_by_id(source_id)
        if src:
            extra_search_dirs = [Path(src.path)]

    # --- Load context & build prompt ---
    context = load_context()
    prompt = build_orchestrator_prompt(task, context, state, extra_search_dirs=extra_search_dirs)

    # --- Create agents & run orchestrator ---
    agents = create_agents()
    print("Launching Orchestrator agent (autonomous mode)...")
    full_output: list[str] = []

    options = ClaudeAgentOptions(
        allowed_tools=["Task", "Bash", "Read", "Glob", "Grep", "Edit", "Write"],
        agents=agents,
        model=ORCHESTRATOR_MODEL,
        max_turns=MAX_TOKENS_PER_TASK // 10_000,
    )

    def on_message(message):
        """Callback for each message — prints and collects output."""
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(f"[Orchestrator] {block.text}")
                    full_output.append(block.text)
                elif isinstance(block, ToolUseBlock):
                    if block.name == "Task":
                        agent_type = block.input.get("subagent_type", "?")
                        desc = block.input.get("description", "")
                        prompt_preview = block.input.get("prompt", "")[:120]
                        print(f"[Orchestrator] → Delegating to {agent_type}: {desc}")
                        print(f"  Prompt: {prompt_preview}...")
                        full_output.append(f"[DELEGATE → {agent_type}] {desc}: {prompt_preview}")
                    else:
                        input_preview = str(block.input)[:150]
                        print(f"[Orchestrator] → Tool: {block.name} | {input_preview}")
                        full_output.append(f"[TOOL {block.name}] {input_preview}")
                elif isinstance(block, ToolResultBlock):
                    content_str = str(block.content) if block.content else ""
                    is_err = " [ERROR]" if block.is_error else ""
                    preview = content_str[:200]
                    print(f"[Orchestrator] ← Result{is_err}: {preview}")
                    full_output.append(f"[RESULT{is_err}] {preview}")
        elif isinstance(message, ResultMessage):
            if message.total_cost_usd:
                state.total_cost_usd += message.total_cost_usd
                print(f"\n[Cost] Task: ${message.total_cost_usd:.4f} | Total: ${state.total_cost_usd:.4f}")
            if message.result:
                full_output.append(message.result)
        elif isinstance(message, SystemMessage):
            print(f"[System] {message.subtype}: {str(message.data)[:150]}")

    try:
        await resilient_stream(
            query_fn=query,
            prompt=prompt,
            options=options,
            on_message=on_message,
        )

    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Saving state...")
        state.current_task.status = "interrupted"
        state.current_task.update_timestamp()
        save_state(state)
        registry_fail_task(
            task_id=task.id, title=task.title, branch=branch,
            started=state.current_task.started_at,
            cost_usd=state.total_cost_usd, reason="Interrupted by user",
            status="interrupted", registry_path=source_registry,
        )
        archive_fail(
            task_id=task.id, title=task.title, branch=branch,
            started=state.current_task.started_at,
            cost_usd=state.total_cost_usd, reason="Interrupted by user",
            status="interrupted", source_id=source_id,
        )
        ok, commit_msg = commit_work(task.id, branch, success=False)
        print(f"[commit_work] {'OK' if ok else 'FAILED'}: {commit_msg}")
        if not skip_branch_cleanup:
            checkout_branch(DEV_BRANCH)
        await dev_server.stop()
        return False
    except Exception as e:
        if is_rate_limit_error(e):
            print(f"\n\nRate limit exhausted after all retries: {e}")
            state.current_task.status = "rate_limited"
            fail_status = "rate_limited"
            fail_reason = f"Rate limit exhausted: {str(e)[:50]}"
            _write_exit_status(
                task_id=task.id,
                status="rate_limited",
                error=str(e)[:200],
                branch=branch,
                source_id=source_id,
            )
        else:
            print(f"\n\nOrchestrator error: {e}")
            state.current_task.status = "failed"
            fail_status = "failed"
            fail_reason = str(e)[:60]
        state.failed_tasks.append(task.id)
        state.current_task.update_timestamp()
        save_state(state)
        registry_fail_task(
            task_id=task.id, title=task.title, branch=branch,
            started=state.current_task.started_at,
            cost_usd=state.total_cost_usd, reason=fail_reason,
            status=fail_status, registry_path=source_registry,
        )
        archive_fail(
            task_id=task.id, title=task.title, branch=branch,
            started=state.current_task.started_at,
            cost_usd=state.total_cost_usd, reason=fail_reason,
            status=fail_status, source_id=source_id,
        )
        ok, commit_msg = commit_work(task.id, branch, success=False)
        print(f"[commit_work] {'OK' if ok else 'FAILED'}: {commit_msg}")
        if not skip_branch_cleanup:
            checkout_branch(DEV_BRANCH)
        await dev_server.stop()
        return False

    # --- Guardrails: check for protected path violations ---
    guardrails_clean = enforce_guardrails()
    if not guardrails_clean:
        print("WARNING: Agent modified protected files — changes were reverted.")

    # --- Extract and save new insights ---
    combined_output = "\n".join(full_output)
    insight_pattern = r"\[NEW INSIGHT\]:\s*(.+?)\s*[—–-]\s*(.+)"
    for match in re.finditer(insight_pattern, combined_output):
        category = match.group(1).strip()
        insight_text = match.group(2).strip()
        print(f"[INSIGHT] New insight discovered: {category} — {insight_text[:60]}")
        add_insight(category=category, insight=insight_text, task_id=task.id)

    # --- Save execution log ---
    log_content = "\n\n---\n\n".join(full_output)
    (task_log_dir / "execution.log").write_text(log_content, encoding="utf-8")

    # --- Run full quality gates ---
    print("\nRunning final quality gates...")
    gates_passed, gates_output = await run_full_gates()
    (task_log_dir / "gates.log").write_text(gates_output, encoding="utf-8")

    if not gates_passed:
        print(f"Quality gates FAILED:\n{gates_output}")
        state.current_task.status = "failed"
        state.current_task.update_timestamp()
        save_state(state)
        registry_fail_task(
            task_id=task.id, title=task.title, branch=branch,
            started=state.current_task.started_at,
            cost_usd=state.total_cost_usd,
            reason="Quality gates failed (tsc/build)",
            registry_path=source_registry,
        )
        archive_fail(
            task_id=task.id, title=task.title, branch=branch,
            started=state.current_task.started_at,
            cost_usd=state.total_cost_usd,
            reason="Quality gates failed (tsc/build)",
            source_id=source_id,
        )
        ok, commit_msg = commit_work(task.id, branch, success=False)
        print(f"[commit_work] {'OK' if ok else 'FAILED'}: {commit_msg}")
        if not skip_branch_cleanup:
            checkout_branch(DEV_BRANCH)
        await dev_server.stop()
        return False

    print("All code quality gates passed!")

    # --- Visual regression test ---
    if server_started:
        print("Running visual regression test...")
        visual_ok, visual_report = await run_visual_test(task.id)
        (task_log_dir / "visual.log").write_text(visual_report, encoding="utf-8")
        print(f"Visual test: {'PASS' if visual_ok else 'NEEDS REVIEW'}")
        print(visual_report)

    await dev_server.stop()

    # --- Parse structured output from agent ---
    combined_output = "\n".join(full_output)

    # Extract [TASK_SUMMARY] — short one-liner
    summary_match = re.search(r"\[TASK_SUMMARY\]:\s*(.+)", combined_output)
    summary = summary_match.group(1).strip()[:80] if summary_match else ""
    if not summary:
        summary = full_output[-1][:200] if full_output else "Completed"

    # Extract [USER_NOTICE] — what the user should check
    notice_match = re.search(r"\[USER_NOTICE\]:\s*(.+?)(?:\n\[|\Z)", combined_output, re.DOTALL)
    user_notice = notice_match.group(1).strip() if notice_match else ""

    # --- Finalize ---
    started_at = state.current_task.started_at
    duration_s = (datetime.now() - datetime.fromisoformat(started_at)).total_seconds()

    _, files_output = git_run(f"diff --name-only {DEV_BRANCH}...{branch}")
    files_changed = [f for f in files_output.split("\n") if f.strip()]

    write_task_report(
        task_id=task.id,
        title=task.title,
        branch=branch,
        summary=summary,
        files_changed=files_changed,
        cost_usd=state.total_cost_usd,
        duration_s=duration_s,
        user_notice=user_notice,
    )

    registry_complete_task(
        task_id=task.id,
        title=task.title,
        branch=branch,
        started=started_at,
        cost_usd=state.total_cost_usd,
        summary=summary[:60],
        registry_path=source_registry,
    )

    archive_complete(
        task_id=task.id,
        title=task.title,
        branch=branch,
        started=started_at,
        cost_usd=state.total_cost_usd,
        summary=summary[:80],
        user_notice=user_notice,
        source_id=source_id,
    )

    # Commit registry update (it was written after the agent's code commit)
    ok, commit_msg = commit_work(task.id, branch, success=True)
    if not ok:
        print(f"WARNING: commit_work failed: {commit_msg}")
    else:
        print(f"[commit_work] {commit_msg}")

    state.current_task.status = "done"
    state.completed_tasks.append(task.id)
    state.current_task = None
    save_state(state)
    if not skip_branch_cleanup:
        checkout_branch(DEV_BRANCH)
    print(f"Task {task.id} completed successfully! Branch: {branch} (merge manually)")
    return True
