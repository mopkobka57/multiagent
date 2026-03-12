"""
Agent definitions for the orchestrator.

Defines the specialized agents used by the Orchestrator.
Each agent has a specific role, tool set, and system prompt.
"""

from collections import defaultdict
from pathlib import Path
from claude_agent_sdk import AgentDefinition

from .. import config


def _get_prompt_vars() -> dict[str, str]:
    """Build template variables for prompt rendering."""
    return {
        "project_name": config.PROJECT_NAME,
        "project_description": config.PROJECT_DESCRIPTION,
        "app_dir": str(config.APP_DIR),
        "app_dir_rel": str(config.APP_DIR.relative_to(config.PROJECT_ROOT)) if config.APP_DIR != config.PROJECT_ROOT else ".",
        "data_dir": str(config.DATA_DIR),
        "data_dir_rel": str(config.DATA_DIR.relative_to(config.PROJECT_ROOT)) if config.DATA_DIR != config.PROJECT_ROOT else ".",
        "specs_dir": str(config.SPECS_DIR),
        "specs_dir_rel": str(config.SPECS_DIR.relative_to(config.PROJECT_ROOT)) if config.SPECS_DIR != config.PROJECT_ROOT else ".",
        "quality_gate_fast": config.QUALITY_GATES.get("fast", config.QUALITY_GATES.get("tsc", "echo 'no fast gate configured'")),
        "quality_gate_full": config.QUALITY_GATES.get("full", config.QUALITY_GATES.get("build", "echo 'no full gate configured'")),
        "protected_paths_formatted": _format_protected_paths(),
        "writable_paths_formatted": _format_writable_paths(),
        "known_gotchas": _load_gotchas_summary(),
        "dev_branch": config.DEV_BRANCH,
        "project_root": str(config.PROJECT_ROOT),
    }


def _format_protected_paths() -> str:
    lines = [f"- {p}" for p in config.PROTECTED_PATHS]
    for e in config.PROTECTED_EXCEPTIONS:
        lines.append(f"  (exception: {e})")
    return "\n".join(lines)


def _format_writable_paths() -> str:
    return ", ".join(config.WRITABLE_PATHS) if config.WRITABLE_PATHS else "(configured in multiagent.toml)"


def _load_gotchas_summary() -> str:
    if config.INSIGHTS_FILE.exists():
        content = config.INSIGHTS_FILE.read_text(encoding="utf-8")
        lines = [l for l in content.splitlines()
                 if l.strip().startswith("**[CRITICAL]") or l.strip().startswith("- **")]
        if lines:
            return "\n".join(lines[:20])
    return "(no known gotchas yet — check agent_insights.md as you work)"


def _load_and_render_prompt(filename: str) -> str:
    """Load a prompt template and substitute project variables."""
    template = (config.PROMPTS_DIR / filename).read_text(encoding="utf-8")
    vars = _get_prompt_vars()
    return template.format_map(defaultdict(str, **vars))


def create_agents() -> dict[str, AgentDefinition]:
    """Create all subagent definitions for the Orchestrator."""

    return {
        "product": AgentDefinition(
            description=(
                "Product Designer — defines user experience, edge cases, "
                "and product requirements for features. Use before Analyst "
                "for feature specs that are stub or partial. Read-only + spec writing."
            ),
            prompt=_load_and_render_prompt("product_system.md"),
            tools=["Read", "Glob", "Grep", "Edit", "Write"],
            model=config.SUBAGENT_MODEL,
        ),

        "analyst": AgentDefinition(
            description=(
                "Code Analyst — reads existing code, understands patterns, "
                "writes design documents. Use for analyzing codebase before "
                "implementing a new feature. Read-only + spec editing."
            ),
            prompt=_load_and_render_prompt("analyst_system.md"),
            tools=["Read", "Glob", "Grep", "Edit"],
            model=config.SUBAGENT_MODEL,
        ),

        "implementor": AgentDefinition(
            description=(
                "Code Implementor — writes code according to a precise plan. "
                "Use for implementing specific steps from the implementation plan. "
                "Provide exact instructions for what files to modify and how."
            ),
            prompt=_load_and_render_prompt("implementor_system.md"),
            tools=["Read", "Glob", "Grep", "Edit", "Write", "Bash"],
            model=config.SUBAGENT_MODEL,
        ),

        "reviewer": AgentDefinition(
            description=(
                "Code Reviewer — reviews git diff for bugs, security issues, "
                "and pattern violations. Use after implementation to verify quality. "
                "Returns PASS/FAIL verdict with specific issues."
            ),
            prompt=_load_and_render_prompt("reviewer_system.md"),
            tools=["Read", "Glob", "Grep", "Bash"],
            model=config.SUBAGENT_MODEL,
        ),

        "visual-tester": AgentDefinition(
            description=(
                "Visual QA Tester — captures screenshots of the running app "
                "before and after changes, compares them for visual regressions, "
                "checks console for JS errors. Use after implementation to verify "
                f"UI looks correct. Needs dev server running on {config.DEV_SERVER_URL}."
            ),
            prompt=_load_and_render_prompt("visual_tester_system.md"),
            tools=["Read", "Bash", "Glob"],
            model=config.SUBAGENT_MODEL,
        ),
    }
