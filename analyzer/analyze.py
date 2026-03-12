"""
AI-powered project analysis.

Uses claude-agent-sdk to read key project files and generate:
- Product context document
- Project conventions document
- Quality gate suggestions
- Visual test page suggestions
- Protected path suggestions
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .detect import ProjectDetection


@dataclass
class ProjectAnalysis:
    product_context: str = ""
    project_conventions: str = ""
    tech_stack_oneliner: str = ""
    quality_gates_fast: str = ""
    quality_gates_full: str = ""
    visual_test_pages: list[str] = field(default_factory=lambda: ["/"])
    protected_paths: list[str] = field(default_factory=list)
    writable_paths: list[str] = field(default_factory=list)
    gotchas: list[str] = field(default_factory=list)


async def analyze(project_root: Path, detection: ProjectDetection) -> ProjectAnalysis:
    """Run AI agent to deeply analyze the project."""
    from claude_agent_sdk import (
        query,
        ClaudeAgentOptions,
        AssistantMessage,
        ResultMessage,
        TextBlock,
    )

    key_files_str = "\n".join(f"- {f}" for f in detection.key_files[:15])
    src_dirs_str = "\n".join(f"- {d}/" for d in detection.src_dirs[:10])

    prompt = f"""You are a Project Analyzer. Analyze this codebase to generate configuration
for an autonomous AI development agent system.

PROJECT: {detection.name}
LANGUAGE: {detection.language}
FRAMEWORK: {detection.framework or 'unknown'}
APP DIR: {detection.app_dir}
PACKAGE MANAGER: {detection.package_manager or 'unknown'}
HAS TYPESCRIPT: {detection.has_typescript}
HAS TESTS: {detection.has_tests}

KEY FILES:
{key_files_str}

SOURCE DIRECTORIES:
{src_dirs_str}

DIRECTORY STRUCTURE:
{detection.structure_summary}

YOUR TASKS:

1. Read README.md (if exists) and CLAUDE.md (if exists) to understand the project
2. Read key config files (package.json, tsconfig.json, prisma schema, etc.)
3. Read 5-10 representative source files from the source directories
4. Generate the following (output as JSON):

{{
  "product_context": "## What is [project]?\\n\\n[2-3 paragraphs]\\n\\n## Target Audience\\n\\n[description]\\n\\n## Key Features\\n\\n[bullet list]\\n\\n## Architecture Overview\\n\\n[high-level description]",

  "project_conventions": "## Tech Stack\\n\\n[with versions]\\n\\n## Project Structure\\n\\n[what each directory does]\\n\\n## Key Patterns\\n\\n[coding conventions, architecture patterns]\\n\\n## Common Gotchas\\n\\n[potential pitfalls]",

  "tech_stack_oneliner": "Framework + DB + CSS + etc",

  "quality_gates": {{
    "fast": "command for quick type/lint check",
    "full": "command for full build"
  }},

  "visual_test_pages": ["/", "/other-key-page"],

  "protected_paths": ["config-files", "env-files"],

  "writable_paths": ["src/", "tests/"],

  "gotchas": ["gotcha 1", "gotcha 2"]
}}

IMPORTANT: Output ONLY the JSON. No explanation, no markdown fencing.
Quality gates should use relative paths. Use cd {{app_dir}} prefix if app_dir is not ".".
If there's a tsconfig.json, the fast gate should include tsc check.
If there's a build script, the full gate should use it.
"""

    options = ClaudeAgentOptions(
        allowed_tools=["Read", "Glob", "Grep"],
        model="sonnet",
        max_turns=20,
    )

    result_text = ""
    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        result_text += block.text
            elif isinstance(message, ResultMessage):
                if not result_text.strip() and message.result:
                    result_text = message.result
    except Exception as e:
        print(f"  AI analysis failed: {e}")
        return fallback_analysis(detection)

    return _parse_analysis(result_text, detection)


def _parse_analysis(raw: str, detection: ProjectDetection) -> ProjectAnalysis:
    """Parse JSON output from AI into ProjectAnalysis."""
    # Strip markdown code fences if present
    raw = re.sub(r'^```json?\s*', '', raw.strip())
    raw = re.sub(r'\s*```$', '', raw.strip())

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return fallback_analysis(detection)

    analysis = ProjectAnalysis()
    analysis.product_context = data.get("product_context", "")
    analysis.project_conventions = data.get("project_conventions", "")
    analysis.tech_stack_oneliner = data.get("tech_stack_oneliner", "")

    qg = data.get("quality_gates", {})
    analysis.quality_gates_fast = qg.get("fast", "")
    analysis.quality_gates_full = qg.get("full", "")

    analysis.visual_test_pages = data.get("visual_test_pages", ["/"])
    analysis.protected_paths = data.get("protected_paths", [])
    analysis.writable_paths = data.get("writable_paths", [])
    analysis.gotchas = data.get("gotchas", [])

    return analysis


def fallback_analysis(detection: ProjectDetection) -> ProjectAnalysis:
    """Generate minimal analysis when AI fails."""
    analysis = ProjectAnalysis()
    analysis.tech_stack_oneliner = f"{detection.framework or detection.language} application"

    if detection.has_typescript:
        app_prefix = f"cd {detection.app_dir} && " if detection.app_dir != "." else ""
        analysis.quality_gates_fast = f"{app_prefix}npx tsc --noEmit"

    if detection.build_command:
        pm = detection.package_manager or "npm"
        app_prefix = f"cd {detection.app_dir} && " if detection.app_dir != "." else ""
        analysis.quality_gates_full = f"{app_prefix}{pm} run build"

    if detection.app_dir != ".":
        analysis.writable_paths = [f"{detection.app_dir}/"]
    else:
        analysis.writable_paths = ["src/"]

    return analysis
