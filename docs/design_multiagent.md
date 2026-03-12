# MIP Multi-Agent Autonomous Development System

## 1. Problem Statement

MIP has a well-documented backlog:
- **34 features** with specs in `agents_data/specs/features/`
- **8 tech debt + 6 refactor tasks** in `agents_data/specs/tech_debt/` and `agents_data/specs/refactor/`
- **5-phase task backlog** in `agents_data/backlog.md`
- Rich architectural specs, insights, and design patterns

Currently, a human (the founder) manually orchestrates Claude Code sessions. The goal: **automate the execution loop** while maintaining quality through multi-agent coordination and human checkpoints.

## 2. Architecture Overview

### 2.1 Agent Topology

```
                    ┌────────────────────┐
                    │   Human (Founder)  │
                    │   reviews auto-dev │
                    │   merges → main    │
                    └────────┬───────────┘
                             │ review PR / merge
                             ▼
                    ┌────────────────────┐
                    │    Orchestrator    │  ← Opus model
                    │  (Lead Agent)     │
                    │                    │
                    │  Reads: roadmap,   │
                    │  overview, configs │
                    │  Plans: decompose  │
                    │  Delegates: spawn  │
                    │  Verifies: gates   │
                    └────────┬───────────┘
                             │ spawns subagents
            ┌────────────────┼──────────────────┐
            ▼                ▼                  ▼
     ┌────────────┐   ┌──────────┐   ┌────────────────┐
     │  Analyst   │   │Implementor│   │   Reviewer     │  ← Sonnet
     │            │   │          │   │                │
     │ Reads code │   │ Writes   │   │ Reviews diff   │
     │ Writes     │   │ code per │   │ for bugs,      │
     │ design-doc │   │ plan     │   │ patterns,      │
     │            │   │          │   │ security       │
     └────────────┘   └──────────┘   └────────────────┘
                                              │
                           ┌──────────────────┤
                           ▼                  ▼
                    ┌────────────┐     ┌──────────────┐
                    │  Quality   │     │Visual Tester │
                    │  Gates     │     │              │
                    │            │     │ Screenshots  │
                    │ tsc, lint, │     │ before/after │
                    │ build      │     │ Console errs │
                    └────────────┘     └──────────────┘
```

### 2.2 Git Branching Strategy

```
main (stable, production — human controls)
  │
  └── auto-dev (staging branch for all automated work)
        │
        ├── auto/roadmap-2-1   ← feature branch (merged → auto-dev)
        ├── auto/roadmap-2-2   ← feature branch (merged → auto-dev)
        ├── auto/TD2             ← feature branch (merged → auto-dev)
        └── ...

Flow:
  1. Orchestrator creates feature branch FROM auto-dev
  2. All work happens on feature branch
  3. After approval → merge feature → auto-dev (automatic)
  4. Human reviews auto-dev → merges to main (manual)
```

This gives you a safety layer: all automated work accumulates in `auto-dev`,
and you merge to `main` only when satisfied.

### 2.3 Autonomy Modes

| Mode | Behavior | Use Case |
|------|----------|----------|
| `supervised` | Pauses at every configured checkpoint | First runs, learning the system |
| `batch` | Pauses only for PR review between tasks | Default — autonomous within task |
| `autonomous` | No pauses at all, logs everything | Overnight/unattended runs |

Within a task, agents run **fully autonomously** — no accept/deny prompts.
The Claude Agent SDK pre-authorizes all tools at launch time.
The only pauses are the human checkpoints configured in `config.py`.

## 3. Agent Definitions

### 3.1 Orchestrator (Lead Agent)

**Model:** Opus (best reasoning, orchestration)
**Role:** Does NOT write code. Reads backlog, selects task, decomposes, delegates, verifies.

**Context (system prompt includes):**
- `overview_analysis.md` — current project status
- `backlog.md` — task backlog with phases and priorities
- `agent_insights.md` — gotchas and patterns
- Task-specific design doc (if exists)

**Tools:** `Task` (for subagents), `Read`, `Glob`, `Grep`, `Bash` (git only)

**Decision loop:**
```
1. Read backlog → pick highest-priority unblocked task
2. Check if design-doc exists → if not, delegate to Analyst
3. Create implementation plan (decompose into steps)
4. For each step:
   a. Delegate to Implementor with precise instructions
   b. Run Quality Gates (tsc, lint)
   c. Delegate to Reviewer for diff review
   d. If issues found → delegate fix to Implementor
   e. If clean → proceed to next step
5. After all steps: run full verification
6. Update docs (overview, insights)
7. Git commit → request Human review
```

### 3.2 Analyst Agent

**Model:** Sonnet (cost-efficient, good at analysis)
**Role:** Reads existing code, understands patterns, writes design documents.

**Tools:** `Read`, `Glob`, `Grep` (read-only — cannot modify code)

**Prompt focus:**
- Study `configs.ts`, existing components, API routes
- Identify reusable patterns for the new feature
- Write `design_[feature].md` following the established template
- Flag dependencies and risks

**Output:** Design document in `docs/features/design_[feature].md`

### 3.3 Implementor Agent

**Model:** Sonnet (good at code generation, cost-efficient)
**Role:** Writes code according to a precise plan. Does NOT make architectural decisions.

**Tools:** `Read`, `Glob`, `Grep`, `Edit`, `Write`, `Bash`

**Prompt focus:**
- Follow the implementation plan step by step
- Read existing code BEFORE modifying (mandatory)
- Reuse existing patterns from configs.ts and components
- Match existing code style exactly
- Do NOT add features beyond the plan
- After each file change: confirm it compiles

**Constraints:**
- Cannot create new architectural patterns without Orchestrator approval
- Must read a file before editing it
- Limited to files specified in the plan

### 3.4 Reviewer Agent

**Model:** Sonnet
**Role:** Reviews git diff for bugs, pattern violations, security issues.

**Tools:** `Read`, `Glob`, `Grep`, `Bash` (git diff only)

**Prompt focus:**
- Compare changes against design-doc requirements
- Check for: type safety, error handling, XSS/injection, pattern consistency
- Check against `agent_insights.md` known gotchas
- Verify no regressions in existing functionality
- Output: PASS / FAIL with specific issues

**Output format:**
```
VERDICT: PASS | FAIL
ISSUES: [list of specific problems with file:line references]
SUGGESTIONS: [non-blocking improvements]
```

### 3.5 Visual Tester Agent (NEW)

**Model:** Sonnet
**Role:** Captures screenshots before/after changes, checks for visual regressions and JS errors.

**Tools:** `Read`, `Bash`, `Glob`

**Process:**
1. Before implementation: capture baseline screenshots of key pages
2. After implementation: capture new screenshots
3. Compare visually: layout shifts, broken elements, missing content
4. Check browser console for JavaScript errors
5. Report PASS/FAIL/WARNING

**Pages tested:** Dashboard (`/`), Work Station (`/workstation`)

**Output format:**
```
VISUAL VERDICT: PASS | FAIL | WARNING
PAGES TESTED: [list with OK/ISSUE per page]
VISUAL ISSUES: [specific descriptions]
CONSOLE ERRORS: [if any]
```

## 4. Quality Gates

Automated checks run between agent steps:

| Gate | Command | When | Blocking? |
|------|---------|------|-----------|
| TypeScript | `npx tsc --noEmit` | After each implementation step | Yes |
| Lint | `npx next lint` | After each implementation step | Yes (errors only) |
| Build | `npm run build` | After all steps complete | Yes |
| Visual | Playwright screenshots | After all steps complete | Warning only |
| Git clean | `git status` | Before commit | Yes |

### Visual Testing Pipeline

```
1. Task starts → dev server starts → baseline screenshots captured
2. Implementation happens (code changes)
3. After code gates pass → post-change screenshots captured
4. Visual Tester agent compares before/after
5. If FAIL → Implementor fixes → re-screenshot → re-compare
6. Dev server stops
```

Uses Playwright (headless Chromium) at 1280x720 viewport.
Screenshots saved to `multiagent/output/screenshots/[task_id]/before|after/`.

## 5. Task Pipeline

### 5.1 Task Sources

Tasks are loaded from `agents_data/backlog.md` and normalized into a common format:

```python
@dataclass
class Task:
    id: str                    # e.g., "FE1", "TD2", "AU1", "RF3"
    source: str                # "feature" | "tech-debt" | "audit" | "refactor"
    title: str                 # Human-readable title
    description: str           # Full description from source file
    priority: float            # Normalized 0-1 (from impact score or urgency)
    complexity: int            # 1-5 from source
    status: str                # "pending" | "in_progress" | "done" | "blocked"
    dependencies: list[str]    # Task IDs that must complete first
    design_doc: str | None     # Path to design doc if exists
    phase: str | None          # Roadmap phase if applicable
```

### 5.2 Priority Resolution

```
1. Roadmap tasks (current phase) — highest priority
2. Technical debt (urgency: High) — if blocking current phase
3. Features (by impact score descending) — when roadmap phase complete
4. Technical debt (urgency: Medium/Low) — gap-fill
```

### 5.3 Task Execution Flow

```
┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
│  QUEUE   │────▶│ ANALYSIS │────▶│ IMPLEMENT│────▶│  REVIEW  │
│          │     │          │     │          │     │          │
│ Pick top │     │ Analyst  │     │ Impl +   │     │ Reviewer │
│ priority │     │ writes   │     │ Quality  │     │ checks   │
│ task     │     │ design   │     │ Gates    │     │ diff     │
└──────────┘     └──────────┘     └──────────┘     └──────────┘
                                                        │
                                        ┌───────────────┤
                                        ▼               ▼
                                   ┌──────────┐   ┌──────────┐
                                   │   FIX    │   │  COMMIT  │
                                   │          │   │          │
                                   │ Impl     │   │ Update   │
                                   │ fixes    │   │ docs,    │
                                   │ issues   │   │ git      │
                                   │          │   │ commit   │
                                   └──────────┘   └──────────┘
                                                        │
                                                        ▼
                                                  ┌──────────┐
                                                  │  HUMAN   │
                                                  │ REVIEW   │
                                                  │          │
                                                  │ Approve? │
                                                  └──────────┘
```

### 5.4 Audit Pipeline

Audit tasks (AU1, AU2, AU3) follow a simplified pipeline:

```
Standard: Orchestrator → [Product] → Analyst → Implementor → QG → Reviewer → VT → merge → done
Audit:    Orchestrator → Analyst (read-only) → Report → [new tasks] → cooldown → re-run
```

Key differences from standard pipeline:
- **No git branch** — works on `auto-dev` directly
- **No Implementor** — no code changes
- **No Quality Gates** — nothing to verify
- **No Visual Tester** — nothing to screenshot
- **No Reviewer** — no code to review
- **Result** — audit report in `output/logs/audits/{task_id}_audit_{date}.md`
- **Repeatable** — cooldown period (14 days), then task becomes available again
- **Task generation** — findings can spawn new bugfix/tech-debt/refactor tasks

Audit tasks are NOT added to `completed_tasks` (they are re-runnable).
Cooldown is managed by `task_loader` via `audit_history` in state.

## 6. Human Checkpoints & Autonomy

The system supports 3 autonomy modes (configured in `config.py`):

### Mode: `supervised`
Pauses at every configured checkpoint. Use for first runs.

### Mode: `batch` (DEFAULT)
- Within a task: **fully autonomous** — no pauses, no accept/deny
- Between tasks: pauses for **PR review** only
- Agents use pre-authorized tools (SDK grants all permissions at launch)

### Mode: `autonomous`
- No pauses at all — runs overnight unattended
- Everything logged to `logs/[task_id]/`

### Configurable checkpoints:
| Checkpoint | Description | Default |
|-----------|-------------|---------|
| `task_selection` | Confirm which task to work on next | OFF |
| `design_doc` | Review design before implementation | OFF |
| `pr_review` | Review completed work before merge to auto-dev | ON |

### Human's only manual action:
**Merge `auto-dev` → `main`** when satisfied with accumulated changes.
This is always manual and never automated.

## 7. Safety Mechanisms

### 7.1 Blast Radius Control
- Each task runs on its own **feature branch** (created from `auto-dev`)
- Automated code NEVER touches `main` directly
- Feature branches merge into `auto-dev` only (staging layer)
- Human merges `auto-dev` → `main` manually
- Git operations limited to: add, commit, status, diff, branch, checkout, merge
- No force-push, no reset --hard, no rebase

### 7.2 Cost Control
- **Token budget per task** — configurable max (default: 500k tokens)
- **Subagent turn limit** — max 30 turns per subagent invocation
- **Retry limit** — max 3 fix cycles before escalating to human
- Orchestrator monitors cumulative cost

### 7.3 Error Escalation
```
Level 1: Quality gate fails     → Implementor retries (up to 3x)
Level 2: Reviewer rejects 3x   → Orchestrator re-plans the step
Level 3: Orchestrator stuck     → Pause, notify human, save state
Level 4: Build broken           → Revert to last clean commit, notify human
```

### 7.4 State Persistence
- Task state saved to `multiagent/output/state.json` after each step
- Can resume from any checkpoint after interruption
- Git branch serves as code checkpoint

## 8. File Structure

```
mip-app/                            # Next.js application
docs/                               # Project documentation and knowledge
agents_data/                        # Project-specific data for agents
  backlog.md                    # Task backlog with phases and priorities
  registry.md                       # Task execution log (status, cost, reports)
  agent_insights.md                 # Critical gotchas discovered by agents
  specs/                            # Task specifications
    _architecture-blocks.md         # Block system reference (foundational)
    _project-conventions.md         # Tech stack and patterns (foundational)
    audit/                          # Audit specs (AU1–AU3)
    features/                       # Feature specs (FE1–FE34)
    tech_debt/                      # Tech debt specs (TD1–TD8)
    refactor/                       # Refactor specs (RF1–RF6)
    bugfix/                         # Bugs from audit findings (auto-generated)

multiagent/                         # Multi-agent orchestration system (Python package)
  __init__.py                       # Package root
  __main__.py                       # CLI entry point: python -m multiagent
  config.py                         # Project-specific settings (paths, models, budgets)

  core/                             # Engine (reusable logic)
    orchestrator.py                 # Entry points: run_next, run_batch, list_tasks
    pipeline.py                     # Standard task pipeline (Impl → QG → Review → VT)
    audit.py                        # Audit pipeline (Analyst-only, read-only)
    prompt_builder.py               # Prompt construction, spec discovery, context loading
    git.py                          # Git operations (branch, merge, checkout)
    agents.py                       # Agent definitions (6 agents)
    state.py                        # State persistence for resume
    quality_gates.py                # Code + visual checks
    retry.py                        # Rate limit handling with exponential backoff
    registry.py                     # Updates registry.md and agent_insights.md
    task_loader.py                  # Parses tasks from agents_data/backlog.md

  prompts/                          # System prompts for agents
    orchestrator_system.md
    product_system.md
    analyst_system.md
    implementor_system.md
    reviewer_system.md
    visual_tester_system.md

  output/                           # Runtime artifacts (gitignored)
    state.json                      # Orchestrator state
    logs/                           # Per-task execution logs
      [task_id]/
        execution.log               # Full agent output trace
        gates.log                   # Quality gate results
        visual.log                  # Visual test results
      audits/                       # Audit reports
        {task_id}_audit_{date}.md   # Per-run audit reports
    screenshots/                    # Visual regression screenshots
      [task_id]/
        before/                     # Baseline screenshots (pre-changes)
        after/                      # Post-change screenshots

  docs/                             # Multiagent system documentation
    README.md                       # Overview and usage
    design_multiagent.md            # This document
```

## 9. Configuration

Key settings in `config.py`:

```python
# --- Git ---
MAIN_BRANCH = "main"           # Production (human-only)
DEV_BRANCH = "auto-dev"        # Staging (automated merges here)
BRANCH_PREFIX = "auto/"        # Feature branch naming

# --- Models ---
ORCHESTRATOR_MODEL = "opus"    # Best reasoning for planning
SUBAGENT_MODEL = "sonnet"      # Cost-efficient for execution

# --- Autonomy ---
AUTONOMY_MODE = "batch"        # "supervised" | "batch" | "autonomous"
HUMAN_CHECKPOINTS = ["pr_review"]  # [] for fully autonomous

# --- Visual testing ---
VISUAL_TEST_PAGES = ["/", "/workstation"]
DEV_SERVER_PORT = 3000

# --- Budgets ---
MAX_TOKENS_PER_TASK = 500_000  # ~$7-10 per task
MAX_FIX_RETRIES = 3            # Before escalating to human
```

## 10. Usage

### Setup:
```bash
pip install claude-agent-sdk
cd /path/to/project  # parent of multiagent/
```

### List all tasks from backlog:
```bash
python -m multiagent --list
```

### Run next priority task (default: batch mode — autonomous within task):
```bash
python -m multiagent --next
```

### Run specific task:
```bash
python -m multiagent --task FE1
```

### Run all tasks in a roadmap phase:
```bash
python -m multiagent --batch --phase 2
```

### Run fully autonomous (no pauses at all):
```bash
python -m multiagent --batch --mode autonomous
```

### Run with full human oversight:
```bash
python -m multiagent --next --mode supervised
```

### Resume after interruption:
```bash
python -m multiagent --resume
```

### After automated work — review and merge:
```bash
git log auto-dev --oneline          # See what was done
git diff main...auto-dev            # See all changes
git checkout main && git merge auto-dev  # Merge when satisfied
```

## 11. Estimated Cost per Task Type

| Task Type | Tokens (est.) | Cost (est.) | Time (est.) |
|-----------|---------------|-------------|-------------|
| Small audit (AU1, AU2) | 50-100k | $1-2 | 3-5 min |
| Medium feature (web_search) | 200-400k | $4-8 | 10-20 min |
| Large feature (PPTX export) | 400-800k | $8-15 | 20-40 min |
| Complex feature (Interviews) | 800k-1.5M | $15-30 | 40-90 min |

Full backlog (39 features + 16 tech tasks) rough estimate: **$300-600 total**

## 12. Phased Rollout

### Phase A: Proof of Concept (first)
- Implement orchestrator with 1 subagent (Implementor only)
- Run on 1 small technical task (e.g., AU1: visual consistency check)
- Validate: quality gates work, state persistence works, human checkpoint works

### Phase B: Full Pipeline
- Add Analyst and Reviewer agents
- Run on 1 medium roadmap task (e.g., R1: improve prompts)
- Validate: design doc quality, review catches real issues

### Phase C: Batch Execution
- Enable batch mode with automatic task selection
- Run on Phase 2 roadmap (3 tasks)
- Validate: task ordering correct, dependencies respected, docs updated

### Phase D: Full Autonomy
- Enable full backlog execution
- Human reviews PRs in batch
- Monitor cost and quality metrics
