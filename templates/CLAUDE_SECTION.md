<!-- multiagent:start -->
## Multi-Agent System

This project uses an autonomous [multi-agent orchestrator](multiagent/) to execute tasks from the backlog. Agents read specs, write code, run quality gates, and commit to feature branches for human review.

### Commands

```bash
{run_prefix}.server            # Start web dashboard (http://localhost:8000)
{run_prefix} spec "desc"       # Create task spec from description
{run_prefix} spec -f file.md  # Create task spec from a file
{run_prefix} spec -f file.md -y  # Auto-create multiple specs from file
{run_prefix} --list            # List all tasks with status
{run_prefix} --next            # Run next priority task
{run_prefix} --task ID         # Run specific task by ID
{run_prefix} --resume          # Resume interrupted task
{run_prefix} --batch           # Run all tasks sequentially
```

### How it works

1. Add tasks to `{data_dir}/backlog.md` (Markdown table with ID, name, type, priority)
2. Write specs in `{data_dir}/specs/` (or let agents generate from stubs)
3. Launch the **web dashboard** or use CLI — agents create a branch, enrich the spec, implement, review, commit

### Web dashboard

The dashboard (`{run_prefix}.server`) is the primary interface for managing tasks. It provides: task list with sorting/filtering, inline spec viewer with AI editor, spec groups for running related tasks on a shared branch, real-time execution logs, archive with full run details and artifacts, scheduling for deferred execution.

Full dashboard docs: `multiagent/docs/dashboard.md`

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

### Creating specs

**CLI:** `{run_prefix} spec "description"` or `{run_prefix} spec -f draft.md` — AI generates spec file + backlog entry from a description or text file. For files with multiple tasks, add `-y` to auto-create separate specs.

**Manual creation:**
1. Pick a type prefix: FE (feature), TD (tech-debt), RF (refactor), BF (bugfix), AU (audit)
2. Find the next ID: check `{data_dir}/backlog.md` for the highest number with that prefix, increment by 1
3. Create the file: `{data_dir}/specs/{{type_dir}}/{{TASK_ID}}-{{slug}}.md` (type → dir: feature→`features/`, tech-debt→`tech_debt/`, refactor→`refactor/`, bugfix→`bugfix/`, audit→`audit/`)
4. Add metadata header (Task ID, Type, Spec Status) + Overview + Acceptance Criteria
5. Append a row to the target phase table in `{data_dir}/backlog.md`

Full guide: `multiagent/docs/writing-specs.md`

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

Agents work on `auto/{{task_id}}` branches from `{dev_branch}`. They never push to remote. Human reviews and merges `{dev_branch}` → `{main_branch}`.

### Protected paths

Agents cannot modify: `multiagent/`, `CLAUDE.md`, `.claude/`, `.env*`, `.gitignore`, foundational specs. Exceptions: `multiagent/output/`.
<!-- multiagent:end -->
