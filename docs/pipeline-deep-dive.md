# Pipeline Deep Dive

Step-by-step breakdown of how tasks are executed, from backlog entry to git commit.
Covers both the standard pipeline (code changes) and the audit pipeline (read-only).

## Standard Pipeline

The standard pipeline is implemented in `core/pipeline.py:run_task()`. It handles
feature, tech-debt, refactor, and bugfix tasks.

### Step-by-Step Flow

```
┌──────────────────────────────────────────────────┐
│ 1. Human Checkpoint (task_selection)             │
│    ├── supervised: always pause                  │
│    ├── batch: auto-approve (unless in checkpoints)│
│    └── autonomous: auto-approve                  │
├──────────────────────────────────────────────────┤
│ 2. Git Branch Creation                           │
│    ├── Ensure auto-dev exists                    │
│    ├── Checkout auto-dev, pull                   │
│    └── Create auto/{task_id} branch              │
├──────────────────────────────────────────────────┤
│ 3. Baseline Screenshots                          │
│    ├── Start dev server                          │
│    ├── Capture screenshots of configured pages   │
│    └── Save to output/screenshots/{id}/before/   │
├──────────────────────────────────────────────────┤
│ 4. Context Loading + Prompt Building             │
│    ├── Load context files (product_context, etc) │
│    ├── Find task spec (versioned lookup)         │
│    ├── Load foundational specs                   │
│    └── Build orchestrator prompt with everything │
├──────────────────────────────────────────────────┤
│ 5. Orchestrator Execution                        │
│    ├── Create agent definitions (5 subagents)    │
│    ├── query() → Claude Agent SDK streaming      │
│    ├── Orchestrator delegates to subagents:      │
│    │   ├── Product → UX, edge cases (features)   │
│    │   ├── Analyst → read code, write plan        │
│    │   ├── Implementor → write code (per step)    │
│    │   ├── Reviewer → review full diff            │
│    │   └── Visual Tester → check screenshots      │
│    └── Collect full output text                  │
├──────────────────────────────────────────────────┤
│ 6. Output Extraction                             │
│    ├── [TASK_SUMMARY]: one-line summary          │
│    ├── [NEW INSIGHT]: category — description     │
│    └── [USER_NOTICE]: what to check              │
├──────────────────────────────────────────────────┤
│ 7. Guardrails Enforcement                        │
│    ├── Check git diff for protected path changes │
│    └── Revert violations if found                │
├──────────────────────────────────────────────────┤
│ 8. Quality Gates (full)                          │
│    ├── Run fast gate (tsc/lint)                  │
│    └── Run full gate (build)                     │
├──────────────────────────────────────────────────┤
│ 9. Visual Regression Test                        │
│    ├── Capture "after" screenshots               │
│    ├── Compare with "before" screenshots         │
│    └── Save report to logs/{id}/visual.log       │
├──────────────────────────────────────────────────┤
│ 10. Registry & Archive Update                    │
│     ├── Write task report (logs/{id}/report.md)  │
│     ├── Update registry.md (Active → Completed)  │
│     └── Add entry to archive.json                │
├──────────────────────────────────────────────────┤
│ 11. Git Commit + Cleanup                         │
│     ├── Commit all changes on feature branch     │
│     ├── Mark task as done in state.json          │
│     └── Checkout auto-dev                        │
└──────────────────────────────────────────────────┘
```

### Branch Override (Group Mode)

When running as part of a spec group, `branch_override` is set:

- No new branch creation — uses the group's shared branch
- `skip_branch_cleanup` prevents checkout back to `auto-dev`
- Multiple tasks accumulate commits on the same branch

## Audit Pipeline

The audit pipeline (`core/audit.py:run_audit_task()`) is a simplified read-only
flow. No code changes, no Implementor, no Quality Gates, no Visual Tester.

```
┌──────────────────────────────────────────────────┐
│ 1. Human Checkpoint (task_selection)             │
├──────────────────────────────────────────────────┤
│ 2. Git Setup (checkout auto-dev, no new branch)  │
├──────────────────────────────────────────────────┤
│ 3. Load Spec + Previous Report                   │
├──────────────────────────────────────────────────┤
│ 4. Build Audit Prompt                            │
│    └── Includes criteria, previous report        │
├──────────────────────────────────────────────────┤
│ 5. Orchestrator → Analyst (read-only)            │
│    ├── Read codebase against audit criteria      │
│    ├── Record findings (severity, location)      │
│    └── Output [NEW TASK] markers for issues      │
├──────────────────────────────────────────────────┤
│ 6. Guardrails Check (should be clean)            │
├──────────────────────────────────────────────────┤
│ 7. Save Audit Report                             │
│    └── output/logs/audits/{id}_audit_{date}.md   │
├──────────────────────────────────────────────────┤
│ 8. Parse Findings → Generate New Tasks           │
│    ├── Extract [NEW TASK] markers from output    │
│    ├── Create stub specs in appropriate dirs     │
│    └── Append entries to backlog.md              │
├──────────────────────────────────────────────────┤
│ 9. Record Audit History (for cooldown)           │
│    └── state.audit_history[task_id].append(date) │
└──────────────────────────────────────────────────┘
```

### Audit Re-Runs

Audits are **re-runnable** — they're not added to `completed_tasks`. Instead,
a cooldown mechanism prevents running the same audit too frequently:

- Each audit run records today's date in `state.audit_history`
- `task_loader` checks if the last run was within `AUDIT_COOLDOWN_DAYS` (default: 14)
- If on cooldown, the task's status is set to `"done"` (not actionable)

### Task Generation from Findings

The audit output is parsed for `[NEW TASK]` markers:

```
[NEW TASK]: bugfix | Fix login validation | Email regex is too permissive | origin:AU1
[NEW TASK]: tech-debt | Extract color constants | Hardcoded hex values in 20+ files | origin:AU1
```

For each finding:
1. A unique task ID is generated (`BF1`, `TD2`, or `MVP_BF1` for custom sources)
2. A stub spec file is created in the appropriate subdirectory
3. A backlog entry is appended to the `## Audit-Generated Tasks` section

## Spec Enrichment

Before implementation, specs may need enrichment based on their status and type:

### Feature Tasks (stub/partial)

```
1. Product Agent → adds ## User Experience, ## Edge Cases, ## Scope
2. Analyst Agent → adds ## Technical Approach, ## Files to Modify
   Both write into the SAME file (no v2 creation)
```

### Non-Feature Tasks (stub/partial)

```
1. Analyst Agent → reviews and may create a v2 file
   {task_id}-{slug}.v2.md with expanded technical sections
```

### Full Specs

No enrichment needed — proceed directly to implementation planning.

## Error Recovery

### Rate Limit During Execution

```
Error occurs → is_rate_limit_error() check
    ├── Yes → resilient_stream() retries
    │         ├── Save progress so far
    │         ├── wait_for_rate_limit() with exponential backoff
    │         ├── Build continuation prompt with progress summary
    │         └── Retry (up to RATE_LIMIT_MAX_RETRIES = 50)
    │
    └── No → Exception propagates
             ├── State saved as "failed" or "rate_limited"
             ├── Registry updated with failure
             ├── Archive entry created
             ├── Partial work committed (safety-net)
             └── exit_status.json written (for server restart)
```

### Rate Limit Exhaustion (Server Level)

When all in-process retries are exhausted:

1. Pipeline writes `exit_status.json` with `status: "rate_limited"`
2. Process exits with non-zero code
3. Server's `ProcessManager` detects exit, reads `exit_status.json`
4. Schedules server-level restart after `SERVER_RATE_LIMIT_DELAY` (30 min)
5. Up to `SERVER_RATE_LIMIT_MAX_RETRIES` (14) server-level retries
6. Restart reuses the same branch (no new branch creation)

### Keyboard Interrupt

```
Ctrl+C → KeyboardInterrupt caught
    ├── State saved as "interrupted"
    ├── Registry updated
    ├── Partial work committed
    └── Can resume with: python -m multiagent --resume
```

### Quality Gate Failure

```
Quality gate returns non-zero
    ├── During Orchestrator: Orchestrator retries via Implementor (up to max_fix_retries)
    ├── Final gates (post-Orchestrator):
    │   ├── Task marked as "failed"
    │   ├── Registry/archive updated
    │   └── Partial work committed on feature branch
    └── Gate output saved to logs/{task_id}/gates.log
```

## Cost Tracking

Cost is tracked through the Claude Agent SDK's `ResultMessage`:

```python
elif isinstance(message, ResultMessage):
    if message.total_cost_usd:
        state.total_cost_usd += message.total_cost_usd
```

- `state.total_cost_usd` — cumulative cost across all tasks in the session
- Cost is reported in registry.md, archive.json, and task reports
- Displayed in `--list` output and server dashboard

## Output Markers

The Orchestrator is instructed to include these markers in its output.
The pipeline parses them with regex after execution completes.

### `[TASK_SUMMARY]`

One-line summary of what was done (max 60 chars). Used in registry and reports.

```
[TASK_SUMMARY]: Add JWT auth with refresh tokens and session management
```

### `[NEW INSIGHT]`

Knowledge that should be preserved for future agents. Parsed and added to
`agent_insights.md` automatically.

```
[NEW INSIGHT]: Tailwind — Custom CSS must be wrapped in @layer base to not override utilities
```

### `[USER_NOTICE]`

What changed for the user — which pages/features to check after the task.

```
[USER_NOTICE]: Check /login — new validation. Check /api/auth — new endpoint. Check /dashboard — user menu.
```

### `[NEW TASK]` (Audit Only)

Machine-parseable task proposals from audit findings.

```
[NEW TASK]: bugfix | Fix broken validation | Email regex accepts invalid formats | origin:AU1
```

## Related Documentation

- [Architecture](architecture.md) — module map and data flow
- [Agents & Prompts](agents-and-prompts.md) — what each agent does
- [Safety & Guardrails](safety-and-guardrails.md) — quality gates and protected paths
- [Extending](extending.md) — modifying the pipeline
