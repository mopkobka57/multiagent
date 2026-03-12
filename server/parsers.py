"""
Parsers for the Agent Monitor server.

Reads all data sources (backlog, registry, state, specs, artifacts)
and returns structured dicts for the API layer.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..config import (
    REGISTRY_FILE,
    TASK_LOGS_DIR,
    AUDIT_REPORTS_DIR,
    OUTPUT_DIR,
    SCREENSHOTS_DIR,
)
from ..core.archive import load_archive
from ..core.git import git_run
from ..core.task_loader import load_all_tasks, load_tasks_for_source
from ..core.state import load_state
from ..core.prompt_builder import find_task_spec
from ..core.sources import load_sources, get_source_by_id


def parse_registry_section(
    content: str,
    header: str,
    columns: list[str],
) -> list[dict]:
    """
    Parse a markdown table section from registry.md.

    Args:
        content: Full file content
        header: Section header (e.g. "## Completed")
        columns: Expected column names in order
    Returns:
        List of dicts with column names as keys
    """
    header_pos = content.find(header)
    if header_pos == -1:
        return []

    # Find the next section or end of file
    next_section = re.search(r"\n## ", content[header_pos + len(header):])
    if next_section:
        section = content[header_pos:header_pos + len(header) + next_section.start()]
    else:
        section = content[header_pos:]

    rows = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if len(cells) < len(columns):
            continue
        # Skip header and separator rows
        if cells[0] in ("ID", "----", "") or all(c.startswith("-") for c in cells):
            continue
        if re.match(r"^-+$", cells[0]):
            continue
        row = {}
        for i, col in enumerate(columns):
            row[col] = cells[i] if i < len(cells) else ""
        rows.append(row)

    return rows


def _get_all_registry_paths() -> list[Path]:
    """Collect registry.md paths from all sources (default + custom)."""
    paths = []
    if REGISTRY_FILE.exists():
        paths.append(REGISTRY_FILE)
    for src in load_sources():
        if src.is_default:
            continue
        reg = src.registry_file
        if reg.exists() and reg != REGISTRY_FILE:
            paths.append(reg)
    return paths


def parse_completed_runs() -> list[dict]:
    """Parse Completed sections from all source registries."""
    rows = []
    for reg_path in _get_all_registry_paths():
        content = reg_path.read_text(encoding="utf-8")
        rows.extend(parse_registry_section(
            content,
            "## Completed",
            ["id", "title", "status", "branch", "started", "finished", "cost", "report", "summary"],
        ))
    return rows


def parse_failed_runs() -> list[dict]:
    """Parse Failed/Blocked sections from all source registries."""
    rows = []
    for reg_path in _get_all_registry_paths():
        content = reg_path.read_text(encoding="utf-8")
        rows.extend(parse_registry_section(
            content,
            "## Failed / Blocked",
            ["id", "title", "status", "branch", "started", "cost", "report", "reason"],
        ))
    return rows


def discover_artifacts(task_id: str) -> list[dict]:
    """Scan output/logs/{task_id}/ and related dirs for artifact files."""
    artifacts = []

    # Task log directory
    task_dir = TASK_LOGS_DIR / task_id
    if task_dir.exists():
        for f in sorted(task_dir.iterdir()):
            if f.is_file() and not f.name.startswith("."):
                file_type = "markdown" if f.suffix == ".md" else "text"
                artifacts.append({
                    "name": f.stem.replace("_", " ").replace("-", " ").title(),
                    "path": f"logs/{task_id}/{f.name}",
                    "type": file_type,
                })

    # Audit reports
    if AUDIT_REPORTS_DIR.exists():
        for f in sorted(AUDIT_REPORTS_DIR.glob(f"{task_id}_audit_*.md")):
            artifacts.append({
                "name": "Audit Report",
                "path": f"logs/audits/{f.name}",
                "type": "markdown",
            })

    # Screenshots
    screenshots_dir = SCREENSHOTS_DIR / task_id
    if screenshots_dir.exists():
        for f in sorted(screenshots_dir.iterdir()):
            if f.suffix.lower() in (".png", ".jpg", ".jpeg"):
                artifacts.append({
                    "name": f.stem.replace("_", " ").title(),
                    "path": f"screenshots/{task_id}/{f.name}",
                    "type": "image",
                })

    return artifacts


def _clean_backticks(val: str) -> str:
    """Remove surrounding backticks from registry values."""
    return val.strip("`").strip()


def get_enriched_tasks() -> list[dict]:
    """
    Build enriched task list for GET /api/tasks.
    Combines backlog data from all sources with state/registry status.
    """
    state = load_state()
    sources = load_sources()

    # Load tasks from all sources
    all_tasks = []
    for src in sources:
        tasks = load_tasks_for_source(
            src.backlog_file, src.id, audit_history=state.audit_history
        )
        all_tasks.extend(tasks)

    # Determine done/failed from state + archive + registries
    done_ids = set(state.completed_tasks)
    failed_ids = set(state.failed_tasks)

    # Primary: check archive.json (survives branch switches)
    for entry in load_archive():
        tid = entry.get("taskId", "")
        status = entry.get("status", "")
        if status == "done":
            done_ids.add(tid)
        elif status in ("failed", "stopped", "interrupted", "rate_limited"):
            failed_ids.add(tid)

    # Fallback: also check git-tracked registries (for historical data)
    for row in parse_completed_runs():
        done_ids.add(row["id"])
    for row in parse_failed_runs():
        failed_ids.add(row["id"])

    # Merged branches and existing branches (for merge status icons)
    merged_branches = _get_merged_branches()
    existing_branches = _get_local_branches()

    # Current task from state
    current_task_id = None
    if state.current_task:
        current_task_id = state.current_task.task_id

    result = []
    for task in all_tasks:
        # For non-default sources, pass the source folder as extra search dir
        extra_dirs = None
        if task.source_id != "default":
            src = get_source_by_id(task.source_id)
            if src:
                extra_dirs = [Path(src.path)]

        spec_path, spec_status, spec_type = find_task_spec(
            task.id, task.source, extra_search_dirs=extra_dirs,
        )

        is_done = task.id in done_ids or task.status == "done"
        is_failed = task.id in failed_ids
        is_running = task.id == current_task_id
        on_cooldown = (
            task.source == "audit"
            and task.id in state.audit_history
            and is_done
        )

        branch_name = f"auto/{task.id}"
        branch_exists = branch_name in existing_branches
        is_merged = (
            branch_name in merged_branches
            or (is_done and not branch_exists)
        )

        result.append({
            "id": task.id,
            "title": task.title,
            "source": task.source,
            "sourceId": task.source_id,
            "phase": task.phase or "",
            "priority": task.priority,
            "complexity": task.complexity,
            "delegability": task.delegability,
            "humanInput": task.human_input,
            "specStatus": spec_status,
            "description": task.description,
            "isActionable": task.is_actionable and not is_done and not is_failed,
            "isDone": is_done,
            "isMerged": is_merged,
            "branchExists": branch_exists,
            "isFailed": is_failed,
            "isRunning": is_running,
            "onCooldown": on_cooldown,
            "specPath": str(spec_path) if spec_path else None,
        })

    return result


def get_task_spec_content(task_id: str, source_id: str | None = None) -> dict:
    """Get spec content for a specific task."""
    # Build extra search dirs for non-default sources
    extra_dirs = None
    if source_id and source_id != "default":
        src = get_source_by_id(source_id)
        if src:
            extra_dirs = [Path(src.path)]

    # Try each source type
    for source in (None, "feature", "tech-debt", "refactor", "audit", "bugfix"):
        spec_path, spec_status, spec_type = find_task_spec(
            task_id, source, extra_search_dirs=extra_dirs,
        )
        if spec_path and spec_path.exists():
            return {
                "taskId": task_id,
                "specStatus": spec_status,
                "specType": spec_type,
                "specPath": str(spec_path),
                "content": spec_path.read_text(encoding="utf-8"),
            }
    return {
        "taskId": task_id,
        "specStatus": "missing",
        "specType": "unknown",
        "specPath": None,
        "content": None,
    }


def get_archive_entries() -> list[dict]:
    """
    Build archive entries from archive.json (primary) + registry.md (fallback).
    Enriches with artifact discovery.
    """
    seen_ids: set[str] = set()
    entries = []

    # Primary: archive.json (survives branch switches)
    for entry in load_archive():
        task_id = entry.get("taskId", "")
        if not task_id or task_id in seen_ids:
            continue
        seen_ids.add(task_id)
        entries.append({
            "taskId": task_id,
            "title": entry.get("title", ""),
            "status": entry.get("status", "done"),
            "branch": entry.get("branch", ""),
            "startedAt": entry.get("startedAt", ""),
            "finishedAt": entry.get("finishedAt", ""),
            "costUsd": entry.get("costUsd", 0.0),
            "summary": entry.get("summary", ""),
            "userNotice": entry.get("userNotice", ""),
            "artifacts": discover_artifacts(task_id),
        })

    # Fallback: git-tracked registries (for older runs before archive.json)
    for row in parse_completed_runs():
        task_id = row["id"]
        if task_id in seen_ids:
            continue
        seen_ids.add(task_id)
        cost_val = 0.0
        try:
            cost_val = float(row.get("cost", "$0").replace("$", "").strip())
        except (ValueError, AttributeError):
            pass

        entries.append({
            "taskId": task_id,
            "title": row.get("title", ""),
            "status": _clean_backticks(row.get("status", "done")),
            "branch": _clean_backticks(row.get("branch", "")),
            "startedAt": row.get("started", ""),
            "finishedAt": row.get("finished", ""),
            "costUsd": cost_val,
            "summary": row.get("summary", ""),
            "userNotice": "",
            "artifacts": discover_artifacts(task_id),
        })

    for row in parse_failed_runs():
        task_id = row["id"]
        if task_id in seen_ids:
            continue
        seen_ids.add(task_id)
        cost_val = 0.0
        try:
            cost_val = float(row.get("cost", "$0").replace("$", "").strip())
        except (ValueError, AttributeError):
            pass

        entries.append({
            "taskId": task_id,
            "title": row.get("title", ""),
            "status": _clean_backticks(row.get("status", "failed")),
            "branch": _clean_backticks(row.get("branch", "")),
            "startedAt": row.get("started", ""),
            "finishedAt": "",
            "costUsd": cost_val,
            "summary": row.get("reason", ""),
            "userNotice": "",
            "artifacts": discover_artifacts(task_id),
        })

    # Enrich with branch existence and merge status
    existing_branches = _get_local_branches()
    merged_branches = _get_merged_branches()
    for entry in entries:
        branch = entry.get("branch", "")
        entry["branchExists"] = branch in existing_branches if branch else False
        # Merged = found in merge commits, OR status is 'done' and branch deleted
        # (covers fast-forward merges where no merge commit is created)
        entry["branchMerged"] = (
            (branch in merged_branches if branch else False)
            or (entry.get("status") == "done" and not entry["branchExists"])
        )

    # Sort by finishedAt descending (newest first), fallback to startedAt
    def _sort_key(e):
        return e.get("finishedAt") or e.get("startedAt") or ""
    entries.sort(key=_sort_key, reverse=True)

    return entries


def _get_local_branches() -> set[str]:
    """Get set of all local git branch names."""
    import subprocess
    from ..config import PROJECT_ROOT
    try:
        result = subprocess.run(
            ["git", "branch", "--format=%(refname:short)"],
            capture_output=True, text=True, cwd=str(PROJECT_ROOT),
        )
        if result.returncode != 0:
            return set()
        return {b.strip() for b in result.stdout.splitlines() if b.strip()}
    except Exception:
        return set()


def _get_merged_branches() -> set[str]:
    """Get set of branch names that were merged into auto-dev (from merge commit messages)."""
    import subprocess, re
    from ..config import PROJECT_ROOT, DEV_BRANCH
    try:
        result = subprocess.run(
            ["git", "log", DEV_BRANCH, "--merges", "--format=%s"],
            capture_output=True, text=True, cwd=str(PROJECT_ROOT),
        )
        if result.returncode != 0:
            return set()
        merged = set()
        for line in result.stdout.splitlines():
            # "Merge branch 'auto/MVP9' into auto-dev"
            m = re.search(r"Merge branch '([^']+)'", line)
            if m:
                merged.add(m.group(1))
                continue
            # "Merge auto/i18n-support: MVP25, ..." (custom message)
            m = re.search(r"^Merge (auto/\S+)", line)
            if m:
                merged.add(m.group(1).rstrip(":"))
        return merged
    except Exception:
        return set()
