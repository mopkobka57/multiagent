# Multi-Agent Orchestrator

A full development pipeline, not just a coding assistant.

Describe tasks in a backlog — six AI agents handle the rest: spec enrichment, architecture planning, implementation with quality gates, code review, visual testing. Every task gets its own branch. You review and merge.

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![GitHub last commit](https://img.shields.io/github/last-commit/mopkobka57/multiagent)](https://github.com/mopkobka57/multiagent/commits/main)
[![Donate](https://img.shields.io/badge/Donate-ETH%20%2F%20USDT%20%2F%20USDC-8247e5?style=flat&logo=ethereum)](#donate)

![Overview](docs/screenshots/overview.png)

## Why Multiagent

**Multi-agent pipeline, not a single chatbot.** Copilot, Cursor, Claude Code, Aider — each runs one generalist agent. Multiagent coordinates 6 specialized agents (Orchestrator, Product, Analyst, Implementor, Reviewer, Visual Tester) through a defined pipeline with each agent scoped to its role.

**Quality gates enforced between every step.** No existing tool enforces build/lint/test checks as mandatory stage gates within the agent workflow. Multiagent runs your configured gates (tsc, eslint, build) automatically between pipeline stages and blocks on failure.

**Spec-driven, not prompt-driven.** Other tools start from a chat message. Multiagent works from structured Markdown specs with tracked statuses, priority scoring, and automatic enrichment. You manage a backlog, not a conversation.

**Autonomous batch execution.** Copilot Workspace, Cursor, and Claude Code handle one task at a time and wait for human prompts. Multiagent reads a prioritized backlog and runs tasks sequentially — each on its own feature branch, never touching main — with configurable autonomy levels.

**Self-hosted, open-source, no vendor lock-in.** Devin is closed-source and cloud-only ($20/mo + metered ACUs). Copilot requires GitHub. Cursor is a proprietary editor. Multiagent installs into any project, runs locally, uses your own API keys, and gives you a web dashboard — no SaaS dependency.

## Quick Start

```bash
# 1. Install
cd multiagent && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Initialize for your project
cd /your-project && multiagent/.venv/bin/python -m multiagent init

# 3. Create a task
python -m multiagent spec "Add user authentication with JWT"

# 4. Launch the dashboard
python -m multiagent.server
# Open http://localhost:8000
```

See [Getting Started](docs/getting-started.md) for the full walkthrough.

<details>
<summary><strong>Prerequisites</strong></summary>

### Claude Code

This system is built on top of [Claude Code](https://docs.anthropic.com/en/docs/claude-code/overview) — Anthropic's CLI tool for agentic coding. Install and authenticate it before using the orchestrator:

1. **Install Claude Code:** follow the [official installation guide](https://docs.anthropic.com/en/docs/claude-code/getting-started)
2. **Log in:** run `claude` and complete authentication

### Token Usage

The multi-agent system can consume a significant amount of tokens — each task involves multiple AI agents, and complex tasks may require hundreds of thousands of tokens.

- **API key users:** monitor your usage carefully. Set token budgets in `multiagent.toml` (`[budgets]` section) to control costs.
- **Claude Pro/Max subscribers:** the system works within your subscription limits. Rate limits are handled automatically.

</details>

## How It Works

```
Backlog → Pick task → Create branch → Enrich spec → Build plan → Implement → QG → Review → Commit
                                        │                          │           │
                                        ▼                          ▼           ▼
                                  Product Agent              Implementor   Reviewer
                                  Analyst Agent              (per step)    Visual Tester
```

| Agent | Model | Role |
|-------|-------|------|
| **Orchestrator** | Opus | Plans, delegates, verifies. Never writes code. |
| **Product** | Sonnet | Defines UX flows, edge cases, scope |
| **Analyst** | Sonnet | Reads codebase, writes technical plan |
| **Implementor** | Sonnet | Writes code per plan, follows existing patterns |
| **Reviewer** | Sonnet | Reviews diff for bugs, security, patterns |
| **Visual Tester** | Sonnet | Captures screenshots, checks for regressions |

## Web Dashboard

Start with `python -m multiagent.server` and open `http://localhost:8000`.

![Task List](docs/screenshots/task-list.png)

Browse your backlog, edit specs with AI, run agents, watch real-time logs, group related tasks, review results.

See [Dashboard Guide](docs/dashboard.md) for details.

## Documentation

| Document | Description |
|----------|-------------|
| [Getting Started](docs/getting-started.md) | Installation, initialization, first task |
| [Writing Specs](docs/writing-specs.md) | Creating task specs — Claude Code, CLI, manual |
| [Web Dashboard](docs/dashboard.md) | Task list, spec editor, groups, archive, scheduling |
| [Configuration](docs/configuration.md) | Complete `multiagent.toml` reference |
| [Architecture](docs/architecture.md) | Module map, data flow, design decisions |
| [Pipeline Deep Dive](docs/pipeline-deep-dive.md) | Step-by-step execution, error recovery |
| [Agents & Prompts](docs/agents-and-prompts.md) | Agent definitions, prompt templates |
| [Safety & Guardrails](docs/safety-and-guardrails.md) | Protected paths, quality gates, cost control |
| [Extending](docs/extending.md) | Adding agents, gates, sources, task types |

---

## Donate

If this project saves you time, consider supporting its development.

| Currency | Network | Address |
|----------|---------|---------|
| **USDT** / **USDC** / **ETH** | Ethereum (ERC-20) | `0x8e152C80a5790927BbeE947FF080075f01bDD907` |

> Send only ERC-20 tokens on **Ethereum mainnet**. Other networks are not supported.
