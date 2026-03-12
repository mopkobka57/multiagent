"""
Project initialization — `multiagent init`.

Creates data directory, runs project analysis, generates config.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from ..config import PROJECT_ROOT, MULTIAGENT_DIR


TEMPLATES_DIR = MULTIAGENT_DIR / "templates"


def run_init(
    data_dir_name: str = "agents_data",
    non_interactive: bool = False,
    refresh: bool = False,
) -> bool:
    """
    Initialize multiagent for the current project.

    Args:
        data_dir_name: Name for the data directory
        non_interactive: Skip confirmations
        refresh: Re-run analysis without recreating structure

    Returns True on success.
    """
    return asyncio.run(_init_async(data_dir_name, non_interactive, refresh))


async def _init_async(data_dir_name: str, non_interactive: bool, refresh: bool) -> bool:
    data_dir = PROJECT_ROOT / data_dir_name
    toml_path = PROJECT_ROOT / "multiagent.toml"

    # --- Pre-flight: warn if already initialized ---
    if not refresh and toml_path.exists():
        if non_interactive:
            print("multiagent.toml already exists. Use --refresh to re-run analysis.")
            return False
        answer = input("Init already completed. Re-run? [y/N]: ").strip().lower()
        if answer != "y":
            print("Exiting. Use --refresh to re-run analysis only.")
            return False

    # --- Step 1: Detection ---
    print("Detecting project structure...")
    from ..analyzer.detect import detect
    detection = detect(PROJECT_ROOT)

    print(f"  Name: {detection.name}")
    print(f"  Language: {detection.language}")
    print(f"  Framework: {detection.framework or 'unknown'}")
    print(f"  App dir: {detection.app_dir}")
    print(f"  Package manager: {detection.package_manager or 'unknown'}")

    if not non_interactive:
        ok = input("\nProceed with AI analysis? [Y/n]: ").strip().lower()
        if ok == "n":
            print("Skipping AI analysis. Generating minimal config...")
            from ..analyzer.analyze import fallback_analysis
            analysis = fallback_analysis(detection)
        else:
            analysis = await _run_analysis(detection)
    else:
        analysis = await _run_analysis(detection)

    # --- Step 2: Create directory structure ---
    if not refresh:
        print(f"\nCreating {data_dir_name}/...")
        _create_data_dir(data_dir, detection)

    # --- Step 3: Generate multiagent.toml ---
    print("Generating multiagent.toml...")
    _generate_toml(toml_path, detection, analysis, data_dir_name)

    # --- Step 4: Generate context files ---
    print("Writing context files...")
    _write_context_files(data_dir, detection, analysis)

    # --- Step 5: Update .gitignore ---
    _update_gitignore()

    # --- Step 6: Update CLAUDE.md ---
    print("Updating CLAUDE.md...")
    _update_claude_md(data_dir_name)

    # --- Done ---
    print(f"\n{'=' * 50}")
    print("Initialization complete!")
    print(f"{'=' * 50}")
    print(f"\nCreated:")
    print(f"  multiagent.toml")
    print(f"  CLAUDE.md (multiagent section)")
    print(f"  {data_dir_name}/backlog.md")
    print(f"  {data_dir_name}/registry.md")
    print(f"  {data_dir_name}/agent_insights.md")
    print(f"  {data_dir_name}/product_context.md")
    print(f"  {data_dir_name}/specs/_project-conventions.md")
    print(f"\nNext steps:")
    print(f"  1. Review multiagent.toml — adjust if needed")
    print(f"  2. Review CLAUDE.md — add project-specific instructions above the multiagent section")
    print(f"  3. Review {data_dir_name}/product_context.md — improve if needed")
    print(f"  4. Add tasks to {data_dir_name}/backlog.md")
    print(f"  5. Write specs in {data_dir_name}/specs/features/")
    print(f"  6. Run: python -m multiagent --list")
    print(f"  7. Run: python -m multiagent --next")
    return True


async def _run_analysis(detection):
    print("\nAnalyzing project with AI (this may take a minute)...")
    from ..analyzer.analyze import analyze
    analysis = await analyze(PROJECT_ROOT, detection)
    print("  Done!")
    return analysis


def _create_data_dir(data_dir: Path, detection):
    """Create the data directory structure."""
    data_dir.mkdir(exist_ok=True)
    specs_dir = data_dir / "specs"
    specs_dir.mkdir(exist_ok=True)
    for subdir in ["audit", "features", "tech_debt", "refactor", "bugfix"]:
        (specs_dir / subdir).mkdir(exist_ok=True)

    now = datetime.now().strftime("%Y-%m-%d")
    template_vars = defaultdict(str, project_name=detection.name, date=now)

    for tmpl_name, target_name in [
        ("backlog.md", "backlog.md"),
        ("registry.md", "registry.md"),
        ("agent_insights.md", "agent_insights.md"),
    ]:
        target = data_dir / target_name
        if not target.exists():
            template = (TEMPLATES_DIR / tmpl_name).read_text(encoding="utf-8")
            target.write_text(template.format_map(template_vars), encoding="utf-8")


def _generate_toml(toml_path: Path, detection, analysis, data_dir_name: str):
    """Generate multiagent.toml from detection + analysis."""
    template = (TEMPLATES_DIR / "multiagent.toml").read_text(encoding="utf-8")

    replacements = {
        "{project_name}": detection.name,
        "{project_description}": analysis.tech_stack_oneliner or f"{detection.framework or detection.language} application",
        "{app_dir}": detection.app_dir,
        "{data_dir}": data_dir_name,
    }

    content = template
    for placeholder, value in replacements.items():
        content = content.replace(placeholder, str(value))

    # Adjust quality gates based on detection
    if not detection.has_typescript:
        # Remove tsc gate, use lint or empty
        if detection.lint_command:
            pm = detection.package_manager or "npm"
            prefix = f"cd {detection.app_dir} && " if detection.app_dir != "." else ""
            content = content.replace(
                f'fast = "cd {detection.app_dir} && npx tsc --noEmit"',
                f'fast = "{prefix}{pm} run {detection.lint_command}"',
            )
        else:
            content = content.replace(
                f'fast = "cd {detection.app_dir} && npx tsc --noEmit"',
                f'# fast = ""  # No type checker detected',
            )

    if not detection.build_command:
        content = content.replace(
            f'full = "cd {detection.app_dir} && npm run build"',
            f'# full = ""  # No build command detected',
        )
    else:
        pm = detection.package_manager or "npm"
        prefix = f"cd {detection.app_dir} && " if detection.app_dir != "." else ""
        content = content.replace(
            f'full = "cd {detection.app_dir} && npm run build"',
            f'full = "{prefix}{pm} run {detection.build_command}"',
        )

    # Use analysis quality gates if AI provided better ones
    if analysis.quality_gates_fast:
        for line in content.splitlines():
            if line.strip().startswith("fast ="):
                content = content.replace(line, f'fast = "{analysis.quality_gates_fast}"')
                break
    if analysis.quality_gates_full:
        for line in content.splitlines():
            if line.strip().startswith("full ="):
                content = content.replace(line, f'full = "{analysis.quality_gates_full}"')
                break

    # Dev command from detection
    if detection.dev_command:
        pm = detection.package_manager or "npm"
        content = content.replace(
            'dev_command = "npm run dev -- -p {port}"',
            f'dev_command = "{pm} run {detection.dev_command} -- -p {{port}}"',
        )

    # Visual test pages from analysis
    if analysis.visual_test_pages and analysis.visual_test_pages != ["/"]:
        pages_str = ", ".join(f'"{p}"' for p in analysis.visual_test_pages)
        content = content.replace('pages = ["/"]', f"pages = [{pages_str}]")

    # Protected paths from analysis
    if analysis.protected_paths:
        existing_paths_section = content.split("[protected_paths]")[1].split("[writable_paths]")[0]
        # Add analysis-suggested paths to the list
        for path in analysis.protected_paths:
            path_entry = f'    "{path}",'
            if path_entry not in existing_paths_section and path not in existing_paths_section:
                content = content.replace(
                    '    "{data_dir}/specs/_*",\n]',
                    f'    "{{data_dir}}/specs/_*",\n    "{path}",\n]',
                )

    # Writable paths from analysis
    if analysis.writable_paths:
        writable_str = ", ".join(f'"{p}"' for p in analysis.writable_paths)
        content = content.replace(
            f'paths = ["{detection.app_dir}/", "docs/"]',
            f"paths = [{writable_str}]",
        )

    toml_path.write_text(content, encoding="utf-8")


def _write_context_files(data_dir: Path, detection, analysis):
    """Write AI-generated context files."""
    now = datetime.now().strftime("%Y-%m-%d")

    # product_context.md
    pc_path = data_dir / "product_context.md"
    if analysis.product_context:
        header = f"# {detection.name} — Product Context\n\n"
        footer = f"\n\n---\n\n*Generated by multiagent init on {now}. Edit this file to improve agent understanding.*\n"
        pc_path.write_text(header + analysis.product_context + footer, encoding="utf-8")
    elif not pc_path.exists():
        tmpl = (TEMPLATES_DIR / "product_context.md").read_text(encoding="utf-8")
        pc_path.write_text(tmpl.format_map(defaultdict(str,
            project_name=detection.name,
            product_summary="(describe your product here)",
            target_audience="(describe your audience)",
            key_features="- (list key features)",
            architecture_overview="(describe architecture)",
            date=now,
        )), encoding="utf-8")

    # _project-conventions.md
    conv_path = data_dir / "specs" / "_project-conventions.md"
    if analysis.project_conventions:
        header = f"# Project Conventions — {detection.name}\n\n"
        footer = f"\n\n---\n\n*Generated by multiagent init on {now}. Edit this file to improve agent code quality.*\n"
        conv_path.write_text(header + analysis.project_conventions + footer, encoding="utf-8")
    elif not conv_path.exists():
        tmpl = (TEMPLATES_DIR / "_project-conventions.md").read_text(encoding="utf-8")
        conv_path.write_text(tmpl.format_map(defaultdict(str,
            project_name=detection.name,
            tech_stack=analysis.tech_stack_oneliner or "(describe your stack)",
            project_structure="(describe directory layout)",
            key_patterns="(describe coding patterns)",
            gotchas="(list known gotchas)",
            date=now,
        )), encoding="utf-8")

    # agent_insights.md — write gotchas if detected
    insights_path = data_dir / "agent_insights.md"
    if analysis.gotchas and insights_path.exists():
        content = insights_path.read_text(encoding="utf-8")
        if "*(Insights will be added here" in content:
            gotchas_text = "\n".join(f"- {g}" for g in analysis.gotchas)
            content = content.replace(
                "*(Insights will be added here as agents work on the project)*",
                gotchas_text,
            )
            insights_path.write_text(content, encoding="utf-8")


def _update_claude_md(data_dir_name: str):
    """Add or update the multiagent section in CLAUDE.md."""
    import re
    import tomllib

    claude_md = PROJECT_ROOT / "CLAUDE.md"
    template = (TEMPLATES_DIR / "CLAUDE_SECTION.md").read_text(encoding="utf-8")

    # Read config values from multiagent.toml for template vars
    toml_path = PROJECT_ROOT / "multiagent.toml"
    main_branch = "main"
    dev_branch = "auto-dev"
    if toml_path.exists():
        with open(toml_path, "rb") as f:
            toml_data = tomllib.load(f)
        git_cfg = toml_data.get("git", {})
        main_branch = git_cfg.get("main_branch", "main")
        dev_branch = git_cfg.get("dev_branch", "auto-dev")

    # Detect how multiagent is invoked relative to project root
    multiagent_rel = MULTIAGENT_DIR.relative_to(PROJECT_ROOT)
    run_prefix = f"python -m {str(multiagent_rel).replace('/', '.')}"

    section = template.format_map({
        "data_dir": data_dir_name,
        "run_prefix": run_prefix,
        "main_branch": main_branch,
        "dev_branch": dev_branch,
    })

    start_marker = "<!-- multiagent:start -->"
    end_marker = "<!-- multiagent:end -->"

    if claude_md.exists():
        content = claude_md.read_text(encoding="utf-8")
        if start_marker in content:
            # Replace existing section
            pattern = re.escape(start_marker) + r".*?" + re.escape(end_marker)
            content = re.sub(pattern, section.strip(), content, flags=re.DOTALL)
            print("  Updated existing multiagent section in CLAUDE.md")
        else:
            # Append section
            content = content.rstrip() + "\n\n" + section
            print("  Added multiagent section to CLAUDE.md")
        claude_md.write_text(content, encoding="utf-8")
    else:
        # Create new file with just the section
        header = f"# {PROJECT_ROOT.name} — Instructions for Claude\n\n"
        claude_md.write_text(header + section, encoding="utf-8")
        print("  Created CLAUDE.md")


def _update_gitignore():
    """Add multiagent/output/ to .gitignore if not already there."""
    gitignore = PROJECT_ROOT / ".gitignore"
    marker = "multiagent/output/"

    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8")
        if marker not in content:
            content += f"\n# Multi-agent runtime artifacts\n{marker}\n"
            gitignore.write_text(content, encoding="utf-8")
            print("  Updated .gitignore")
    else:
        gitignore.write_text(f"# Multi-agent runtime artifacts\n{marker}\n", encoding="utf-8")
        print("  Created .gitignore")
