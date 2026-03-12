# Getting Started

From zero to your first autonomous task in 10 minutes.
This guide covers installation, initialization, and running your first task.

## Prerequisites

- **Python 3.11+** (for `tomllib` support)
- **Claude API key** — set `ANTHROPIC_API_KEY` in your environment
- **Git repository** — the orchestrator uses git branches for isolation
- **Node.js** (optional) — only needed if your project uses it for quality gates

## Installation

### As Git Submodule (Recommended)

```bash
git submodule add <repo-url> multiagent
cd multiagent
python -m venv .venv
source .venv/bin/activate    # or .venv/Scripts/activate on Windows
pip install -r requirements.txt
```

### As Standalone Directory

```bash
# Copy or clone the multiagent directory into your project root
cp -r /path/to/multiagent ./multiagent

cd multiagent
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Dependencies

The `requirements.txt` installs:

| Package | Purpose |
|---------|---------|
| `claude-agent-sdk` | Claude API agent framework |
| `fastapi` | Web dashboard server |
| `filelock` | Thread-safe file locking |
| `websockets` | Real-time dashboard updates |

## Initialize

From your **project root** (not inside `multiagent/`):

```bash
multiagent/.venv/bin/python -m multiagent init
```

This does three things:

1. **Detects** your project structure (language, framework, package manager)
2. **Analyzes** your codebase with AI (reads key files, generates context)
3. **Creates** configuration and data files

Example output:

```
Detecting project structure...
  Name: my-app
  Language: typescript
  Framework: nextjs
  App dir: .
  Package manager: npm

Analyzing project with AI (this may take a minute)...
  Done!

Creating multiagent_specs/...
Generating multiagent.toml...
Writing context files...

==================================================
Initialization complete!
==================================================

Created:
  multiagent.toml
  multiagent_specs/backlog.md
  multiagent_specs/registry.md
  multiagent_specs/agent_insights.md
  multiagent_specs/product_context.md
  multiagent_specs/specs/_project-conventions.md
```

### Init Options

```bash
# Skip AI analysis (use defaults)
python -m multiagent init --non-interactive

# Re-run analysis only (keep existing files)
python -m multiagent init --refresh

# Custom data directory name
python -m multiagent init --data-dir my_data
```

## Review Generated Files

After init, review these files and improve them:

### `multiagent.toml`

The main configuration file. Key things to verify:

- `[project]` — correct app directory and description
- `[quality_gates]` — commands work when run manually
- `[visual_testing]` — dev server command and pages to test
- `[protected_paths]` — critical files that agents must never touch

See [Configuration Reference](configuration.md) for all options.

### `multiagent_specs/product_context.md`

What the AI understood about your project. The better this document, the
better agents understand your codebase. Edit it to add context about:

- Business logic and domain concepts
- Architecture decisions and their reasons
- Key patterns and conventions

### `multiagent_specs/specs/_project-conventions.md`

Technical conventions that all agents follow. Add your:

- Code style preferences
- Architecture patterns
- Import conventions
- Testing approaches

## Add Your First Task

Edit `multiagent_specs/backlog.md` and add a task row:

```markdown
## Phase 1: Initial

| ID | Name | Type | Importance | Complexity | Deleg. | Spec | Human | Description |
|---|---|---|---|---|---|---|---|---|
| FE1 | Add dark mode toggle | feature | 3 | 2 | high | stub | auto | Add a dark/light mode toggle to the settings page |
```

Then create a spec file at `multiagent_specs/specs/features/FE1-add-dark-mode-toggle.md`:

```markdown
# Add Dark Mode Toggle

**Task ID:** FE1
**Type:** feature
**Spec Status:** stub

---

## Overview

Add a dark/light mode toggle to the settings page. Should persist the
user's preference in localStorage.
```

See [Backlog Format](backlog-format.md) for the full format reference.

## Run

### List Tasks

```bash
multiagent/.venv/bin/python -m multiagent --list
```

Output:

```
ID                        Source       Pri    Cplx   Status          Title
----------------------------------------------------------------------------------------------------
FE1                       feature      0.99   2      pending         Add dark mode toggle

Total: 1 | Done: 0 | Failed: 0
Autonomy mode: batch
Human checkpoints: ['pr_review']
```

### Run Next Task

```bash
multiagent/.venv/bin/python -m multiagent --next
```

### Run a Specific Task

```bash
multiagent/.venv/bin/python -m multiagent --task FE1
```

## What Happens During Execution

When you run a task, here's the full flow:

1. **Human checkpoint** — In supervised/batch mode, asks for approval
2. **Branch creation** — Creates `auto/FE1` from `auto-dev`
3. **Baseline screenshots** — Captures screenshots of key pages (before changes)
4. **Context loading** — Loads product context, insights, foundational specs
5. **Prompt building** — Constructs the Orchestrator prompt with task, spec, and context
6. **Orchestrator execution** — Claude Opus plans and delegates to subagents:
   - **Product Agent** — expands UX flows and edge cases (for feature stubs)
   - **Analyst Agent** — reads codebase, writes technical implementation plan
   - **Implementor Agent** — writes code step by step
   - **Quality Gates** — type check / lint after each step
   - **Reviewer Agent** — reviews the full diff
   - **Visual Tester** — captures post-change screenshots
7. **Post-processing** — Guardrails check, final quality gates, visual regression
8. **Git commit** — Commits all changes on the feature branch
9. **Registry update** — Records result in `registry.md` and `archive.json`

The task runs autonomously. Watch the output for progress. Use `Ctrl+C` to
interrupt — state is saved and the task can be resumed with `--resume`.

## Next Steps

- [Configuration Reference](configuration.md) — fine-tune your setup
- [Backlog Format](backlog-format.md) — task and spec format details
- [Autonomy Modes](autonomy-modes.md) — control how much human oversight you want
- [Pipeline Deep Dive](pipeline-deep-dive.md) — understand the full execution flow
- [Troubleshooting](troubleshooting.md) — common issues and solutions
