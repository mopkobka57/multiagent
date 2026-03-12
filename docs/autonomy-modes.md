# Autonomy Modes

How much human oversight the system requires during execution. Three modes
control when the pipeline pauses for approval.

## Overview

| Mode | Default? | Behavior |
|------|----------|----------|
| `supervised` | No | Pauses at every checkpoint. Human approves each step. |
| `batch` | **Yes** | Runs tasks automatically. Pauses only at configured checkpoints. |
| `autonomous` | No | No pauses at all. Fully unattended execution. |

Configure in `multiagent.toml`:

```toml
[autonomy]
mode = "batch"
human_checkpoints = ["pr_review"]
```

## Supervised Mode

Every checkpoint pauses for human approval. Use when:

- Running the system for the first time
- Working on high-risk tasks (auth, payments, data migrations)
- You want to review the AI's plan before implementation begins

```
Task selected → PAUSE (approve task?)
  → Branch created → Orchestrator runs
  → Task complete → PAUSE (review PR?)
```

Both `task_selection` and `pr_review` checkpoints will pause, plus any
custom checkpoints listed in `human_checkpoints`.

## Batch Mode (Default)

Runs tasks automatically but pauses at configured checkpoints. The default
configuration pauses only at `pr_review`:

```toml
[autonomy]
mode = "batch"
human_checkpoints = ["pr_review"]
```

This means:
- `task_selection` — **auto-approved** (tasks run without asking)
- `pr_review` — **pauses** (you review the diff before it's finalized)

Use when:
- Running multiple tasks overnight
- You trust the task selection but want to review results
- Normal day-to-day operation

```
Task selected → auto-approved
  → Branch created → Orchestrator runs
  → Task complete → PAUSE (review PR?)
  → Approved → next task → auto-approved → ...
```

### Customizing Batch Checkpoints

Add `task_selection` to pause before each task too:

```toml
[autonomy]
mode = "batch"
human_checkpoints = ["task_selection", "pr_review"]
```

Or remove all checkpoints for auto-approval at every stage:

```toml
[autonomy]
mode = "batch"
human_checkpoints = []
```

## Autonomous Mode

No pauses at all. Every checkpoint is auto-approved, including `pr_review`.
The system runs until the backlog is empty or an error occurs.

```toml
[autonomy]
mode = "autonomous"
```

Use when:
- Running in CI/CD or server mode
- You have strong quality gates and trust the output
- Processing a large batch of low-risk tasks

**Risks:**
- No human review before changes are committed to feature branches
- Higher token spend if tasks fail and retry
- Requires robust quality gates (`fast` + `full`) to catch issues

## Human Checkpoints

Two built-in checkpoint types:

### `task_selection`

Fires before a task begins. Shows:

```
============================================================
HUMAN CHECKPOINT: task_selection
============================================================
Next task: [FE5] Add dark mode toggle
Source: feature | Priority: 0.99 | Complexity: 2/5
Description: Add a dark/light mode toggle to the settings page
============================================================
Approve? [y/n/details]:
```

- `y` / Enter — approve, start the task
- `n` — skip this task, move to the next one
- `d` — show full task details again

### `pr_review`

Fires after the task completes. Shows the git diff for review.

- `y` / Enter — approve the changes
- `n` — reject (changes remain on the feature branch for manual review)

### Non-Interactive Environments

If the system detects a non-interactive terminal (e.g., piped input, CI),
checkpoints are auto-approved with a log message:

```
[AUTO-APPROVE] non-interactive environment
```

## CLI Override

Override the mode for a single run with `--mode`:

```bash
# Run next task in supervised mode (regardless of config)
python -m multiagent --next --mode supervised

# Run batch autonomously
python -m multiagent --batch --mode autonomous

# Run specific task with full oversight
python -m multiagent --task FE5 --mode supervised
```

The override applies only to that invocation. It does not change `multiagent.toml`.

## Checkpoint Decision Matrix

| Mode | `task_selection` | `pr_review` | Custom Checkpoints |
|------|-----------------|-------------|-------------------|
| `supervised` | Pauses | Pauses | Pauses (if in `human_checkpoints`) |
| `batch` | Auto-approve | Auto-approve | Pauses (if in `human_checkpoints`) |
| `autonomous` | Auto-approve | Auto-approve | Auto-approve |

**Special case for `batch` mode:** The `pr_review` checkpoint is treated
specially — it pauses in batch mode even if not explicitly listed in
`human_checkpoints`, because it's the default safety net.

## Typical Scenarios

### First-time setup

```toml
[autonomy]
mode = "supervised"
human_checkpoints = ["task_selection", "pr_review"]
```

Review everything. Build confidence in the system's output quality.

### Daily development

```toml
[autonomy]
mode = "batch"
human_checkpoints = ["pr_review"]
```

Let the system pick and execute tasks. Review the diff at the end.

### Overnight batch run

```toml
[autonomy]
mode = "batch"
human_checkpoints = []
```

Or use `--mode autonomous` for the run. Tasks execute back-to-back.
Review all feature branches the next morning.

### CI/CD integration

```toml
[autonomy]
mode = "autonomous"
```

Fully unattended. Rely on quality gates and post-run review of branches.

## Related Documentation

- [Configuration Reference](configuration.md) — `[autonomy]` section details
- [Safety & Guardrails](safety-and-guardrails.md) — other safety layers beyond checkpoints
- [Pipeline Deep Dive](pipeline-deep-dive.md) — where checkpoints fire in the pipeline
