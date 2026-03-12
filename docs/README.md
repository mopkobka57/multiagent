# Documentation

> **Looking for the project overview?** See the [main README](../README.md).

## User Guides

| Document | Description |
|----------|-------------|
| [Getting Started](getting-started.md) | Installation, initialization, first task (10 min) |
| [Writing Specs](writing-specs.md) | Creating task specs — Claude Code, CLI, manual, tips for good specs |
| [Web Dashboard](dashboard.md) | Task list, spec editor, groups, archive, scheduling |
| [Configuration Reference](configuration.md) | Complete `multiagent.toml` reference — all sections and options |
| [Backlog & Spec Format](backlog-format.md) | Backlog table format, spec file structure, multiple sources |
| [Autonomy Modes](autonomy-modes.md) | Supervised, batch, autonomous modes and human checkpoints |
| [Troubleshooting](troubleshooting.md) | Common issues, error diagnosis, performance tuning |

## Technical Reference

| Document | Description |
|----------|-------------|
| [Architecture](architecture.md) | Module map, data flow, state persistence, design decisions |
| [Pipeline Deep Dive](pipeline-deep-dive.md) | Step-by-step execution flow, error recovery, output markers |
| [Agents & Prompts](agents-and-prompts.md) | Agent definitions, prompt templates, customization |
| [Safety & Guardrails](safety-and-guardrails.md) | Protected paths, quality gates, cost control, rate limiting |
| [Extending](extending.md) | Adding agents, gates, sources, task types, server dashboard |

## Historical

| Document | Description |
|----------|-------------|
| [Design Document](design_multiagent.md) | Original architecture design (historical reference) |

## Commands

| Command | Description |
|---------|-------------|
| `python -m multiagent init` | Initialize for current project |
| `python -m multiagent spec "desc"` | Create task spec from description, file (`-f`), or stdin |
| `python -m multiagent.server` | Start web dashboard |
| `python -m multiagent --list` | List all tasks with status |
| `python -m multiagent --next` | Run next priority task |
| `python -m multiagent --task ID` | Run specific task by ID |
| `python -m multiagent --resume` | Resume interrupted task |
| `python -m multiagent --batch` | Run all tasks sequentially |
| `python -m multiagent --batch --phase 2` | Run tasks from a specific phase |
| `python -m multiagent --mode supervised` | Override autonomy mode |

## Git Strategy

```
main                    ← stable, human-controlled
  └── auto-dev          ← staging for automated work
        ├── auto/FE5    ← feature branch per task
        ├── auto/TD2    ← isolated from other tasks
        └── ...
```

Agents never push to any remote. Human merges `auto-dev` into `main`.

## File Structure

```
multiagent/
  __main__.py              CLI entry point
  config.py                Settings (from multiagent.toml)
  project_config.py        TOML config loader
  core/
    pipeline.py            Standard task pipeline
    orchestrator.py        High-level commands (run_next, run_batch, etc.)
    agents.py              Agent definitions and prompt rendering
    prompt_builder.py      Orchestrator prompt construction
    task_loader.py         Backlog parser
    state.py               State persistence (resume support)
    guardrails.py          Protected path enforcement
    quality_gates.py       Build/lint gates, screenshots
    git.py                 Git operations (branch, commit, checkout)
    retry.py               Rate limit handling with backoff
    registry.py            Registry and insights management
    audit.py               Audit pipeline (read-only)
    archive.py             Execution history archive
    sources.py             Multi-source backlog management
    groups.py              Spec group execution
    scheduler.py           Timer-based deferred execution
    spec_manager.py        Spec CRUD operations
    spec_creator.py        AI-powered spec generation from descriptions
    init.py                Project initialization
  analyzer/
    detect.py              Filesystem project detection
    analyze.py             AI-powered project analysis
  prompts/                 Agent prompt templates (Markdown)
  templates/               Scaffold templates for init
  server/
    app.py                 FastAPI web dashboard + REST API + WebSocket
    process_manager.py     Agent subprocess lifecycle + queue
    parsers.py             Backlog + state enrichment for API
    spec_editor.py         AI-powered spec editing
    static/                SPA frontend (Alpine.js + Tailwind)
  output/                  Runtime artifacts (gitignored)
  docs/                    This documentation
```
