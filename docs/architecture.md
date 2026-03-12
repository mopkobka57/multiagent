# System Architecture

Technical reference for the internal architecture of the Multi-Agent Orchestrator.
For developers extending the system or understanding how the pieces fit together.

## Overview

```
                        ┌──────────────────────┐
                        │    CLI (__main__)     │
                        │  or Server (FastAPI)  │
                        └──────────┬───────────┘
                                   │
                        ┌──────────▼───────────┐
                        │     Orchestrator      │
                        │  (entry points, CLI   │
                        │   arg dispatch)       │
                        └──────────┬───────────┘
                                   │
                   ┌───────────────▼───────────────┐
                   │          Pipeline             │
                   │  (standard task execution)    │
                   │  or Audit (read-only flow)    │
                   └───┬──────┬──────┬──────┬──────┘
                       │      │      │      │
              ┌────────▼──┐ ┌─▼────┐ │  ┌───▼──────┐
              │PromptBuild│ │Agents│ │  │QualityGate│
              └────────┬──┘ └─┬────┘ │  └───┬──────┘
                       │      │      │      │
                       ▼      ▼      ▼      ▼
              ┌─────────────────────────────────────┐
              │        Claude Agent SDK             │
              │  (query → Orchestrator → Subagents) │
              └─────────────────────────────────────┘
```

The system follows a linear pipeline architecture. The CLI or server dispatches
to the Orchestrator, which invokes `pipeline.run_task()`. The pipeline builds
a prompt, streams it through the Claude Agent SDK, and applies guardrails,
quality gates, and visual testing around the agent execution.

## Module Map

```
multiagent/
├── __init__.py              # Package marker
├── __main__.py              # CLI entry point (argparse)
├── config.py                # Global settings (module-level variables)
├── project_config.py        # TOML loader → populates config.py
│
├── core/
│   ├── __init__.py
│   ├── init.py              # `multiagent init` — project bootstrapping
│   ├── orchestrator.py      # High-level commands: run_next, run_batch, list
│   ├── pipeline.py          # Standard task pipeline (full flow)
│   ├── audit.py             # Audit pipeline (read-only analysis)
│   ├── prompt_builder.py    # Prompt construction + context loading
│   ├── agents.py            # Agent definitions (5 subagents)
│   ├── task_loader.py       # Parses backlog.md into Task objects
│   ├── sources.py           # Multi-source backlog management
│   ├── groups.py            # Spec groups (sequential execution)
│   ├── scheduler.py         # Timer-based deferred execution
│   ├── state.py             # State persistence (JSON + FileLock)
│   ├── git.py               # Git operations (branch, commit, merge)
│   ├── guardrails.py        # Protected path enforcement
│   ├── quality_gates.py     # Code quality checks + visual testing
│   ├── retry.py             # Rate limit handling with backoff
│   ├── registry.py          # Task log (registry.md) + insights
│   ├── archive.py           # Completed/failed run archive (JSON)
│   └── spec_manager.py      # Spec file CRUD operations
│
├── analyzer/
│   ├── __init__.py
│   ├── detect.py            # Filesystem-based project detection
│   └── analyze.py           # AI-powered deep project analysis
│
├── server/
│   ├── __init__.py
│   ├── __main__.py          # Server entry point
│   ├── app.py               # FastAPI app, WebSocket hub, watchers
│   ├── process_manager.py   # Agent subprocess lifecycle + queue
│   ├── parsers.py           # Task enrichment for API responses
│   ├── spec_editor.py       # AI-powered spec editing
│   └── static/              # Web dashboard (HTML + JS)
│
├── prompts/                 # System prompt templates (Markdown)
│   ├── orchestrator_system.md
│   ├── product_system.md
│   ├── analyst_system.md
│   ├── implementor_system.md
│   ├── reviewer_system.md
│   └── visual_tester_system.md
│
├── templates/               # Scaffold templates for `init`
│   ├── multiagent.toml
│   ├── backlog.md
│   ├── registry.md
│   ├── product_context.md
│   ├── agent_insights.md
│   └── _project-conventions.md
│
├── output/                  # Runtime artifacts (gitignored)
│   ├── state.json           # Current orchestrator state
│   ├── archive.json         # Completed/failed run history
│   ├── sources.json         # Custom backlog sources
│   ├── groups.json          # Spec group definitions
│   ├── schedules.json       # Deferred execution schedules
│   ├── server_runs.json     # Server process tracking
│   ├── logs/                # Per-task execution logs
│   │   └── {task_id}/
│   │       ├── live.log     # Real-time output (server watches)
│   │       ├── execution.log# Full agent output
│   │       ├── gates.log    # Quality gate results
│   │       ├── visual.log   # Visual test report
│   │       ├── report.md    # Structured task report
│   │       └── exit_status.json  # Rate limit signal
│   └── screenshots/         # Visual regression screenshots
│       └── {task_id}/
│           ├── before/
│           └── after/
│
└── docs/                    # Documentation (you are here)
```

## Data Flow

A task goes through these stages from backlog to commit:

```
backlog.md                 multiagent_specs/specs/
    │                           │
    ▼                           ▼
┌──────────┐            ┌──────────────┐
│TaskLoader│            │PromptBuilder │
│ parse()  │            │ find_spec()  │
└────┬─────┘            │ load_context │
     │                  └──────┬───────┘
     ▼                         │
┌──────────┐                   │
│Orchestrat│                   │
│ or picks │                   │
│ next task│                   │
└────┬─────┘                   │
     │                         ▼
     │              ┌─────────────────────┐
     └─────────────►│ build_orchestrator  │
                    │ _prompt()           │
                    └────────┬────────────┘
                             │
                             ▼
                    ┌─────────────────────┐
                    │  Claude Agent SDK   │
                    │  query() → stream   │
                    │                     │
                    │  Orchestrator ──────┼──► Product Agent
                    │       │             │         │
                    │       ▼             │         ▼
                    │  Analyst Agent      │  Implementor Agent
                    │       │             │         │
                    │       ▼             │         ▼
                    │  Reviewer Agent     │  Visual Tester
                    └────────┬────────────┘
                             │
                             ▼
                    ┌─────────────────────┐
                    │  Post-processing    │
                    │  • Guardrails check │
                    │  • Quality gates    │
                    │  • Visual regression│
                    │  • Insight extract  │
                    │  • Registry update  │
                    │  • Git commit       │
                    └─────────────────────┘
```

## State & Persistence

All state is file-based (no database). Thread safety is provided by `filelock`.

| File | Location | Purpose | Format |
|------|----------|---------|--------|
| `state.json` | `output/` | Current task, completed/failed lists, cost, audit history | JSON |
| `archive.json` | `output/` | Run history that survives branch switches | JSON array |
| `sources.json` | `output/` | Custom backlog source definitions | JSON array |
| `groups.json` | `output/` | Spec group definitions and progress | JSON array |
| `schedules.json` | `output/` | Deferred execution timers | JSON array |
| `server_runs.json` | `output/` | Server process tracking for orphan recovery | JSON |
| `registry.md` | `multiagent_specs/` | Human-readable execution log (Markdown table) | Markdown |
| `agent_insights.md` | `multiagent_specs/` | Knowledge base of gotchas for agents | Markdown |
| `backlog.md` | `multiagent_specs/` | Task backlog with priorities | Markdown table |

### State Lifecycle

```
load_state()    →  OrchestratorState (dataclass)  →  save_state()
                   ├── current_task: TaskState
                   │   ├── task_id, branch, status
                   │   ├── steps: list[StepState]
                   │   └── started_at, updated_at
                   ├── completed_tasks: list[str]
                   ├── failed_tasks: list[str]
                   ├── total_cost_usd: float
                   └── audit_history: dict[str, list[str]]
```

State is saved after every significant event (task start, step completion, failure).
`atomic_state_update()` provides read-modify-write under a single lock.

## Config Loading

```
__main__.py
    │
    ├── import config          # Module-level defaults in config.py
    │       │
    │       └── from .project_config import load
    │               │
    │               ├── Read multiagent.toml
    │               │   └── _load_from_toml() → populates config.* attrs
    │               │
    │               └── OR fallback: _try_auto_detect()
    │                   └── Looks for multiagent_specs/ next to multiagent/
    │
    └── CLI args (--mode) can override config at runtime
```

**Priority:** `multiagent.toml` > auto-detection of `multiagent_specs/` > hardcoded defaults

The `config.py` module exposes all settings as module-level variables. Other modules
import them directly: `from ..config import PROJECT_ROOT, QUALITY_GATES`.

## Key Design Decisions

### Opus for Orchestrator, Sonnet for Subagents

The Orchestrator needs to understand complex task descriptions, make delegation
decisions, and coordinate multi-step plans. Opus provides the reasoning capability
for this. Subagents (Product, Analyst, Implementor, Reviewer, Visual Tester) perform
focused tasks where Sonnet's speed and cost efficiency are more appropriate.

### Streaming + Output Parsing (Not Structured Output)

The agent output uses text markers (`[TASK_SUMMARY]`, `[NEW INSIGHT]`, `[USER_NOTICE]`,
`[NEW TASK]`) parsed with regex after execution. This is simpler and more flexible
than structured output schemas, and allows the Orchestrator to produce natural
language alongside machine-readable markers.

### File-Based State (Not Database)

All persistence uses JSON files with `filelock`. This makes the system:
- Zero-dependency (no DB setup)
- Portable (copy the directory)
- Debuggable (edit JSON by hand)
- Git-friendly (`output/` is gitignored, but `registry.md` is tracked)

### Git Branch Per Task

Each task gets its own feature branch (`auto/{task_id}`), created from `auto-dev`.
This provides:
- Isolation between tasks
- Easy rollback (delete the branch)
- Clean diffs for review
- No risk to `main` (human merges `auto-dev` → `main`)

### Single-Agent Execution

Only one agent subprocess runs at a time (enforced by `ProcessManager`). This
prevents git conflicts and resource contention. Additional tasks are queued.

## Module Dependencies

```
__main__ ──► config ◄── project_config
               │
               ▼
          orchestrator ──► pipeline ──┬──► prompt_builder
                │                    ├──► agents
                │                    ├──► quality_gates
                │                    ├──► guardrails
                │                    ├──► git
                │                    ├──► registry
                │                    ├──► archive
                │                    ├──► retry
                │                    ├──► state
                │                    └──► task_loader ──► sources
                │
                └──► audit (parallel to pipeline, simpler flow)

server/app ──► process_manager ──► git, groups, archive, registry
          ──► parsers ──► task_loader, state, sources
          ──► spec_editor
          ──► scheduler

analyzer/detect ──► (no internal deps)
analyzer/analyze ──► detect
```

## Related Documentation

- [Configuration Reference](configuration.md) — all `multiagent.toml` options
- [Pipeline Deep Dive](pipeline-deep-dive.md) — step-by-step execution flow
- [Agents & Prompts](agents-and-prompts.md) — agent definitions and prompt system
- [Safety & Guardrails](safety-and-guardrails.md) — defense-in-depth layers
- [Extending](extending.md) — how to add agents, gates, and sources
