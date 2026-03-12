# Multi-Agent Orchestrator

Autonomous task execution system powered by Claude. Reads a backlog,
picks tasks by priority, writes code through coordinated AI agents,
runs quality gates, and commits to feature branches for human review.

## Quick Start

See [Getting Started](getting-started.md) for the full walkthrough.

```bash
# 1. Install
cd multiagent && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Initialize for your project
cd /your-project && multiagent/.venv/bin/python -m multiagent init

# 3. Add tasks to agents_data/backlog.md

# 4. Run
python -m multiagent --next
```

## How It Works

```
Backlog → Pick task → Create branch → Enrich spec → Build plan → Implement → QG → Review → Commit
           │                            │                          │           │
           ▼                            ▼                          ▼           ▼
     task_loader.py              Product Agent              Implementor   Reviewer
                                 Analyst Agent              (per step)    Visual Tester
```

1. **Task selection** — highest priority actionable task from the backlog
2. **Branch creation** — `auto/{task_id}` from `auto-dev`
3. **Spec enrichment** — Product + Analyst agents fill in UX, edge cases, technical plan
4. **Implementation** — Implementor writes code step by step, quality gates after each step
5. **Review** — Reviewer checks the full diff, Visual Tester captures screenshots
6. **Commit** — changes committed on the feature branch for human review

## Agents

| Agent | Model | Role |
|-------|-------|------|
| **Orchestrator** | Opus | Plans, delegates, verifies. Never writes code. |
| **Product** | Sonnet | Defines UX flows, edge cases, scope for feature specs |
| **Analyst** | Sonnet | Reads codebase, writes technical approach and implementation plan |
| **Implementor** | Sonnet | Writes code per plan, follows existing patterns |
| **Reviewer** | Sonnet | Reviews diff for bugs, security issues, pattern violations |
| **Visual Tester** | Sonnet | Captures screenshots before/after, checks for regressions |

## Commands

| Command | Description |
|---------|-------------|
| `python -m multiagent init` | Initialize for current project |
| `python -m multiagent --list` | List all tasks with status |
| `python -m multiagent --next` | Run next priority task |
| `python -m multiagent --task ID` | Run specific task by ID |
| `python -m multiagent --resume` | Resume interrupted task |
| `python -m multiagent --batch` | Run all tasks sequentially |
| `python -m multiagent --batch --phase 2` | Run tasks from a specific phase |
| `python -m multiagent --mode supervised` | Override autonomy mode |
| `python -m multiagent.server` | Start web dashboard |

## Documentation

### User Guides

| Document | Description |
|----------|-------------|
| [Getting Started](getting-started.md) | Installation, initialization, first task (10 min) |
| [Configuration Reference](configuration.md) | Complete `multiagent.toml` reference — all sections and options |
| [Backlog & Spec Format](backlog-format.md) | Backlog table format, spec file structure, multiple sources |
| [Autonomy Modes](autonomy-modes.md) | Supervised, batch, autonomous modes and human checkpoints |
| [Troubleshooting](troubleshooting.md) | Common issues, error diagnosis, performance tuning |

### Technical Reference

| Document | Description |
|----------|-------------|
| [Architecture](architecture.md) | Module map, data flow, state persistence, design decisions |
| [Pipeline Deep Dive](pipeline-deep-dive.md) | Step-by-step execution flow, error recovery, output markers |
| [Agents & Prompts](agents-and-prompts.md) | Agent definitions, prompt templates, customization |
| [Safety & Guardrails](safety-and-guardrails.md) | Protected paths, quality gates, cost control, rate limiting |
| [Extending](extending.md) | Adding agents, gates, sources, task types, server dashboard |

### Historical

| Document | Description |
|----------|-------------|
| [Design Document](design_multiagent.md) | Original architecture design (historical reference) |

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
    init.py                Project initialization
  analyzer/
    detect.py              Filesystem project detection
    analyze.py             AI-powered project analysis
  prompts/                 Agent prompt templates (Markdown)
  templates/               Scaffold templates for init
  server/
    app.py                 FastAPI web dashboard
    process_manager.py     Agent subprocess lifecycle
  output/                  Runtime artifacts (gitignored)
  docs/                    This documentation
```
