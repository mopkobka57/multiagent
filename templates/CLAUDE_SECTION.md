<!-- multiagent:start -->
## Multi-Agent System

This project uses an autonomous [multi-agent orchestrator](multiagent/) to execute tasks from the backlog. Agents read specs, write code, run quality gates, and commit to feature branches for human review.

### Commands

```bash
{run_prefix} --list            # List all tasks with status
{run_prefix} --next            # Run next priority task
{run_prefix} --task ID         # Run specific task by ID
{run_prefix} --resume          # Resume interrupted task
{run_prefix} --batch           # Run all tasks sequentially
```

### How it works

1. Add tasks to `{data_dir}/backlog.md` (Markdown table with ID, name, type, priority)
2. Write specs in `{data_dir}/specs/` (or let agents generate from stubs)
3. Run a task — agents create a branch, enrich the spec, implement, review, commit

### Specs

Specs are Markdown files that describe what to build. They live in `{data_dir}/specs/` organized by type (features/, tech_debt/, refactor/, bugfix/, audit/).

**Spec statuses:** `stub` (minimal, agents will enrich) → `partial` (some sections filled) → `full` (ready for implementation).

A minimal stub:
```markdown
# Task Title

**Task ID:** FE1
**Type:** feature
**Spec Status:** stub

---

## Overview
What this task does and why.

## Acceptance Criteria
- [ ] Criterion 1
```

Agents will add UX flows, edge cases, technical approach, and implementation steps before coding.

For full spec format and examples: `multiagent/docs/backlog-format.md`

### Key files

| File | Purpose |
|------|---------|
| `multiagent.toml` | All configuration — models, budgets, quality gates, paths |
| `{data_dir}/backlog.md` | Task backlog (source of truth for what to build) |
| `{data_dir}/product_context.md` | Product description for agent context |
| `{data_dir}/agent_insights.md` | Learned gotchas (agents read and update this) |
| `{data_dir}/specs/_project-conventions.md` | Coding conventions (loaded for every task) |

### Documentation

Full docs: `multiagent/docs/README.md` — getting started, configuration, architecture, pipeline, agents, safety, extending.

### Git strategy

Agents work on `auto/{task_id}` branches from `{dev_branch}`. They never push to remote. Human reviews and merges `{dev_branch}` → `{main_branch}`.

### Protected paths

Agents cannot modify: `multiagent/`, `CLAUDE.md`, `.claude/`, `.env*`, `.gitignore`, foundational specs. Exceptions: `multiagent/output/`.
<!-- multiagent:end -->
