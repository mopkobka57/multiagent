# Writing Specs

How to create task specs for the multi-agent system — from quick descriptions
to detailed specifications.

## What is a Spec

A spec is a Markdown file that describes what to build. Agents read specs to
understand the task, plan the implementation, and write code. Better specs lead
to better results.

Specs live in `{data_dir}/specs/` organized by type:

```
specs/
├── _project-conventions.md   ← foundational (every agent reads this)
├── features/                 ← feature specs (FE1, FE2, ...)
├── tech_debt/                ← tech-debt specs (TD1, TD2, ...)
├── refactor/                 ← refactor specs (RF1, RF2, ...)
├── bugfix/                   ← bugfix specs (BF1, BF2, ...)
└── audit/                    ← audit specs (AU1, AU2, ...)
```

## Creating Specs

### Method 1: Claude Code (Recommended)

Open your project in Claude Code and describe what you want. Claude will:

1. Determine the task type (feature, bugfix, tech-debt, etc.)
2. Generate the next available task ID
3. Create a spec file in the right directory
4. Add a row to `backlog.md`

Example:

> "Create a spec for adding dark mode support to the settings page.
> It should toggle between light/dark themes and persist the preference."

Claude creates `specs/features/FE3-dark-mode-settings.md` and adds an entry
to the backlog. You can then review, edit, and refine.

### Method 2: CLI

```bash
python -m multiagent spec "Add dark mode toggle to settings page"
python -m multiagent spec --file feature-draft.md
python -m multiagent spec "Fix login crash on empty email" --phase 2
echo "Refactor auth middleware to use new session format" | python -m multiagent spec
```

The CLI uses AI to generate a structured spec from a description string, a text
file (`--file` / `-f`), or stdin. It assigns a task ID, creates the spec file,
and appends the backlog entry.

### Method 3: Manual

1. **Pick a type prefix:** FE (feature), TD (tech-debt), RF (refactor), BF (bugfix), AU (audit)
2. **Find the next ID:** check `backlog.md` for the highest number with that prefix, increment by 1
3. **Create the file:** `specs/{type_dir}/{ID}-{slug}.md`
4. **Add backlog row:** append to the target phase table in `backlog.md`

## Spec Structure

### Metadata Header (Required)

Every spec starts with a metadata header:

```markdown
# Task Title

**Task ID:** FE3
**Type:** feature
**Spec Status:** stub

---
```

### Sections

What you include depends on the spec status and task type.

**Minimal (stub) — any type:**

```markdown
## Overview

What this task does and why.

## Acceptance Criteria

- [ ] Criterion 1
- [ ] Criterion 2
```

Stubs are enough to get started — the Product and Analyst agents will enrich
them before implementation.

**Full feature spec:**

```markdown
## Overview
What and why.

## User Experience
User flows, UI states, transitions.

## Edge Cases & Error States
What happens when things go wrong.

## Scope
What's in and what's out.

## Technical Approach
Architecture, libraries, patterns.

## Files to Modify
- `src/path/to/file.ts` — what changes

## Implementation Steps
1. Step one
2. Step two

## Acceptance Criteria
- [ ] Criterion 1
- [ ] Criterion 2
```

**Tech-debt / refactor / bugfix spec:**

These are typically more focused. Skip User Experience and Scope — go straight
to the technical details:

```markdown
## Overview
What's wrong or what needs to change.

## Fix / Approach
How to fix it. Code patterns, files to touch.

## Acceptance Criteria
- [ ] Issue is resolved
- [ ] No regressions
```

**Audit spec:**

Audits are read-only — agents analyze code but don't change it:

```markdown
## Overview
What to audit and why.

## Scope
Which files/modules/areas to examine.

## Audit Criteria
- [ ] Check for X
- [ ] Verify Y consistency
```

## Spec Statuses

| Status | Meaning | What Happens |
|--------|---------|--------------|
| `full` | Ready for implementation | Orchestrator proceeds to planning and coding |
| `partial` | Has some sections | Product or Analyst enriches missing sections first |
| `stub` | Minimal description | Full enrichment pipeline before implementation |
| `missing` | No spec file exists | Agents create the spec from the backlog description |

You don't need to write full specs. **Stubs are perfectly fine** — that's what
the Product and Analyst agents are for. They'll add UX flows, edge cases,
technical approach, and implementation steps.

## How Agents Enrich Specs

When a task runs, the enrichment pipeline depends on the task type:

**Feature tasks:** Product agent adds UX flows, edge cases, scope → Analyst
agent adds technical approach, file list, implementation steps.

**Tech-debt / refactor / bugfix:** Analyst agent reads the codebase, adds
technical approach and implementation plan directly.

**Audit:** Analyst reads code, writes a report. No code changes.

Enrichment writes into the same file (features) or creates a `.v2.md` version
(other types). The system always uses the highest version.

## File Naming

```
{TASK_ID}-{slug}.md           # v1 (default)
{TASK_ID}-{slug}.v2.md        # v2 (agent-enriched)
{TASK_ID}-{slug}.v3.md        # v3 (further revision)
```

Examples:
- `FE3-dark-mode-settings.md`
- `TD1-fix-n-plus-one.md`
- `BF2-login-crash.v2.md`

## Type → ID Prefix → Directory

| Task Type | ID Prefix | Spec Directory |
|-----------|-----------|----------------|
| feature | FE | `specs/features/` |
| tech-debt | TD | `specs/tech_debt/` |
| refactor | RF | `specs/refactor/` |
| bugfix | BF | `specs/bugfix/` |
| audit | AU | `specs/audit/` |

## Tips for Good Specs

- **Focus on "why"** — agents can figure out "how" from the codebase, but they
  need to understand the motivation and constraints.
- **Include acceptance criteria** — concrete, verifiable conditions for "done".
  Agents use these to validate their work.
- **Reference existing patterns** — if the project has conventions (e.g.,
  "use the GenericBlockEditor pattern"), mention them. Agents will follow.
- **Be specific about scope** — especially for features. "Add auth" is vague.
  "Add email/password login with JWT, no OAuth" is actionable.
- **Don't over-specify implementation** — let the Analyst agent plan the
  technical approach based on the actual codebase. Over-specified stubs fight
  against the enrichment pipeline.
- **One task per spec** — if you have "add dark mode AND refactor theme system",
  split into two specs. Agents work best on focused tasks.

## Related Documentation

- [Backlog & Spec Format](backlog-format.md) — table format, ID conventions, multi-source
- [Agents & Prompts](agents-and-prompts.md) — how agents use specs
- [Pipeline Deep Dive](pipeline-deep-dive.md) — enrichment and implementation flow
