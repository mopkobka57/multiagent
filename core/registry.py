"""
Registry & insights manager.

Manages two living documents:
1. registry.md  — task execution log (status, branch, cost, report, summary)
2. agent_insights.md — critical knowledge base for agents

Both are Markdown files designed to be human-readable AND machine-updatable.

Registry operations support per-source registry files:
  - Default: multiagent_specs/registry.md
  - Custom sources: {source_path}/registry.md
Pass registry_path to write to a specific registry.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from filelock import FileLock

from ..config import REGISTRY_FILE, INSIGHTS_FILE, TASK_LOGS_DIR

_INSIGHTS_LOCK = FileLock(str(INSIGHTS_FILE) + ".lock", timeout=30)

# Cache file locks per registry path to avoid recreating them
_lock_cache: dict[str, FileLock] = {}


def _get_lock(registry_path: Path) -> FileLock:
    """Get or create a FileLock for a registry file."""
    key = str(registry_path)
    if key not in _lock_cache:
        _lock_cache[key] = FileLock(key + ".lock", timeout=30)
    return _lock_cache[key]


def _resolve_registry(registry_path: Path | None) -> Path:
    """Resolve registry path, defaulting to REGISTRY_FILE."""
    return registry_path if registry_path is not None else REGISTRY_FILE


# ---------------------------------------------------------------------------
# Registry operations
# ---------------------------------------------------------------------------

def registry_start_task(
    task_id: str,
    title: str,
    branch: str,
    registry_path: Path | None = None,
) -> None:
    """Record a task as started in the registry."""
    reg = _resolve_registry(registry_path)
    with _get_lock(reg):
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        _registry_remove_placeholder(reg)
        _registry_upsert_active(
            reg,
            task_id=task_id,
            title=title,
            status="running",
            branch=branch,
            started=now,
            cost="—",
            report="—",
            summary="In progress...",
        )


def registry_update_status(
    task_id: str,
    status: str,
    summary: str = "",
    cost: str = "",
    registry_path: Path | None = None,
) -> None:
    """Update the status of an active task in the registry."""
    reg = _resolve_registry(registry_path)
    with _get_lock(reg):
        content = reg.read_text(encoding="utf-8")

        pattern = rf"(\| {re.escape(task_id)} \|[^\n]*)"
        match = re.search(pattern, content)
        if not match:
            return

        old_row = match.group(1)
        cells = [c.strip() for c in old_row.split("|")[1:-1]]

        if status:
            cells[2] = f"`{status}`"
        if cost:
            cells[5] = cost
        if summary:
            cells[7] = summary[:80]

        new_row = "| " + " | ".join(cells) + " |"
        content = content.replace(old_row, new_row)
        reg.write_text(content, encoding="utf-8")


def registry_complete_task(
    task_id: str,
    title: str,
    branch: str,
    started: str,
    cost_usd: float,
    summary: str,
    registry_path: Path | None = None,
) -> None:
    """Move a task from Active to Completed section."""
    reg = _resolve_registry(registry_path)
    with _get_lock(reg):
        content = reg.read_text(encoding="utf-8")
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        report_path = f"logs/{task_id}/report.md"
        cost_str = f"${cost_usd:.2f}"

        pattern = rf"\| {re.escape(task_id)} \|[^\n]*\n?"
        content = re.sub(pattern, "", content)

        completed_row = (
            f"| {task_id} | {title[:40]} | `done` | `{branch}` "
            f"| {started} | {now} | {cost_str} | `{report_path}` | {summary[:60]} |"
        )
        content = _insert_after_header(content, "## Completed", completed_row)

        reg.write_text(content, encoding="utf-8")


def registry_fail_task(
    task_id: str,
    title: str,
    branch: str,
    started: str,
    cost_usd: float,
    reason: str,
    status: str = "failed",
    registry_path: Path | None = None,
) -> None:
    """Move a task from Active to Failed section."""
    reg = _resolve_registry(registry_path)
    with _get_lock(reg):
        content = reg.read_text(encoding="utf-8")
        cost_str = f"${cost_usd:.2f}"

        pattern = rf"\| {re.escape(task_id)} \|[^\n]*\n?"
        content = re.sub(pattern, "", content)

        failed_row = (
            f"| {task_id} | {title[:40]} | `{status}` | `{branch}` "
            f"| {started} | {cost_str} | — | {reason[:60]} |"
        )
        content = _insert_after_header(content, "## Failed / Blocked", failed_row)

        reg.write_text(content, encoding="utf-8")


def _registry_remove_placeholder(reg: Path) -> None:
    """Remove the 'no tasks yet' placeholder row."""
    content = reg.read_text(encoding="utf-8")
    content = content.replace(
        "| — | *(no tasks executed yet)* | — | — | — | — | — | — |",
        "",
    )
    reg.write_text(content, encoding="utf-8")


def _registry_upsert_active(
    reg: Path,
    task_id: str,
    title: str,
    status: str,
    branch: str,
    started: str,
    cost: str,
    report: str,
    summary: str,
) -> None:
    """Add or update a row in the Active section only."""
    content = reg.read_text(encoding="utf-8")

    row = (
        f"| {task_id} | {title[:40]} | `{status}` | `{branch}` "
        f"| {started} | {cost} | {report} | {summary[:60]} |"
    )

    # Only match within the Active section, not Completed/Failed
    active_header = "## Active / Recent"
    active_pos = content.find(active_header)
    if active_pos == -1:
        content = _insert_after_header(content, active_header, row)
    else:
        next_section = re.search(r"\n## ", content[active_pos + len(active_header):])
        if next_section:
            active_end = active_pos + len(active_header) + next_section.start()
        else:
            active_end = len(content)

        active_section = content[active_pos:active_end]
        pattern = rf"\| {re.escape(task_id)} \|[^\n]*"
        if re.search(pattern, active_section):
            active_section = re.sub(pattern, row, active_section)
            content = content[:active_pos] + active_section + content[active_end:]
        else:
            content = _insert_after_header(content, active_header, row)

    reg.write_text(content, encoding="utf-8")


def _insert_after_header(content: str, header: str, row: str) -> str:
    """Insert a row after a section's table header (after the |---|---| line)."""
    header_pos = content.find(header)
    if header_pos == -1:
        return content

    sep_pattern = re.compile(r"\|[-| ]+\|", re.MULTILINE)
    match = sep_pattern.search(content, header_pos)
    if not match:
        return content

    insert_pos = match.end()
    content = content[:insert_pos] + "\n" + row + content[insert_pos:]
    return content


# ---------------------------------------------------------------------------
# Task report generation
# ---------------------------------------------------------------------------

def write_task_report(
    task_id: str,
    title: str,
    branch: str,
    summary: str,
    files_changed: list[str],
    cost_usd: float,
    duration_s: float,
    insights: list[str] | None = None,
    issues: list[str] | None = None,
    user_notice: str = "",
) -> Path:
    """Write a structured report for a completed task."""
    report_dir = TASK_LOGS_DIR / task_id
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "report.md"

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    duration_min = duration_s / 60

    files_section = "\n".join(f"- `{f}`" for f in files_changed) if files_changed else "- *(none recorded)*"
    insights_section = "\n".join(f"- {i}" for i in insights) if insights else "- *(none)*"
    issues_section = "\n".join(f"- {i}" for i in issues) if issues else "- *(none)*"

    notice_section = ""
    if user_notice:
        notice_section = f"""
## What to Check

{user_notice}
"""

    report = f"""# Task Report: {task_id}

**Title:** {title}
**Branch:** `{branch}`
**Date:** {now}
**Cost:** ${cost_usd:.2f}
**Duration:** {duration_min:.1f} min

## Summary

{summary}
{notice_section}
## Files Changed

{files_section}

## Issues Encountered

{issues_section}

## New Insights

{insights_section}

---
*Auto-generated by Multi-Agent Orchestrator*
"""

    report_path.write_text(report, encoding="utf-8")
    return report_path


# ---------------------------------------------------------------------------
# Insights operations
# ---------------------------------------------------------------------------

def add_insight(
    category: str,
    insight: str,
    task_id: str = "",
) -> None:
    """
    Add a new CRITICAL insight to agent_insights.md.

    Only call this for genuinely important gotchas that could cause
    agent failures if not known.
    """
    with _INSIGHTS_LOCK:
        content = INSIGHTS_FILE.read_text(encoding="utf-8")
        now = datetime.now().strftime("%Y-%m-%d")
        source = f" *(discovered in {task_id})*" if task_id else ""

        if not insight.startswith("**[CRITICAL]"):
            insight = f"**[CRITICAL] {insight}"

        new_entry = f"\n{insight}{source}\n"

        category_pattern = rf"(## {re.escape(category)}.*?)(\n---|\n## |\Z)"
        match = re.search(category_pattern, content, re.DOTALL)

        if match:
            insert_pos = match.end(1)
            content = content[:insert_pos] + "\n" + new_entry + content[insert_pos:]
        else:
            last_updated_pattern = r"\n\*Last updated:.*"
            new_section = f"\n---\n\n## {category}\n{new_entry}\n"
            content = re.sub(last_updated_pattern, new_section + f"\n*Last updated: {now}*", content)

        content = re.sub(
            r"\*Last updated:.*\*",
            f"*Last updated: {now}*",
            content,
        )

        INSIGHTS_FILE.write_text(content, encoding="utf-8")


def load_insights() -> str:
    """Load the full insights file content for inclusion in agent prompts."""
    if INSIGHTS_FILE.exists():
        return INSIGHTS_FILE.read_text(encoding="utf-8")
    return ""
