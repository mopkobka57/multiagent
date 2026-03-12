"""
Prompt construction for the orchestrator.

Builds prompts for both standard tasks and audit tasks.
Handles spec discovery, context loading, and foundational docs.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .. import config
from ..config import (
    CONTEXT_FILES,
    DEV_BRANCH,
    BRANCH_PREFIX,
    FOUNDATIONAL_SPECS,
    PROJECT_ROOT,
    SPEC_TYPE_DIRS,
    SPECS_DIR,
    AUDIT_REPORTS_DIR,
)

from .state import OrchestratorState
from .task_loader import Task


def load_context() -> str:
    """Load all context files into a single string for the Orchestrator."""
    sections = []
    for path in CONTEXT_FILES:
        if path.exists():
            content = path.read_text(encoding="utf-8")
            sections.append(f"# FILE: {path.name}\n\n{content}")
    return "\n\n---\n\n".join(sections)


def load_foundational_specs() -> str:
    """Load foundational specs (architecture, conventions) for agent context."""
    sections = []
    for path in FOUNDATIONAL_SPECS:
        if path.exists():
            content = path.read_text(encoding="utf-8")
            sections.append(f"# REFERENCE: {path.name}\n\n{content}")
    return "\n\n---\n\n".join(sections)


def find_task_spec(
    task_id: str,
    task_source: str | None = None,
    extra_search_dirs: list[Path] | None = None,
) -> tuple[Path | None, str, str]:
    """
    Find the latest version of a task spec.
    Returns (path, status, spec_type) where:
      - status is 'full'|'partial'|'stub'|'missing'
      - spec_type is 'feature'|'tech-debt'|'refactor'|'audit'|'unknown'

    Versioning: TD2-error-handling.md (v1), TD2-error-handling.v2.md (v2), etc.
    Always returns the highest version.

    Search strategy:
      1. If task_source is known → search in the specific subdirectory first
      2. If not found → fallback to all subdirectories + root
      3. If extra_search_dirs provided → also scan those dirs and their subdirs
    """
    # Support both hyphenated and original task ID as file prefixes
    prefixes = {task_id}
    hyphenated = task_id.replace("_", "-")
    if hyphenated != task_id:
        prefixes.add(hyphenated)

    def _scan_dir(directory: Path) -> list[tuple[int, Path]]:
        """Find matching spec files in a directory."""
        hits = []
        if not directory.exists():
            return hits
        for f in directory.iterdir():
            if f.name.startswith("_") or f.is_dir():
                continue
            name = f.stem
            if any(name.startswith(p + "-") or name == p for p in prefixes):
                version = 1
                match = re.search(r'\.v(\d+)$', name)
                if match:
                    version = int(match.group(1))
                hits.append((version, f))
        return hits

    def _scan_recursive(directory: Path) -> list[tuple[int, Path]]:
        """Scan a directory and all its subdirectories."""
        hits = _scan_dir(directory)
        if directory.exists():
            for child in directory.iterdir():
                if child.is_dir() and not child.name.startswith("."):
                    hits.extend(_scan_recursive(child))
        return hits

    candidates = []

    # 1. Try specific subdirectory first (default specs)
    if SPECS_DIR.exists():
        if task_source and task_source in SPEC_TYPE_DIRS:
            candidates = _scan_dir(SPEC_TYPE_DIRS[task_source])

        # 2. Fallback: scan all subdirectories + root
        if not candidates:
            candidates = _scan_dir(SPECS_DIR)
            for subdir in SPEC_TYPE_DIRS.values():
                candidates.extend(_scan_dir(subdir))

    # 3. Scan extra search dirs (custom source folders)
    if extra_search_dirs:
        for extra_dir in extra_search_dirs:
            candidates.extend(_scan_recursive(extra_dir))

    if not candidates:
        return None, "missing", "unknown"

    candidates.sort(key=lambda x: x[0], reverse=True)
    best_path = candidates[0][1]

    # Read spec status and type from file header
    status = "partial"
    spec_type = "unknown"
    try:
        content = best_path.read_text(encoding="utf-8")
        status_match = re.search(r'\*\*Spec Status:\*\*\s*(\w+)', content)
        if status_match:
            status = status_match.group(1)
        type_match = re.search(r'\*\*Type:\*\*\s*([\w-]+)', content)
        if type_match:
            spec_type = type_match.group(1)
    except Exception:
        pass

    return best_path, status, spec_type


def get_previous_audit_report(task_id: str) -> str | None:
    """Load the most recent audit report for this task, if any."""
    if not AUDIT_REPORTS_DIR.exists():
        return None
    reports = sorted(AUDIT_REPORTS_DIR.glob(f"{task_id}_audit_*.md"), reverse=True)
    if reports:
        return reports[0].read_text(encoding="utf-8")
    return None


def _build_protected_paths_block() -> str:
    """Build the protected paths section for orchestrator prompts."""
    lines = []
    for p in config.PROTECTED_PATHS:
        lines.append(f"- {p}")
    if config.PROTECTED_EXCEPTIONS:
        lines.append(f"  (exceptions: {', '.join(config.PROTECTED_EXCEPTIONS)})")
    return "\n".join(lines)


def _build_writable_paths() -> str:
    """Build the writable paths string for orchestrator prompts."""
    paths = list(config.WRITABLE_PATHS)
    specs_rel = str(SPECS_DIR.relative_to(PROJECT_ROOT)) + "/"
    if specs_rel not in paths:
        paths.append(specs_rel)
    return ", ".join(paths)


def build_orchestrator_prompt(
    task: Task, context: str, state: OrchestratorState,
    extra_search_dirs: list[Path] | None = None,
) -> str:
    """Build the full prompt for the Orchestrator agent (standard pipeline)."""

    branch = f"{BRANCH_PREFIX}{task.id.replace('_', '-')}"

    spec_path, spec_status, spec_type = find_task_spec(
        task.id, task.source, extra_search_dirs=extra_search_dirs
    )
    is_feature = spec_type == "feature"

    # Determine where specs should be written/updated.
    if spec_path:
        spec_target_dir = spec_path.parent
    elif extra_search_dirs:
        spec_target_dir = extra_search_dirs[0]
    else:
        spec_target_dir = SPEC_TYPE_DIRS.get(task.source, SPECS_DIR)

    if spec_path:
        spec_content = spec_path.read_text(encoding="utf-8")
        spec_info = (
            f"\n\nTASK SPEC ({spec_status}, type={spec_type}, {spec_path.name}):\n"
            f"{spec_content}\n"
        )
        if spec_status == "stub":
            if is_feature:
                spec_info += (
                    "\nSpec is a STUB and type is \"feature\" — FIRST use Product agent to expand "
                    "the product side by writing product sections (## User Experience, "
                    f"## Edge Cases & Error States, ## Scope) into the spec at {spec_path}. "
                    "THEN use Analyst agent to add technical sections (## Technical Approach, "
                    "## Files to Modify) into the SAME spec file. Both agents write into "
                    "different sections of ONE file — do NOT create v2.\n"
                )
            else:
                spec_info += (
                    "\nSpec is a STUB — Analyst agent MUST create an expanded spec (v2) "
                    f"at {spec_target_dir}/{spec_path.stem}.v2.md before implementation.\n"
                )
        elif spec_status == "partial":
            if is_feature:
                spec_info += (
                    "\nSpec is PARTIAL and type is \"feature\" — FIRST use Product agent to add "
                    "product sections (## User Experience, ## Edge Cases & Error States, "
                    f"## Scope) into the spec at {spec_path}. "
                    "THEN use Analyst agent to add technical sections (## Technical Approach, "
                    "## Files to Modify) into the SAME spec file. Both agents write into "
                    "different sections of ONE file — do NOT create v2.\n"
                )
            else:
                spec_info += (
                    "\nSpec is PARTIAL — Analyst agent should review and decide if expansion "
                    "is needed. If so, create v2. If sufficient, proceed.\n"
                )
    else:
        if is_feature:
            spec_info = (
                f"\n\nNO SPEC exists for this task.\n"
                f"FIRST use Product agent to create spec at {spec_target_dir}/{task.id}-<name>.md "
                "with product sections (## User Experience, ## Edge Cases & Error States, ## Scope). "
                "THEN use Analyst agent to add technical sections (## Technical Approach, "
                "## Files to Modify) into the SAME file.\n"
            )
        else:
            spec_info = (
                f"\n\nNO SPEC exists for this task.\n"
                f"Analyst agent MUST create one at {spec_target_dir}/{task.id}-<name>.md before implementation.\n"
            )

    # Check for existing plan (resume scenario)
    plan_info = ""
    if state.current_task and state.current_task.plan:
        plan_info = (
            f"\n\nEXISTING PLAN (resuming):\n{state.current_task.plan}\n"
            f"Current step: {state.current_task.current_step}\n"
        )

    foundational = load_foundational_specs()

    # Quality gate commands
    qg_fast = config.QUALITY_GATES.get("fast", config.QUALITY_GATES.get("tsc", "echo 'no fast gate'"))
    qg_full = config.QUALITY_GATES.get("full", config.QUALITY_GATES.get("build", "echo 'no full gate'"))

    # Protected paths
    protected_block = _build_protected_paths_block()
    writable = _build_writable_paths()

    # Custom source note
    is_custom_source = spec_target_dir != SPECS_DIR and spec_target_dir not in SPEC_TYPE_DIRS.values()
    specs_dir_note = (
        f"  (Custom source — specs for this task live here, NOT in {SPECS_DIR})"
        if is_custom_source
        else "  Subdirectories: audit/, features/, tech_debt/, refactor/, bugfix/"
    )

    return f"""You are the {config.PROJECT_NAME} Orchestrator. Execute this task autonomously.

TASK ID: {task.id}
TASK SOURCE: {task.source}
TASK TITLE: {task.title}
TASK DESCRIPTION:
{task.description}

PRIORITY: {task.priority:.2f}
COMPLEXITY: {task.complexity}/5
{spec_info}
{plan_info}

PROJECT CONTEXT:
{context}

REFERENCE DOCS (architecture & conventions):
{foundational}

GIT SETUP:
- You are already on branch: {branch} (created from {DEV_BRANCH})
- All work happens on this branch
- Do NOT push or merge — the orchestrator handles that

INSTRUCTIONS:
You are a COORDINATOR. Do NOT read or write code directly — delegate to Analyst and Implementor.
Delegate ALL code reading to Analyst. Delegate ALL code writing to Implementor.

1. Check the TASK SPEC status and type above:
   - If "full" → go to step 2
   - If "partial"/"stub" AND type is "feature" → delegate to Product agent first (UX, edge cases, scope), THEN delegate to Analyst (technical approach) — both write into the SAME spec file
   - If "partial"/"stub" AND type is NOT "feature" → delegate to Analyst to review/expand
   - If missing AND feature → delegate to Product first, then Analyst — both create sections in the same new spec file
   - If missing AND not feature → delegate to Analyst to create spec

2. DELEGATE to Analyst: "Read the task spec and relevant codebase. Return: (1) files to modify, (2) existing patterns to follow, (3) risks, (4) step-by-step implementation plan (3-8 steps). Each step: files, changes, patterns, criteria."

3. Review the Analyst's plan. For EACH step, delegate to Implementor with precise instructions.

4. After each Implementor step, run quality gate via Bash: `{qg_fast}`
   - If fails: send error to Implementor (max 3 retries per step)

5. After ALL steps: delegate to Reviewer: "Review the full diff: git diff {DEV_BRANCH}...HEAD"
   - If issues found: delegate fixes to Implementor, then re-review

6. After Reviewer passes: delegate to Visual Tester to check UI

7. Finalize via Bash: create a descriptive git commit on this branch

8. Output these TWO markers as your LAST output (the pipeline parses them):
   [TASK_SUMMARY]: <one-line summary, max 60 chars, describing what was done>
   [USER_NOTICE]: <what changed for the user — which pages/features to check, 2-3 bullet points>
   Example:
   [TASK_SUMMARY]: Add JWT auth with refresh tokens and session management
   [USER_NOTICE]: Check /login page — new password field validation. Check /dashboard — user menu now shows avatar. Check /api/auth — new refresh token endpoint.

SPECS DIRECTORY: {spec_target_dir}
{specs_dir_note}
  Foundational docs (prefixed with _) stay in {SPECS_DIR}.
WORKING DIRECTORY: {PROJECT_ROOT}
APP DIRECTORY: {config.APP_DIR}

IMPORTANT:
- Branch is already created — just start working
- Do NOT read files yourself — delegate to Analyst or Implementor
- Keep changes minimal and focused
- After ALL work is done, create a single comprehensive git commit on this branch

PROTECTED PATHS — you must NEVER modify these files or directories:
{protected_block}
You MAY write to: {writable}{f", {spec_target_dir}/" if is_custom_source else ""}.
Violations will be automatically detected and reverted.

INSIGHTS (from PROJECT CONTEXT):
- The "Agent Insights" section contains CRITICAL gotchas — pass them to Analyst and Implementor
- If you discover a new critical insight, add tag: [NEW INSIGHT]: category — description

Report progress after each major step concisely.
"""


def build_audit_prompt(task: Task, context: str, spec_content: str, previous_report: str | None) -> str:
    """Build the prompt for an audit task (Analyst-only, read-only)."""
    foundational = load_foundational_specs()

    prev_section = ""
    if previous_report:
        prev_section = (
            f"\n\nPREVIOUS AUDIT REPORT (compare for regressions/improvements):\n"
            f"{previous_report}\n"
        )

    protected_block = _build_protected_paths_block()

    return f"""You are the {config.PROJECT_NAME} Orchestrator running an AUDIT task. This is NOT a feature implementation.

TASK ID: {task.id}
TASK SOURCE: audit
TASK TITLE: {task.title}
TASK DESCRIPTION:
{task.description}

AUDIT SPEC (criteria to check):
{spec_content}
{prev_section}

PROJECT CONTEXT:
{context}

REFERENCE DOCS (architecture & conventions):
{foundational}

INSTRUCTIONS:
You must use the Analyst agent to perform a READ-ONLY audit of the codebase.

The Analyst should:
1. Read the audit spec — it defines the criteria to check
2. Scan relevant codebase files against each criterion
3. Record findings with severity (critical/warning/info), location, recommendation
4. Compare with previous report if provided — note improvements and regressions
5. Do NOT modify any code — only read and report

OUTPUT FORMAT — the Analyst MUST produce a report in this exact structure:

```
# Audit Report: {task.title}
**Task ID:** {task.id}
**Date:** {datetime.now().strftime('%Y-%m-%d')}

## Summary
- Total findings: N
- Critical: N, Warning: N, Info: N
- Overall health: Good | Fair | Poor

## Findings
### [FE29] {{title}}
- **Severity:** critical | warning | info
- **Category:** consistency | architecture | performance | security
- **Location:** {{file}}:{{line range}}
- **Description:** ...
- **Recommendation:** ...
- **Suggested task type:** tech-debt | bugfix | refactor
```

CRITICAL — TASK GENERATION:
After the report, you MUST output machine-parseable task markers.
The system uses regex to extract these markers. Without them, no tasks are created.

For EVERY finding that should become a new task, output EXACTLY this line:
[NEW TASK]: {{type}} | {{title}} | {{description}} | origin:{task.id}

Rules:
- type is one of: bugfix, tech-debt, refactor
- Each marker MUST be on its own line, starting with literal "[NEW TASK]:"
- Do NOT wrap in bold (**), backticks, or any markdown formatting
- Do NOT use tables, bullet lists, or any other format for task proposals
- Place ALL [NEW TASK] markers in a dedicated section at the END of the report

Example (follow this format exactly):

## New Tasks

[NEW TASK]: tech-debt | Extract hardcoded strings to i18n catalogs | ~200 hardcoded English strings across 30+ components need extraction to translation files | origin:{task.id}
[NEW TASK]: bugfix | Fix broken language preference save | Settings API endpoint has DB write commented out, preference is never persisted | origin:{task.id}

IMPORTANT:
- Do NOT create any git branches or modify any files
- Do NOT use the Implementor, Reviewer, or Visual Tester agents
- ONLY use the Analyst agent for read-only analysis
- The Analyst should use Read, Glob, Grep tools to scan code
- Report your complete findings in the output

PROTECTED PATHS — you must NEVER modify these files or directories:
{protected_block}
Violations will be automatically detected and reverted.

WORKING DIRECTORY: {PROJECT_ROOT}
APP DIRECTORY: {config.APP_DIR}
SPECS DIRECTORY: {SPECS_DIR}
"""
