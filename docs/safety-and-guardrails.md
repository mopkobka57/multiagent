# Safety & Guardrails

The Multi-Agent Orchestrator uses defense-in-depth to prevent agents from causing
damage. Four layers of protection work together to ensure safe autonomous execution.

## Defense in Depth

```
┌─────────────────────────────────────────────┐
│  Layer 4: Human Checkpoints (approval guard) │
│  ┌─────────────────────────────────────────┐ │
│  │ Layer 3: Quality Gates (code guard)     │ │
│  │ ┌─────────────────────────────────────┐ │ │
│  │ │ Layer 2: Protected Paths (hard guard)│ │ │
│  │ │ ┌─────────────────────────────────┐ │ │ │
│  │ │ │ Layer 1: System Prompts (soft)  │ │ │ │
│  │ │ │ (instructions to agents)        │ │ │ │
│  │ │ └─────────────────────────────────┘ │ │ │
│  │ └─────────────────────────────────────┘ │ │
│  └─────────────────────────────────────────┘ │
└─────────────────────────────────────────────┘
```

## Layer 1: System Prompts (Soft Guard)

Agent prompts include explicit instructions about what agents can and cannot do:

- **Orchestrator**: "You are a COORDINATOR. Do NOT read or write code directly."
- **Analyst**: Read-only tools (no Write, no Bash)
- **Reviewer**: Read-only tools (no Write, no Edit)
- **All agents**: Protected paths are listed with "you must NEVER modify these"
- **All agents**: Writable paths are listed as guidance

This is a *soft* guard — the instructions are in natural language and can
theoretically be ignored by the model. The hard guards below catch violations.

## Layer 2: Protected Paths (Hard Guard)

Implemented in `core/guardrails.py`. After every Orchestrator execution,
the system checks `git diff` for modifications to protected files.

### How It Works

```python
def enforce_guardrails() -> bool:
    clean, violations = check_protected_paths()
    if clean:
        return True
    revert_protected_files(violations)
    return False
```

1. **Detection**: `check_protected_paths()` runs three git commands:
   - `git diff --name-only HEAD` (unstaged changes)
   - `git diff --name-only --cached` (staged changes)
   - `git ls-files --others --exclude-standard` (new untracked files)

2. **Matching**: Each changed file is checked against `PROTECTED_PATHS` patterns,
   with `PROTECTED_EXCEPTIONS` checked first.

3. **Revert**: Violations are automatically reverted:
   - Tracked files: `git checkout HEAD -- <file>` (restore from HEAD)
   - Untracked files: `file.unlink()` (delete)

### Pattern Rules

| Pattern | Matches | Example |
|---------|---------|---------|
| `dir/` | Everything under `dir/` | `multiagent/` matches `multiagent/core/agents.py` |
| `file.md` | Exact file | `CLAUDE.md` matches only `CLAUDE.md` |
| `dir/_*` | Files starting with `_` in `dir/` | `multiagent_specs/specs/_*` matches `_project-conventions.md` |
| `.env*` | Files starting with `.env` | Matches `.env`, `.env.local`, `.env.production` |

### Exception Handling

Exceptions are checked **before** protection patterns. If a file matches
an exception, it's allowed even if it also matches a protected pattern.

Default exception: `multiagent/output/` — allows agents to write logs and state.

### Configuration

```toml
[protected_paths]
paths = [
    "multiagent/",
    "CLAUDE.md",
    ".claude/",
    ".env*",
    ".gitignore",
    "multiagent_specs/backlog.md",
    "multiagent_specs/specs/_*",
]
exceptions = ["multiagent/output/"]
```

## Layer 3: Quality Gates (Code Guard)

Implemented in `core/quality_gates.py`. Automated checks that validate the
agent's code changes won't break the build.

### Gate Types

| Gate | When | Timeout | Purpose |
|------|------|---------|---------|
| `fast` (tsc) | After each Implementor step | 120s | Quick type/lint check |
| `full` (build) | At end of task | 120s | Full production build |

### Execution Flow

```python
async def run_gate(name: str) -> tuple[bool, str]:
    command = QUALITY_GATES.get(name)
    result = subprocess.run(command, shell=True, timeout=120, cwd=APP_DIR)
    return result.returncode == 0, result.stdout + result.stderr
```

- Commands are configured in `[quality_gates]` section of `multiagent.toml`
- `{app_dir}` placeholder is replaced at config load time
- Both stdout and stderr are captured for error reporting

### Adding Custom Gates

Add new commands to `[quality_gates]`:

```toml
[quality_gates]
fast = "cd {app_dir} && npx tsc --noEmit"
full = "cd {app_dir} && npm run build"
lint = "cd {app_dir} && npx next lint"
test = "cd {app_dir} && npm test"
```

The pipeline currently runs `fast` and `full` gates. To add more, modify
`run_full_gates()` in `quality_gates.py`.

## Layer 4: Human Checkpoints (Approval Guard)

Implemented in `core/pipeline.py:request_human_approval()`. Pauses execution
for human review at configured points.

### Checkpoint Points

| Checkpoint | When | What's Reviewed |
|------------|------|-----------------|
| `task_selection` | Before starting a task | Task ID, title, priority, description |
| `pr_review` | After task completion | All changes on the feature branch |

### Behavior by Mode

| Mode | `task_selection` | `pr_review` | Other Checkpoints |
|------|-----------------|-------------|-------------------|
| `supervised` | Pauses | Pauses | Pauses if in `human_checkpoints` |
| `batch` | Auto-approve | Pauses | Auto-approve (unless in `human_checkpoints`) |
| `autonomous` | Auto-approve | Auto-approve | Auto-approve |

See [Autonomy Modes](autonomy-modes.md) for details.

## Cost Control

### Token Budgets

| Setting | Default | Purpose |
|---------|---------|---------|
| `MAX_TOKENS_PER_TASK` | 500,000 | Max tokens for entire Orchestrator session |
| `MAX_TURNS_PER_SUBAGENT` | 30 | Max turns per subagent call |
| `MAX_FIX_RETRIES` | 3 | Max retries when quality gate fails |
| `AUDIT_COOLDOWN_DAYS` | 14 | Min days between audit re-runs |

The Orchestrator's `max_turns` is derived: `MAX_TOKENS_PER_TASK / 10_000`.

### Cost Tracking

Total cost is tracked in `state.total_cost_usd` and reported in:
- CLI output after each task
- `registry.md` entries
- `archive.json` entries
- Task reports (`logs/{id}/report.md`)

## Rate Limiting

### In-Process Retry (core/retry.py)

Handles rate limits (HTTP 429/529) during agent execution:

```
Error → is_rate_limit_error() → calculate_delay() → wait → retry
```

**Backoff formula:**

```python
delay = RATE_LIMIT_BASE_DELAY * (RATE_LIMIT_BACKOFF_FACTOR ** attempt)
delay = min(delay, RATE_LIMIT_MAX_DELAY)  # Cap at 5 minutes
```

| Attempt | Delay |
|---------|-------|
| 0 | 30s |
| 1 | 60s |
| 2 | 120s |
| 3 | 240s |
| 4+ | 300s (max) |

Special handling for Claude Code CLI limits: parses "resets 6am (timezone)"
messages and waits until the reset time (no cap on delay for these).

### `resilient_stream()`

Wraps the entire query-to-completion flow with retry:

1. Start query, collect messages via streaming
2. If rate limit mid-stream: save progress, wait, retry with continuation prompt
3. Continuation prompt includes a summary of progress so far
4. Detects CLI limit messages in agent output (not just exceptions)

### Server-Level Restart (ProcessManager)

When in-process retries are exhausted:

1. Pipeline writes `exit_status.json` with `status: "rate_limited"`
2. Process exits
3. `ProcessManager._monitor_loop()` detects exit, reads status
4. Schedules restart after `SERVER_RATE_LIMIT_DELAY` (30 min)
5. Up to `SERVER_RATE_LIMIT_MAX_RETRIES` (14) restarts
6. Restart preserves the branch (no new branch creation)
7. State persists across restarts via `server_runs.json`

## Git Safety

### Branch Isolation

```
main                    ← human-controlled, never touched by agents
  └── auto-dev          ← staging branch for automated work
        ├── auto/FE5    ← feature branch per task
        ├── auto/TD2    ← isolated from other tasks
        └── ...
```

- Agents never push to any remote
- Agents never merge branches
- Agents never touch `main`
- Each task gets its own branch, created from `auto-dev`
- Human merges `auto-dev` → `main` when satisfied

### Safety-Net Commit

If a task fails or is interrupted, `commit_work()` commits partial work:

```python
def commit_work(task_id, branch, success):
    git_run("add -A")
    prefix = "feat" if success else "wip"
    msg = f"{prefix}({task_id}): {'completed' if success else 'partial work'}"
    git_run(f'commit -m "{msg}"')
```

This ensures no work is lost, even on crashes.

## State Persistence

### Thread Safety

All file writes use `filelock.FileLock` with 30-second timeouts:

```python
_STATE_LOCK = FileLock(str(STATE_FILE) + ".lock", timeout=30)

def save_state(state):
    with _STATE_LOCK:
        _save_state_unlocked(state)
```

Lock files: `state.json.lock`, `registry.md.lock`, `agent_insights.md.lock`,
`archive.json.lock`, `groups.json.lock`, `schedules.json.lock`.

### Resume After Crash

1. State is saved frequently during execution
2. `state.json` records `current_task` with status, branch, plan, current step
3. `--resume` flag reloads state and continues from where it left off
4. Server persists process info to `server_runs.json` for orphan recovery

### Atomic State Updates

For operations that need read-modify-write atomicity:

```python
def atomic_state_update(fn):
    with _STATE_LOCK:
        state = load_state()
        fn(state)
        _save_state_unlocked(state)
        return state
```

## Related Documentation

- [Architecture](architecture.md) — module map and design decisions
- [Configuration Reference](configuration.md) — all safety-related config options
- [Autonomy Modes](autonomy-modes.md) — human checkpoint behavior
- [Pipeline Deep Dive](pipeline-deep-dive.md) — where guardrails are enforced
- [Troubleshooting](troubleshooting.md) — diagnosing safety-related issues
