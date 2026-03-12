# Backlog & Spec Format

Reference for the `backlog.md` table format, spec file structure, and multi-source
backlog support. Used by `task_loader.py` to parse tasks and `prompt_builder.py`
to find spec files.

## Backlog (`backlog.md`)

The backlog is a Markdown file with tables organized by phase. It lives at
`{data_dir}/backlog.md` (default: `agents_data/backlog.md`).

### Table Format

Each phase section contains a Markdown table with these columns:

```markdown
## Phase 1: Core Features

| ID | Name | Type | Importance | Complexity | Deleg. | Spec | Human | Description |
|---|---|---|---|---|---|---|---|---|
| FE1 | Add user auth | feature | 5 | 3 | high | full | auto | JWT auth with refresh tokens |
| TD1 | Fix N+1 queries | tech-debt | 3 | 2 | high | partial | auto | Optimize DB queries in API |
```

#### Column Reference

| Column | Type | Values | Description |
|--------|------|--------|-------------|
| **ID** | string | `FE1`, `TD2`, `AU3`, `BF1`, `MVP_FE1` | Unique task identifier |
| **Name** | string | free text | Short task title (displayed in `--list`) |
| **Type** | string | see Type Map | Task type (maps to internal source type) |
| **Importance** | integer | `1`–`5` | Priority within the phase (5 = most important) |
| **Complexity** | integer | `1`–`5` | Implementation complexity (5 = most complex) |
| **Deleg.** | string | `high`, `medium`, `low` | How well the task can be delegated to AI |
| **Spec** | string | `full`, `partial`, `stub`, `missing` | Spec completeness status |
| **Human** | string | `auto`, `design`, `decision` | Required human input level |
| **Description** | string | free text | Task description (passed to Orchestrator) |

#### Type Map

The Type column maps to internal task types via `[backlog.type_map]` in the config.
Default mapping:

| Type in Backlog | Internal Source | Pipeline |
|-----------------|-----------------|----------|
| `feature` | `feature` | Standard (Product → Analyst → Implementor → Reviewer → VT) |
| `tech-debt` | `tech-debt` | Standard (Analyst → Implementor → Reviewer) |
| `refactor` | `refactor` | Standard (Analyst → Implementor → Reviewer) |
| `audit` | `audit` | Audit (Analyst read-only → Report → New tasks) |
| `bugfix` | `bugfix` | Standard (Analyst → Implementor → Reviewer) |

Custom types can be mapped in `multiagent.toml`:

```toml
[backlog]
type_map = { "фича" = "feature", "техдолг" = "tech-debt", "баг" = "bugfix" }
```

### Phase Sections

Tasks are grouped under phase headers:

```markdown
## Phase 1: Core
## Phase 2: Enhancement
## Phase BF: Bug Fixes
## Phase X: Experimental
```

The phase string is parsed from `## Phase {phase_id}:`. Phase determines base
priority — earlier phases get higher priority.

**Phase priority mapping:**

| Phase | Base Priority | Notes |
|-------|--------------|-------|
| `1` / `A` | 0.95 | Highest priority |
| `2` / `B` | 0.85 | |
| `3` / `C` | 0.70 / 0.75 | |
| `4` / `D` | 0.55 / 0.65 | |
| `5` / `E` | 0.40 / 0.55 | |
| `X` | 0.20 | Experimental |

Multi-character phases (e.g., `1a`, `BF`) use the first character for base
priority with a small offset from the second character (`a` = -0.001, `b` = -0.002).

### ID Format

Task IDs must match the pattern: letters/digits/underscores ending with a digit.

| Pattern | Examples | Use Case |
|---------|----------|----------|
| `XX#` | `FE1`, `TD2`, `AU3` | Default format |
| `PREFIX_XX#` | `MVP_FE1`, `MVP_BF17` | Prefixed (custom sources) |

### Priority Calculation

```
priority = min(1.0, phase_base + importance * 0.02)
```

Tasks are sorted by `(-priority, complexity)` — highest priority first, lowest
complexity first among equal priorities.

### Minimal Example

```markdown
# Backlog

## Phase 1: MVP

| ID | Name | Type | Importance | Complexity | Deleg. | Spec | Human | Description |
|---|---|---|---|---|---|---|---|---|
| FE1 | User login page | feature | 5 | 3 | high | full | auto | Email/password login with validation |
| FE2 | User dashboard | feature | 4 | 4 | high | partial | auto | Main dashboard with stats |

## Phase 2: Polish

| ID | Name | Type | Importance | Complexity | Deleg. | Spec | Human | Description |
|---|---|---|---|---|---|---|---|---|
| TD1 | Add error boundaries | tech-debt | 3 | 2 | high | stub | auto | Wrap pages in error boundaries |
| AU1 | Code quality audit | audit | 2 | 1 | high | full | auto | Review code for consistency |
```

## Spec Files

### Directory Mapping

Spec files live in subdirectories based on their task type:

```
agents_data/specs/
├── _project-conventions.md   ← foundational (loaded in every agent context)
├── features/
│   ├── FE1-user-login.md
│   └── FE2-user-dashboard.md
├── tech_debt/
│   └── TD1-error-boundaries.md
├── refactor/
│   └── RF1-extract-utils.md
├── audit/
│   └── AU1-code-quality.md
└── bugfix/
    └── BF1-fix-login-crash.md
```

### File Naming

```
{task_id}-{slug}.md           # v1 (default)
{task_id}-{slug}.v2.md        # v2 (Analyst-enriched)
{task_id}-{slug}.v3.md        # v3 (further revision)
```

The system always uses the **highest version**. For feature tasks, Product and
Analyst agents write into the **same file** (no version bump). For non-feature
tasks, the Analyst creates a v2 when enriching a stub.

### Spec Structure

Minimal spec template:

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
- [ ] Criterion 2
```

Full spec (after Product + Analyst enrichment):

```markdown
# Task Title

**Task ID:** FE1
**Type:** feature
**Spec Status:** full

---

## Overview

What this task does and why.

## User Experience

How the user interacts with this feature.
- User flow description
- UI states and transitions

## Edge Cases & Error States

- What happens when X fails
- Empty state behavior
- Concurrent access handling

## Scope

What's included and excluded.

## Technical Approach

How to implement this.
- Architecture decisions
- Libraries to use

## Files to Modify

- `src/pages/login.tsx` — add login form
- `src/lib/auth.ts` — new auth utility
- `prisma/schema.prisma` — add User model

## Implementation Steps

1. Create auth utility
2. Add login form component
3. Wire up API route
4. Add validation

## Acceptance Criteria

- [ ] Login form validates email format
- [ ] Error messages show on invalid input
- [ ] Successful login redirects to dashboard
```

### Spec Statuses

| Status | Meaning | What Happens |
|--------|---------|--------------|
| `full` | Ready for implementation | Orchestrator proceeds directly to Analyst for plan |
| `partial` | Has some sections, needs more | Product (features) or Analyst (others) enriches first |
| `stub` | Minimal description only | Product → Analyst enrichment pipeline before implementation |
| `missing` | No spec file found | Created automatically by Product/Analyst |

### Foundational Specs

Files prefixed with `_` in the specs directory are **foundational** — they're
loaded into every agent's context, not tied to any specific task.

```
agents_data/specs/
├── _project-conventions.md   ← loaded for every task
├── _architecture-blocks.md   ← loaded for every task
└── features/
    └── FE1-login.md          ← task-specific
```

Configure which foundational specs to load:

```toml
[data]
foundational_specs = [
    "agents_data/specs/_project-conventions.md",
    "agents_data/specs/_architecture-blocks.md",
]
```

## Multiple Backlog Sources

The system supports multiple backlog directories, each with its own `backlog.md`,
specs, and registry. This is useful for separating concerns (e.g., main backlog
vs. MVP requirements vs. client-specific tasks).

### Adding a Source

From the server dashboard or programmatically:

```python
from multiagent.core.sources import add_source

source = add_source("/path/to/mvp-backlog", task_prefix="MVP")
# Creates source with auto-generated slug ID
# The folder must contain a backlog.md
```

Via CLI when running a task from a custom source:

```bash
python -m multiagent --task MVP_FE1 --source-id mvp-backlog
```

### Source Structure

Each source is a directory containing at minimum a `backlog.md`:

```
/path/to/mvp-backlog/
├── backlog.md          ← required
├── registry.md         ← auto-created
├── features/           ← spec subdirectories
├── tech_debt/
└── bugfix/
```

### Source Definition

Sources are stored in `output/sources.json`:

```json
[
  {
    "id": "mvp-backlog",
    "name": "mvp-backlog",
    "path": "/absolute/path/to/mvp-backlog",
    "backlog_file": "/absolute/path/to/mvp-backlog/backlog.md",
    "is_default": false,
    "task_prefix": "MVP"
  }
]
```

The default source (`agents_data/`) is always present and cannot be removed.

### Task Loading

`load_all_tasks()` iterates all sources and merges their tasks into a single
priority-sorted list. Tasks from all sources compete equally for execution.

## Related Documentation

- [Getting Started](getting-started.md) — adding your first task
- [Configuration Reference](configuration.md) — `type_map` and `data` sections
- [Pipeline Deep Dive](pipeline-deep-dive.md) — how specs are enriched during execution
