"""
Audit pipeline — simplified task execution for read-only audits.

Audit tasks go through: Orchestrator → Analyst (read-only) → Report → [new tasks]
No git branch, no Implementor, no Quality Gates, no Visual Tester, no Reviewer.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from filelock import FileLock

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
    AUDIT_COOLDOWN_DAYS,
    AUDIT_REPORTS_DIR,
    DEV_BRANCH,
    MAX_TOKENS_PER_TASK,
    ORCHESTRATOR_MODEL,
    SPEC_TYPE_DIRS,
    SPECS_DIR,
    TASK_LOGS_DIR,
    BACKLOG_FILE,
)

from .agents import create_agents
from .git import git_run, ensure_dev_branch, checkout_branch
from .guardrails import enforce_guardrails
from .prompt_builder import (
    build_audit_prompt,
    find_task_spec,
    get_previous_audit_report,
    load_context,
)
from .registry import (
    add_insight,
    registry_complete_task,
    registry_fail_task,
    registry_start_task,
)
from .retry import resilient_stream
from .state import OrchestratorState, TaskState, save_state
from .task_loader import Task


# ---------------------------------------------------------------------------
# Audit findings & task generation
# ---------------------------------------------------------------------------

def parse_audit_findings(output: str) -> list[dict]:
    """Parse [NEW TASK] markers from audit output.

    Accepts both formats:
      [NEW TASK]: type | title | description | origin:XX   (4 fields)
      [NEW TASK]: type | title | origin:XX                 (3 fields, description = title)
    """
    findings = []
    # Try 4-field format first (tolerates **bold**, numbering, extra punctuation)
    marker = r"\*{0,2}\[NEW TASK\]:?\*{0,2}:?\s*"
    pattern_4 = marker + r"(\S+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*origin:(\S+)"
    # 3-field format (no separate description)
    pattern_3 = marker + r"(\S+)\s*\|\s*(.+?)\s*\|\s*origin:(\S+)"

    matched_positions = set()
    seen_titles = set()
    for match in re.finditer(pattern_4, output):
        matched_positions.add(match.start())
        title = match.group(2).strip()
        if title not in seen_titles:
            seen_titles.add(title)
            findings.append({
                "type": match.group(1).strip(),
                "title": title,
                "description": match.group(3).strip(),
                "origin": match.group(4).strip(),
            })
    for match in re.finditer(pattern_3, output):
        if match.start() not in matched_positions:
            title = match.group(2).strip()
            if title not in seen_titles:
                seen_titles.add(title)
                findings.append({
                    "type": match.group(1).strip(),
                    "title": title,
                    "description": title,
                    "origin": match.group(3).strip(),
                })
    return findings


def write_audit_report(task: Task, output: str, date: str) -> Path:
    """Save the full audit report to output/logs/audits/."""
    AUDIT_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = AUDIT_REPORTS_DIR / f"{task.id}_audit_{date}.md"
    report_path.write_text(output, encoding="utf-8")
    return report_path


def _get_existing_task_ids(backlog_path: Path | None = None) -> set[str]:
    """Collect all task IDs already present in backlog.md."""
    path = backlog_path or BACKLOG_FILE
    if not path.exists():
        return set()
    content = path.read_text(encoding="utf-8")
    # Match both plain IDs (BF1) and prefixed IDs (MVP_BF1)
    return set(re.findall(r"\|\s*([A-Z_]{2,}[A-Z]\d+|[A-Z]{2}\d+)\s*\|", content))


def _generate_task_id(task_type: str, existing_ids: set[str], prefix: str = "") -> str:
    """Generate a unique task ID for a given type.

    prefix="MVP" → MVP_BF1, MVP_BF2, ...
    prefix=""    → BF1, BF2, ... (fallback)
    """
    type_prefix_map = {
        "bugfix": "BF",
        "tech-debt": "TD",
        "refactor": "RF",
    }
    type_code = type_prefix_map.get(task_type, "BF")
    for i in range(1, 1000):
        if prefix:
            candidate = f"{prefix}_{type_code}{i}"
        else:
            candidate = f"{type_code}{i}"
        if candidate not in existing_ids:
            return candidate
    return f"{prefix}_{type_code}999" if prefix else f"{type_code}999"


def generate_tasks_from_findings(
    audit_task_id: str,
    findings: list[dict],
    source_id: str = "default",
) -> list[str]:
    """
    Generate stub specs and backlog.md entries from audit findings.
    Returns list of generated task IDs.

    When source_id != "default", specs and backlog entries are written
    to the custom source's directory instead of the default agents_data/.
    """
    if not findings:
        return []

    # Resolve source for paths and prefix
    from .sources import get_source_by_id
    source = get_source_by_id(source_id)
    if source and source_id != "default":
        source_path = Path(source.path)
        backlog_path = Path(source.backlog_file)
        task_prefix = source.task_prefix
        # Type dirs relative to the source folder
        source_type_dirs = {
            "bugfix": source_path / "bugfix",
            "tech-debt": source_path / "tech_debt",
            "refactor": source_path / "refactor",
        }
    else:
        backlog_path = BACKLOG_FILE
        task_prefix = ""
        source_type_dirs = SPEC_TYPE_DIRS

    existing_ids = _get_existing_task_ids(backlog_path)
    generated_ids = []
    phases_entries = []

    for finding in findings:
        task_type = finding["type"]
        task_id = _generate_task_id(task_type, existing_ids, prefix=task_prefix)
        existing_ids.add(task_id)
        generated_ids.append(task_id)

        # Create stub spec in the right subdirectory
        fallback_dir = source_type_dirs.get("bugfix", SPECS_DIR / "bugfix")
        target_dir = source_type_dirs.get(task_type, fallback_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r'[^a-z0-9]+', '-', finding["title"].lower()).strip('-')
        spec_path = target_dir / f"{task_id}-{slug}.md"
        spec_content = (
            f"# {finding['title']}\n\n"
            f"**Task ID:** {task_id}\n"
            f"**Type:** {task_type}\n"
            f"**Spec Status:** stub\n"
            f"**Origin:** {audit_task_id} audit\n"
            f"**Human Input:** auto\n\n"
            f"---\n\n"
            f"## Overview\n\n"
            f"{finding['description']}\n\n"
            f"## Acceptance Criteria\n\n"
            f"- [ ] Issue identified by {audit_task_id} audit is resolved\n"
        )
        spec_path.write_text(spec_content, encoding="utf-8")

        # Prepare backlog.md entry
        source_ru = {"bugfix": "фича", "tech-debt": "техдолг", "refactor": "рефактор"}.get(task_type, "техдолг")
        phases_entries.append(
            f"| {task_id} | {finding['title'][:50]} | {source_ru} | 3 | 2 | high | stub | auto | "
            f"From {audit_task_id}: {finding['description'][:80]} |"
        )

    if phases_entries:
        _append_tasks_to_phases(phases_entries, backlog_path)

    return generated_ids


def _append_tasks_to_phases(entries: list[str], backlog_path: Path | None = None) -> None:
    """Append audit-generated task entries to backlog.md."""
    path = backlog_path or BACKLOG_FILE
    lock = FileLock(str(path) + ".lock", timeout=30)
    with lock:
        if not path.exists():
            return

        content = path.read_text(encoding="utf-8")
        section_header = "## Audit-Generated Tasks"

        if section_header not in content:
            content += (
                f"\n\n---\n\n{section_header}\n\n"
                "| ID | Name | Type | Importance | Complexity | Deleg. | Spec | Human | Description |\n"
                "|---|---|---|---|---|---|---|---|---|\n"
            )

        for entry in entries:
            content += entry + "\n"

        path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Audit task runner
# ---------------------------------------------------------------------------

async def run_audit_task(
    task: Task,
    state: OrchestratorState,
    branch_override: str | None = None,
    skip_branch_cleanup: bool = False,
    source_id: str = "default",
) -> bool:
    """
    Run an audit task through a simplified pipeline.
    No git branch, no Implementor, no QG, no Visual Tester, no Reviewer.
    Result is a report, not code changes.

    branch_override: if set, use this branch instead of auto-dev (group mode).
    skip_branch_cleanup: if True, don't checkout auto-dev at the end.
    """
    from .pipeline import request_human_approval

    print(f"\n{'='*60}")
    print(f"STARTING AUDIT: {task.id}")
    print(f"TITLE: {task.title}")
    print(f"{'='*60}\n")

    # --- Human checkpoint: task selection ---
    approved = await request_human_approval(
        "task_selection",
        f"Audit task: [{task.id}] {task.title}\n"
        f"Source: audit | This will NOT modify code — read-only analysis.\n"
        f"Description: {task.description[:500]}"
    )
    if not approved:
        print("Audit rejected by human. Skipping.")
        return False

    # --- Git setup ---
    if branch_override:
        # Group mode: use existing branch
        ok, output = git_run(f"checkout {branch_override}")
        if not ok:
            print(f"FATAL: Cannot checkout branch {branch_override}: {output}")
            return False
        branch = branch_override
    else:
        # Standalone mode: work on auto-dev
        if not ensure_dev_branch():
            print(f"FATAL: Cannot set up {DEV_BRANCH} branch.")
            return False
        ok, output = git_run(f"checkout {DEV_BRANCH}")
        if not ok:
            print(f"FATAL: Cannot checkout {DEV_BRANCH}: {output}")
            return False
        branch = DEV_BRANCH

    print(f"Working on branch: {branch}" + (" (group mode)" if branch_override else " (no feature branch for audits)"))

    # --- Registry ---
    registry_start_task(task_id=task.id, title=task.title, branch=branch)

    today = datetime.now().strftime("%Y-%m-%d")

    # --- Initialize task state ---
    state.current_task = TaskState(
        task_id=task.id,
        branch=branch,
        status="auditing",
        started_at=datetime.now().isoformat(),
    )
    save_state(state)

    # --- Create task log directory ---
    task_log_dir = TASK_LOGS_DIR / task.id
    task_log_dir.mkdir(parents=True, exist_ok=True)

    # --- Load spec and previous report ---
    spec_path, _, _ = find_task_spec(task.id, "audit")
    spec_content = ""
    if spec_path and spec_path.exists():
        spec_content = spec_path.read_text(encoding="utf-8")
    else:
        print(f"Warning: No spec found for audit {task.id}")

    previous_report = get_previous_audit_report(task.id)

    # --- Build prompt and run ---
    context = load_context()
    prompt = build_audit_prompt(task, context, spec_content, previous_report)

    agents = create_agents()
    print("Launching Orchestrator agent (audit mode)...")
    full_output: list[str] = []

    options = ClaudeAgentOptions(
        allowed_tools=["Task", "Read", "Glob", "Grep", "Bash"],
        agents=agents,
        model=ORCHESTRATOR_MODEL,
        max_turns=MAX_TOKENS_PER_TASK // 10_000,
    )

    def on_message(message):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(f"[Audit] {block.text}")
                    full_output.append(block.text)
                elif isinstance(block, ToolUseBlock):
                    if block.name == "Task":
                        agent_type = block.input.get("subagent_type", "?")
                        desc = block.input.get("description", "")
                        prompt_preview = block.input.get("prompt", "")[:120]
                        print(f"[Audit] → Delegating to {agent_type}: {desc}")
                        print(f"  Prompt: {prompt_preview}...")
                        full_output.append(f"[DELEGATE → {agent_type}] {desc}: {prompt_preview}")
                    else:
                        input_preview = str(block.input)[:150]
                        print(f"[Audit] → Tool: {block.name} | {input_preview}")
                        full_output.append(f"[TOOL {block.name}] {input_preview}")
                elif isinstance(block, ToolResultBlock):
                    content_str = str(block.content) if block.content else ""
                    is_err = " [ERROR]" if block.is_error else ""
                    preview = content_str[:200]
                    print(f"[Audit] ← Result{is_err}: {preview}")
                    full_output.append(f"[RESULT{is_err}] {preview}")
        elif isinstance(message, ResultMessage):
            if message.total_cost_usd:
                state.total_cost_usd += message.total_cost_usd
                print(f"\n[Cost] Audit: ${message.total_cost_usd:.4f} | Total: ${state.total_cost_usd:.4f}")
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
        print("\n\nAudit interrupted by user. Saving state...")
        state.current_task.status = "interrupted"
        state.current_task.update_timestamp()
        save_state(state)
        registry_fail_task(
            task_id=task.id, title=task.title, branch=branch,
            started=state.current_task.started_at,
            cost_usd=state.total_cost_usd, reason="Interrupted by user",
            status="interrupted",
        )
        if not skip_branch_cleanup:
            checkout_branch(DEV_BRANCH)
        return False
    except Exception as e:
        print(f"\n\nAudit error: {e}")
        state.current_task.status = "failed"
        state.current_task.update_timestamp()
        save_state(state)
        registry_fail_task(
            task_id=task.id, title=task.title, branch=branch,
            started=state.current_task.started_at,
            cost_usd=state.total_cost_usd,
            reason=f"Audit error: {str(e)[:60]}",
        )
        if not skip_branch_cleanup:
            checkout_branch(DEV_BRANCH)
        return False

    # --- Guardrails: check for protected path violations ---
    guardrails_clean = enforce_guardrails()
    if not guardrails_clean:
        print("WARNING: Audit agent modified protected files — changes were reverted.")

    # --- Extract insights ---
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

    # --- Write audit report ---
    report_path = write_audit_report(task, combined_output, today)
    print(f"Audit report saved: {report_path}")

    # --- Record audit history (for cooldown) ---
    if task.id not in state.audit_history:
        state.audit_history[task.id] = []
    state.audit_history[task.id].append(today)

    # --- Parse findings and generate new tasks ---
    findings = parse_audit_findings(combined_output)
    if findings:
        print(f"\n{len(findings)} actionable findings → generating tasks...")
        generated_ids = generate_tasks_from_findings(task.id, findings, source_id=source_id)
        print(f"Generated tasks: {', '.join(generated_ids)}")
    else:
        print("No actionable findings (no [NEW TASK] markers).")

    # --- Registry complete ---
    started_at = state.current_task.started_at
    duration_s = (datetime.now() - datetime.fromisoformat(started_at)).total_seconds()

    summary = f"Audit complete. {len(findings)} findings."
    registry_complete_task(
        task_id=task.id,
        title=task.title,
        branch=branch,
        started=started_at,
        cost_usd=state.total_cost_usd,
        summary=summary,
    )

    # Audit is NOT added to completed_tasks — it's re-runnable.
    # Cooldown in task_loader manages availability.
    state.current_task = None
    save_state(state)

    if not skip_branch_cleanup:
        checkout_branch(DEV_BRANCH)

    print(f"\nAudit {task.id} complete! Next run available after {AUDIT_COOLDOWN_DAYS} days.")
    return True
