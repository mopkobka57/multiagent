"""
AI-powered spec creation from natural language descriptions.

Uses claude-agent-sdk to generate structured spec files and backlog entries.
Supports multi-spec detection from file input.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from ..config import (
    BACKLOG_FILE,
    DATA_DIR,
    PRODUCT_CONTEXT_FILE,
    SPECS_DIR,
)


# Task ID prefix → type and spec subdirectory
_TYPE_INFO = {
    "feature":   {"prefix": "FE", "dir": "features"},
    "tech-debt": {"prefix": "TD", "dir": "tech_debt"},
    "refactor":  {"prefix": "RF", "dir": "refactor"},
    "bugfix":    {"prefix": "BF", "dir": "bugfix"},
    "audit":     {"prefix": "AU", "dir": "audit"},
}

# Reverse: prefix → type
_PREFIX_TO_TYPE = {v["prefix"]: k for k, v in _TYPE_INFO.items()}


def _status(msg: str) -> None:
    """Print a progress message to stderr (doesn't mix with stdout results)."""
    print(msg, file=sys.stderr, flush=True)


def _generate_task_id(task_type: str) -> str:
    """
    Scan backlog for the highest ID with the given type's prefix, increment by 1.
    """
    info = _TYPE_INFO.get(task_type)
    if not info:
        info = _TYPE_INFO["feature"]
    prefix = info["prefix"]

    max_num = 0
    if BACKLOG_FILE.exists():
        content = BACKLOG_FILE.read_text(encoding="utf-8")
        for match in re.finditer(rf"\|\s*({re.escape(prefix)}\d+)\s*\|", content):
            num = int(match.group(1)[len(prefix):])
            if num > max_num:
                max_num = num

    return f"{prefix}{max_num + 1}"


def _get_project_context() -> str:
    """Load project context for AI prompts."""
    context_parts = []

    if PRODUCT_CONTEXT_FILE.exists():
        context_parts.append(
            "## Product Context\n\n"
            + PRODUCT_CONTEXT_FILE.read_text(encoding="utf-8")
        )

    conventions_path = SPECS_DIR / "_project-conventions.md"
    if conventions_path.exists():
        context_parts.append(
            "## Project Conventions\n\n"
            + conventions_path.read_text(encoding="utf-8")
        )

    return "\n\n---\n\n".join(context_parts) if context_parts else "(no project context available)"


def _build_prompt(description: str) -> str:
    """Assemble the AI prompt for single spec generation."""
    context_block = _get_project_context()

    return f"""You are a spec writer for a software project.

Given a natural language description of a task, generate a structured task spec.

{context_block}

---

Task description from the user:
{description}

---

Return a JSON object with these fields (no extra text, no code fences):

{{
  "title": "Short task title (3-8 words)",
  "type": "feature|tech-debt|refactor|bugfix|audit",
  "slug": "kebab-case-slug-for-filename",
  "importance": 3,
  "complexity": 3,
  "backlog_description": "One-line description for the backlog table",
  "spec_content": "Full markdown spec content (everything below the metadata header)"
}}

Rules:
- "type" must be one of: feature, tech-debt, refactor, bugfix, audit. Default to "feature" if unclear.
- "importance" and "complexity" are integers 1-5.
- "slug" should be 2-4 words, kebab-case, no task ID prefix.
- "spec_content" should include: Overview, Acceptance Criteria. For features, also include User Experience and Scope sections. Use markdown formatting.
- Return ONLY the JSON object. No explanation, no wrapping."""


def _build_triage_prompt(description: str) -> str:
    """Prompt to decide if input contains one or multiple tasks."""
    return f"""You are a task analyst. Read the following text and determine whether it describes ONE task or MULTIPLE separate tasks.

Text:
{description}

---

Return a JSON object (no extra text, no code fences):

If ONE task:
{{ "count": 1 }}

If MULTIPLE tasks:
{{
  "count": <number>,
  "tasks": [
    {{ "title": "Short title", "summary": "One-line summary" }},
    ...
  ]
}}

Rules:
- A single feature/bugfix/refactor with multiple steps or acceptance criteria is ONE task.
- Separate unrelated features, independent fixes, or distinct improvements are MULTIPLE tasks.
- When in doubt, lean toward ONE.
- Return ONLY the JSON object."""


def _build_multi_prompt(description: str, task_index: int, total: int) -> str:
    """Prompt for generating one spec from a multi-task input."""
    context_block = _get_project_context()

    return f"""You are a spec writer for a software project.

The user provided a document describing {total} tasks. Generate a spec for task #{task_index + 1} only.

{context_block}

---

Full document from the user:
{description}

---

Generate a spec for task #{task_index + 1} from this document.

Return a JSON object with these fields (no extra text, no code fences):

{{
  "title": "Short task title (3-8 words)",
  "type": "feature|tech-debt|refactor|bugfix|audit",
  "slug": "kebab-case-slug-for-filename",
  "importance": 3,
  "complexity": 3,
  "backlog_description": "One-line description for the backlog table",
  "spec_content": "Full markdown spec content (everything below the metadata header)"
}}

Rules:
- "type" must be one of: feature, tech-debt, refactor, bugfix, audit. Default to "feature" if unclear.
- "importance" and "complexity" are integers 1-5.
- "slug" should be 2-4 words, kebab-case, no task ID prefix.
- "spec_content" should include: Overview, Acceptance Criteria. For features, also include User Experience and Scope sections. Use markdown formatting.
- Focus ONLY on task #{task_index + 1}. Do not include other tasks.
- Return ONLY the JSON object. No explanation, no wrapping."""


async def _query_ai(prompt: str) -> str:
    """Run a single AI query and return the text result."""
    from claude_agent_sdk import (
        query,
        ClaudeAgentOptions,
        AssistantMessage,
        ResultMessage,
        TextBlock,
    )

    options = ClaudeAgentOptions(
        model="sonnet",
        allowed_tools=[],
        max_turns=1,
    )

    result_text = ""
    stream = query(prompt=prompt, options=options)
    async for message in stream:
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    result_text += block.text
        elif isinstance(message, ResultMessage):
            if not result_text.strip() and message.result:
                result_text = message.result

    return result_text.strip()


def _parse_json_response(text: str) -> dict:
    """Parse AI response as JSON, stripping code fences if present."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
    cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    return json.loads(cleaned)


def _write_spec_file(task_id: str, task_type: str, slug: str, content: str) -> Path:
    """Write spec file to the appropriate subdirectory."""
    info = _TYPE_INFO.get(task_type, _TYPE_INFO["feature"])
    spec_dir = SPECS_DIR / info["dir"]
    spec_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{task_id}-{slug}.md"
    spec_path = spec_dir / filename

    header = f"""# {task_id} — {slug.replace('-', ' ').title()}

**Task ID:** {task_id}
**Type:** {task_type}
**Spec Status:** stub

---

"""
    spec_path.write_text(header + content, encoding="utf-8")
    return spec_path


def _append_backlog_entry(
    task_id: str,
    title: str,
    task_type: str,
    importance: int,
    complexity: int,
    description: str,
    phase: str,
) -> None:
    """Append a row to the backlog under the target phase."""
    if not BACKLOG_FILE.exists():
        raise FileNotFoundError(
            f"Backlog not found at {BACKLOG_FILE}. Run `python -m multiagent init` first."
        )

    content = BACKLOG_FILE.read_text(encoding="utf-8")
    row = f"| {task_id} | {title} | {task_type} | {importance} | {complexity} | high | stub | auto | {description} |"

    # Find the target phase section
    phase_pattern = re.compile(
        rf"^##\s+Phase\s+{re.escape(phase)}\b.*$", re.MULTILINE
    )
    phase_match = phase_pattern.search(content)

    if phase_match:
        # Find the end of this phase's table: next ## header or EOF
        next_header = re.search(r"^## ", content[phase_match.end():], re.MULTILINE)
        if next_header:
            insert_pos = phase_match.end() + next_header.start()
            # Insert row before the blank lines preceding the next header
            before = content[:insert_pos].rstrip()
            after = content[insert_pos:]
            content = before + "\n" + row + "\n" + after
        else:
            # End of file
            content = content.rstrip() + "\n" + row + "\n"
    else:
        # Phase doesn't exist — create it at the end
        table_header = (
            f"\n\n## Phase {phase}\n\n"
            "| ID | Name | Type | Importance | Complexity | Deleg. | Spec | Human | Description |\n"
            "|----|------|------|-----------|-----------|--------|------|-------|-------------|\n"
        )
        content = content.rstrip() + table_header + row + "\n"

    BACKLOG_FILE.write_text(content, encoding="utf-8")


def _materialize_spec(data: dict, phase: str) -> dict:
    """Validate AI response, write spec file, append backlog entry. Returns result dict."""
    title = data.get("title", "Untitled task")
    task_type = data.get("type", "feature")
    if task_type not in _TYPE_INFO:
        task_type = "feature"
    slug = data.get("slug", "new-task")
    importance = max(1, min(5, int(data.get("importance", 3))))
    complexity = max(1, min(5, int(data.get("complexity", 3))))
    backlog_desc = data.get("backlog_description", title)
    spec_content = data.get("spec_content", "## Overview\n\n(to be filled)")

    task_id = _generate_task_id(task_type)

    _status(f"  Writing spec file...")
    spec_path = _write_spec_file(task_id, task_type, slug, spec_content)

    _status(f"  Adding backlog entry...")
    _append_backlog_entry(task_id, title, task_type, importance, complexity, backlog_desc, phase)

    return {
        "success": True,
        "task_id": task_id,
        "title": title,
        "type": task_type,
        "file_path": str(spec_path),
    }


async def triage_input(description: str) -> dict:
    """
    Determine if the input describes one or multiple tasks.

    Returns:
        Dict with "count" (int) and optionally "tasks" (list of {title, summary}).
    """
    _status("Analyzing input...")
    prompt = _build_triage_prompt(description)

    try:
        raw = await _query_ai(prompt)
    except Exception as e:
        return {"count": 1}  # Fallback to single on error

    try:
        data = _parse_json_response(raw)
    except (json.JSONDecodeError, ValueError):
        return {"count": 1}

    count = int(data.get("count", 1))
    if count <= 1:
        return {"count": 1}

    return {
        "count": count,
        "tasks": data.get("tasks", []),
    }


async def create_spec(description: str, phase: str = "1") -> dict:
    """
    Create a single spec from a description using AI.

    Returns:
        Dict with success, task_id, title, type, file_path.
    """
    if not BACKLOG_FILE.exists():
        return {
            "success": False,
            "error": f"Backlog not found at {BACKLOG_FILE}. Run `python -m multiagent init` first.",
        }

    _status("Generating spec with AI...")
    try:
        raw = await _query_ai(_build_prompt(description))
    except Exception as e:
        return {"success": False, "error": f"AI error: {str(e)[:500]}"}

    if not raw:
        return {"success": False, "error": "AI returned empty response"}

    _status("Parsing AI response...")
    try:
        data = _parse_json_response(raw)
    except json.JSONDecodeError as e:
        return {"success": False, "error": f"Failed to parse AI response as JSON: {e}"}

    return _materialize_spec(data, phase)


async def create_specs_multi(
    description: str, count: int, phase: str = "1"
) -> list[dict]:
    """
    Create multiple specs from a single document.

    Args:
        description: Full document text.
        count: Number of tasks detected by triage.
        phase: Target backlog phase.

    Returns:
        List of result dicts (one per spec).
    """
    if not BACKLOG_FILE.exists():
        return [{
            "success": False,
            "error": f"Backlog not found at {BACKLOG_FILE}. Run `python -m multiagent init` first.",
        }]

    results = []
    for i in range(count):
        _status(f"Generating spec {i + 1}/{count} with AI...")
        try:
            raw = await _query_ai(_build_multi_prompt(description, i, count))
        except Exception as e:
            results.append({"success": False, "error": f"AI error on spec {i + 1}: {str(e)[:500]}"})
            continue

        if not raw:
            results.append({"success": False, "error": f"AI returned empty response for spec {i + 1}"})
            continue

        _status(f"Parsing spec {i + 1}/{count}...")
        try:
            data = _parse_json_response(raw)
        except json.JSONDecodeError as e:
            results.append({"success": False, "error": f"Failed to parse spec {i + 1}: {e}"})
            continue

        result = _materialize_spec(data, phase)
        results.append(result)

    return results
