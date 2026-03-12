# Multiagent — Instructions for Claude

## What is this

Autonomous task execution system powered by Claude. Reads a backlog, picks tasks by priority, coordinates 6 AI agents (Orchestrator → Product → Analyst → Implementor → Reviewer → Visual Tester), runs quality gates, and commits to feature branches for human review.

Designed to be installed into any project via `python -m multiagent init`.

## Language

Code, comments, commits — English.

## Architecture

```
multiagent/
  __main__.py          CLI entry point (init, --next, --task, --list, --batch)
  config.py            All settings (populated from multiagent.toml at import)
  project_config.py    TOML loader, auto-detection fallback
  core/                Engine
    pipeline.py        Standard task pipeline (branch → enrich → implement → review → commit)
    orchestrator.py    High-level commands (run_next, run_batch, etc.)
    agents.py          Agent definitions and Claude API calls
    prompt_builder.py  Prompt construction, spec discovery
    task_loader.py     Backlog parser (Markdown tables → Task objects)
    state.py           State persistence for resume support
    guardrails.py      Protected path enforcement
    quality_gates.py   Build/lint gates, screenshots
    git.py             Git operations (branch, commit, checkout)
    retry.py           Rate limit handling with exponential backoff
    spec_manager.py    Spec CRUD, versioning
    init.py            Project initialization logic
  analyzer/            Project detection + AI analysis (used by init)
  prompts/             Agent system prompts (Markdown templates)
  templates/           Scaffold templates for init (toml, backlog, contexts)
  server/              FastAPI web dashboard + process manager
  docs/                Full documentation (11 files)
```

## Key concepts

- **Specs** — Markdown files describing tasks. Statuses: stub → partial → full. Agents enrich stubs before implementation. See `docs/backlog-format.md`.
- **Foundational specs** — files prefixed with `_` in specs/, loaded into every agent's context (conventions, architecture).
- **Quality gates** — shell commands (tsc, build) run after each implementation step. Configured in `multiagent.toml`.
- **Autonomy modes** — supervised (confirm each step), batch (confirm per-task), autonomous (hands-off). See `docs/autonomy-modes.md`.

## Key documentation

| Doc | When to read |
|-----|-------------|
| `docs/README.md` | Overview, commands, file structure |
| `docs/getting-started.md` | Installation, first task walkthrough |
| `docs/configuration.md` | Complete `multiagent.toml` reference |
| `docs/backlog-format.md` | Backlog table format, spec structure, statuses |
| `docs/architecture.md` | Module map, data flow, design decisions |
| `docs/pipeline-deep-dive.md` | Step-by-step execution, error recovery |
| `docs/agents-and-prompts.md` | Agent roles, prompt templates, customization |
| `docs/safety-and-guardrails.md` | Protected paths, cost control, rate limits |
| `docs/extending.md` | Adding agents, gates, sources, task types |

## Common commands

```bash
python -m multiagent init              # Initialize for a project
python -m multiagent --list            # List tasks
python -m multiagent --next            # Run next priority task
python -m multiagent --task FE5        # Run specific task
python -m multiagent --batch           # Run all tasks sequentially
python -m multiagent.server            # Start web dashboard
```

## Development workflow

1. Read the relevant code before modifying. Follow existing patterns.
2. `config.py` + `project_config.py` — all settings flow from `multiagent.toml`. Don't hardcode values.
3. Agent prompts live in `prompts/` as Markdown. Variables are injected by `prompt_builder.py`.
4. `templates/` contains scaffolds for `init` — edit these to change what new projects get.
5. Protected paths in `config.PROTECTED_PATHS` — agents can't modify these files.

## Critical patterns

- **Config-driven.** Everything configurable lives in `multiagent.toml`. Code reads from `config.*` module-level vars.
- **Spec discovery.** `prompt_builder.find_task_spec()` searches by task ID across type directories, returns highest version.
- **Pipeline stages.** `pipeline.py` runs: branch → spec enrichment → implementation steps → quality gates → review → commit. Each stage is resumable via `state.py`.
- **Rate limit retry.** `retry.py` handles Claude API rate limits with exponential backoff. Config: `RATE_LIMIT_*` vars.
