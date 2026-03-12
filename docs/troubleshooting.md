# Troubleshooting

Common issues and solutions when installing, configuring, and running
the Multi-Agent Orchestrator.

## Installation Issues

### "No module named claude_agent_sdk"

The virtual environment is not activated or dependencies are not installed.

```bash
cd multiagent
source .venv/bin/activate
pip install -r requirements.txt
```

If using the system without activating the venv, use the full path:

```bash
multiagent/.venv/bin/python -m multiagent --list
```

### Python Version Compatibility

Requires Python 3.11+ (for `tomllib` support). Check your version:

```bash
python3 --version
```

If you have multiple Python versions, create the venv explicitly:

```bash
python3.11 -m venv .venv
```

### ANTHROPIC_API_KEY Not Set

```
Error: ANTHROPIC_API_KEY environment variable not set
```

Set the API key in your shell:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

Or add to your shell profile (`~/.bashrc`, `~/.zshrc`):

```bash
echo 'export ANTHROPIC_API_KEY="sk-ant-..."' >> ~/.zshrc
```

## Init Issues

### "No Project Detected"

`multiagent init` looks for project markers (`package.json`, `Cargo.toml`,
`pyproject.toml`, `go.mod`, etc.) in the current directory.

**Fix:** Run `init` from your project root (not from inside `multiagent/`):

```bash
cd /path/to/my-project
multiagent/.venv/bin/python -m multiagent init
```

If your project doesn't have a standard marker file, `init` still works —
it falls back to generic detection and generates a basic config.

### AI Analysis Fails

If the Claude API call during `init --refresh` or `init` fails (network
error, rate limit), the system falls back to `fallback_analysis()` which
generates a basic config from filesystem detection alone.

The generated `multiagent.toml` will work but may need manual tuning:
- Verify `[project]` section (name, description, app_dir)
- Set `[quality_gates]` commands for your build system
- Add `[visual_testing]` pages if applicable

### Wrong App Directory Detected

The detector looks for framework-specific files and picks the most likely
app directory. If it's wrong, edit `multiagent.toml`:

```toml
[project]
app_dir = "frontend"  # Relative to project root
```

## Runtime Issues

### Rate Limiting

```
Rate limit hit (429). Waiting 30s before retry...
```

This is normal operation. The system uses exponential backoff:

| Attempt | Delay |
|---------|-------|
| 0 | 30s |
| 1 | 60s |
| 2 | 120s |
| 3 | 240s |
| 4+ | 300s (max) |

**If rate limits persist:**
- Check your API tier and usage limits
- Reduce `MAX_TOKENS_PER_TASK` in `[budgets]`
- Reduce `MAX_TURNS_PER_SUBAGENT` to limit subagent verbosity
- Use `sonnet` for subagents (default) instead of `opus`

**Claude Code CLI limits** (daily usage cap) are also handled. The system
parses "resets at 6am" messages and waits until the reset time.

### Task Parsing: 0 Tasks Loaded

```
Total: 0 | Done: 0 | Failed: 0
```

Common causes:

1. **Wrong backlog path** — verify `[data]` section in `multiagent.toml`:
   ```toml
   [data]
   dir = "multiagent_specs"
   ```

2. **Table format error** — the parser expects a specific Markdown table
   format. Check that:
   - Headers match: `| ID | Name | Type | Importance | Complexity | Deleg. | Spec | Human | Description |`
   - Separator row exists: `|---|---|---|---|---|---|---|---|---|`
   - Each row has the correct number of columns

3. **Unknown task type** — add a type mapping if using custom types:
   ```toml
   [backlog]
   type_map = { "фича" = "feature" }
   ```

4. **All tasks already completed** — check `registry.md` for completed
   entries. Tasks in the `## Completed` section are filtered out.

### Quality Gate Failures

```
[GATE:fast] FAIL (exit 1)
```

**Diagnose:**

1. Run the gate command manually:
   ```bash
   cd /path/to/app && npx tsc --noEmit
   ```

2. Common issues:
   - Missing `node_modules` — run `npm install` first
   - TypeScript config issues — check `tsconfig.json`
   - The command uses `{app_dir}` placeholder — verify `app_dir` in config

3. If gates fail consistently, check the gate timeout (120s default). Large
   projects may need more time. The timeout is hardcoded in
   `core/quality_gates.py:run_gate()`.

### "Protected Path Violation"

```
GUARDRAIL VIOLATION: 2 protected files modified
  multiagent/core/agents.py → REVERTED
  CLAUDE.md → REVERTED
```

The system detected and reverted changes to protected files. This is
working as intended — agents should not modify protected paths.

**If you want to allow a path:**

Add it to exceptions in `multiagent.toml`:

```toml
[protected_paths]
paths = ["multiagent/", "CLAUDE.md", ".env*"]
exceptions = ["multiagent/output/", "multiagent/docs/"]
```

### State Corruption

If `state.json` becomes corrupted (invalid JSON, partial write), you'll see
errors on startup.

**Reset state:**

```bash
# Back up the file first
cp multiagent/output/state.json multiagent/output/state.json.bak

# Delete the corrupted state
rm multiagent/output/state.json

# Remove the lock file too
rm -f multiagent/output/state.json.lock
```

The system creates a fresh state on next run. In-progress task state
will be lost, but completed task records are preserved in `registry.md`
and `archive.json`.

### Resume After Crash

If the process was interrupted (crash, `Ctrl+C`, power loss):

```bash
python -m multiagent --resume
```

This reloads `state.json` and continues from the last saved step. If the
state is too corrupted to resume, delete `state.json` and start fresh.

**What's preserved on crash:**
- Partial code changes on the feature branch (git working tree)
- State file with current task, step, and branch info
- Registry and archive entries

## Git Issues

### "auto-dev Branch Doesn't Exist"

The system creates `auto-dev` automatically on first run. If it was deleted:

```bash
git checkout main
git checkout -b auto-dev
```

### Branch Conflicts

If a feature branch already exists:

```
fatal: A branch named 'auto/FE5' already exists
```

The previous run may have been interrupted. Options:

1. **Delete the old branch and retry:**
   ```bash
   git branch -D auto/FE5
   python -m multiagent --task FE5
   ```

2. **Resume the interrupted task:**
   ```bash
   python -m multiagent --resume
   ```

### Uncommitted Changes on auto-dev

If you have uncommitted changes on `auto-dev` when starting a task:

```bash
# Stash your changes
git stash

# Run the task
python -m multiagent --next

# Restore your changes
git stash pop
```

The system runs `git checkout auto-dev` before creating feature branches,
which will fail if there are uncommitted changes.

## Visual Testing Issues

### Dev Server Doesn't Start

```
Dev server failed to start within timeout
```

**Check:**
1. The dev command works manually:
   ```bash
   cd /path/to/app && npm run dev -- -p 3000
   ```
2. The port is not already in use:
   ```bash
   lsof -i :3000
   ```
3. Config is correct:
   ```toml
   [visual_testing]
   dev_command = "npm run dev -- -p {port}"
   dev_port = 3000
   ```

### Playwright Not Installed

The visual testing system uses Playwright via a Node.js subprocess.
If screenshots fail:

```bash
cd multiagent && npx playwright install chromium
```

### Screenshots Are Empty or Black

- The dev server may not be fully ready — increase startup wait time
- Pages may require authentication — visual testing works best on public pages
- Check the `pages` list in `[visual_testing]` — make sure paths are correct

## Performance & Cost

### Typical Cost Per Task

Costs vary based on task complexity and spec status:

| Spec Status | Typical Cost |
|-------------|-------------|
| `full` (no enrichment needed) | $0.50–$2.00 |
| `partial` (needs some enrichment) | $1.00–$3.00 |
| `stub` (needs full enrichment) | $2.00–$5.00 |

Audit tasks are cheaper ($0.30–$1.00) since they're read-only.

### Reducing Token Spend

1. **Write detailed specs** — `full` specs skip enrichment, saving tokens
2. **Lower budgets:**
   ```toml
   [budgets]
   max_tokens_per_task = 300000    # Down from 500000
   max_turns_per_subagent = 20     # Down from 30
   ```
3. **Use Sonnet for subagents** (default) — Opus is only used for the
   Orchestrator by default
4. **Minimize quality gate failures** — each retry costs more tokens.
   Fix build issues before running agents.

### Server Mode Memory

The web dashboard holds WebSocket connections and file watchers. For
long-running server deployments:
- Monitor memory with `ps aux | grep multiagent`
- Restart the server periodically if needed
- Log files in `output/logs/` can grow large — archive old logs

## Related Documentation

- [Getting Started](getting-started.md) — initial setup walkthrough
- [Configuration Reference](configuration.md) — all config options
- [Safety & Guardrails](safety-and-guardrails.md) — understanding safety mechanisms
- [Pipeline Deep Dive](pipeline-deep-dive.md) — detailed execution flow
