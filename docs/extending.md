# Extending the System

How to add new agents, quality gates, backlog sources, task types, and
customize the pipeline. All extension points are designed to require
minimal code changes.

## Adding a New Agent

Three steps: create the prompt, register the agent, (optionally) update
the Orchestrator's instructions.

### 1. Create the Prompt File

Create `prompts/my_agent_system.md`:

```markdown
You are the {project_name} Security Auditor.

Your role is to review code changes for security vulnerabilities,
following OWASP Top 10 guidelines.

PROJECT ROOT: {project_root}
APP DIRECTORY: {app_dir}

PROTECTED PATHS (never modify):
{protected_paths_formatted}

INSTRUCTIONS:
1. Read the git diff for the current task
2. Check for injection, XSS, auth bypass, and SSRF vulnerabilities
3. Report findings with severity and remediation steps
4. You are READ-ONLY — do not modify any files
```

Available template variables are listed in
[Agents & Prompts](agents-and-prompts.md#template-variables).

### 2. Register in `agents.py`

Add the agent to the `create_agents()` function in `core/agents.py`:

```python
def create_agents() -> dict[str, AgentDefinition]:
    return {
        # ... existing agents ...
        "security": AgentDefinition(
            description=(
                "Security Auditor — reviews code for vulnerabilities. "
                "Use after Implementor, before or alongside Reviewer."
            ),
            prompt=_load_and_render_prompt("my_agent_system.md"),
            tools=["Read", "Glob", "Grep", "Bash"],
            model=config.SUBAGENT_MODEL,
        ),
    }
```

**Key decisions:**
- `description` — tells the Orchestrator when to use this agent
- `tools` — choose the minimum set needed (see available tools below)
- `model` — typically `config.SUBAGENT_MODEL` (Sonnet)

### 3. Update Orchestrator Instructions (Optional)

If you want the Orchestrator to use the new agent automatically in its
workflow, edit `core/prompt_builder.py` and add delegation instructions
to the `build_orchestrator_prompt()` function.

Without this step, the Orchestrator can still delegate to the new agent
(it sees the description), but won't do so by default in its standard
workflow.

### Available Tools

| Tool | Capability |
|------|-----------|
| `Read` | Read file contents |
| `Glob` | Find files by pattern |
| `Grep` | Search file contents |
| `Edit` | Edit existing files |
| `Write` | Create new files |
| `Bash` | Execute shell commands |
| `Task` | Delegate to other subagents |

Choose the minimal set. Read-only agents should not get `Edit`, `Write`,
or `Bash`.

## Adding a Quality Gate

### 1. Add Command to Config

In `multiagent.toml`:

```toml
[quality_gates]
fast = "cd {app_dir} && npx tsc --noEmit"
full = "cd {app_dir} && npm run build"
lint = "cd {app_dir} && npx next lint"
test = "cd {app_dir} && npm test -- --watchAll=false"
```

The `{app_dir}` placeholder is replaced with the absolute path to your
app directory at config load time.

### 2. Call the Gate in the Pipeline

Edit `core/quality_gates.py` to add the gate to `run_full_gates()`:

```python
async def run_full_gates() -> tuple[bool, str]:
    """Run all quality gates. Returns (all_passed, combined_output)."""
    gates = ["fast", "full"]  # Add your gate name here
    # gates = ["fast", "full", "lint", "test"]

    results = []
    for gate_name in gates:
        passed, output = await run_gate(gate_name)
        results.append((gate_name, passed, output))
        if not passed:
            break  # Stop on first failure

    all_passed = all(r[1] for r in results)
    combined = "\n".join(f"[{r[0]}] {'PASS' if r[1] else 'FAIL'}\n{r[2]}" for r in results)
    return all_passed, combined
```

### Gate Execution Details

- Each gate runs as a subprocess with a 120-second timeout
- Both stdout and stderr are captured
- Exit code 0 = pass, non-zero = fail
- The `fast` gate runs after each Implementor step (inside the Orchestrator)
- The `full` gate runs at the end of the task (post-Orchestrator)

## Adding a Backlog Source

Multiple backlog sources let you organize tasks from different origins
(main backlog, client requests, MVP checklist, etc.).

### Programmatically

```python
from multiagent.core.sources import add_source

source = add_source("/path/to/mvp-requirements", task_prefix="MVP")
# Creates source with auto-generated slug ID
# The folder must contain a backlog.md
```

### Via Server Dashboard

The web dashboard provides a UI for adding/removing sources.

### Source Directory Structure

Each source folder needs at minimum a `backlog.md`:

```
/path/to/mvp-requirements/
├── backlog.md          # Required — same format as agents_data/backlog.md
├── registry.md         # Auto-created on first task run
├── features/           # Spec subdirectories (optional)
├── tech_debt/
└── bugfix/
```

### Running Tasks from a Source

```bash
python -m multiagent --task MVP_FE1 --source-id mvp-requirements
```

Tasks from all sources are merged into a single priority-sorted list when
using `--next` or `--batch`.

### Source Storage

Sources are persisted in `output/sources.json`. The default source
(`agents_data/`) is always present and cannot be removed.

## Custom Task Types

### Adding Type Mappings

Map custom type names to internal types in `multiagent.toml`:

```toml
[backlog]
type_map = { "фича" = "feature", "техдолг" = "tech-debt", "баг" = "bugfix" }
```

Now your backlog can use localized type names:

```markdown
| ID | Name | Type | Importance | Complexity | Deleg. | Spec | Human | Description |
|---|---|---|---|---|---|---|---|---|
| FE1 | Login page | фича | 5 | 3 | high | full | auto | Email/password login |
```

### Spec Directory Mapping

Custom types are mapped to spec subdirectories via `[data.spec_types]`:

```toml
[data.spec_types]
feature = "features"
tech-debt = "tech_debt"
refactor = "refactor"
audit = "audit"
bugfix = "bugfix"
```

Add new entries if you create new internal task types.

## Modifying the Pipeline

### Extension Points in `pipeline.py`

The `run_task()` function has clear phases where you can inject custom logic:

| Phase | Location | What to Customize |
|-------|----------|-------------------|
| Pre-execution | After human checkpoint | Add custom validation, pre-checks |
| Context loading | Before prompt building | Add extra context files |
| Post-orchestrator | After `resilient_stream()` | Add custom output parsing |
| Post-quality-gates | After `run_full_gates()` | Add notification, deployment |
| Pre-commit | Before `commit_work()` | Add custom file cleanup |

### Adding a Post-Task Hook

Example: send a Slack notification after each task:

```python
# In pipeline.py, after registry update

# --- Custom hook: notify ---
if success:
    notify_slack(f"Task {task.id} completed: {summary}")
```

### Modifying the Audit Pipeline

The audit pipeline in `core/audit.py` is simpler and follows the same
pattern. The key extension point is `parse_audit_findings()` — you can
modify how `[NEW TASK]` markers are parsed and how new tasks are generated.

## Spec Groups

Spec groups execute multiple tasks sequentially on a shared branch, useful
for related changes that should be reviewed together.

### Creating a Group

```python
from multiagent.core.groups import create_group, GroupTask

group = create_group(
    name="auth-overhaul",
    tasks=[
        GroupTask(task_id="FE1", title="Login page", source="feature", source_id="default"),
        GroupTask(task_id="FE2", title="Signup page", source="feature", source_id="default"),
        GroupTask(task_id="FE3", title="Password reset", source="feature", source_id="default"),
    ],
)
# Creates branch auto/auth-overhaul
# Tasks execute in order on the same branch
```

### Group Execution

When a group runs:
1. All tasks share a single branch (e.g., `auto/auth-overhaul`)
2. Tasks execute in order — each builds on the previous task's changes
3. No branch cleanup between tasks (`skip_branch_cleanup=True`)
4. Results are tracked per-task in `group.task_results`

### Group Statuses

| Status | Meaning |
|--------|---------|
| `idle` | Created but not started |
| `running` | Currently executing tasks |
| `paused` | Paused between tasks (human checkpoint or error) |
| `completed` | All tasks finished |
| `stopped` | Manually stopped |

## Writing Custom Prompts

### Template Testing

Test your prompt rendering without running a full task:

```python
from multiagent.core.agents import _load_and_render_prompt

rendered = _load_and_render_prompt("my_agent_system.md")
print(rendered)
```

### Common Gotchas

- **Literal braces**: Use `{{` and `}}` for literal `{` and `}` in templates
  (Python `format_map` syntax)
- **Missing variables**: Unknown `{var}` silently becomes empty string
- **Long prompts**: Keep prompts focused. The Orchestrator prompt is already
  large — subagent prompts should be concise and role-specific.

### Template Variables Reference

All variables from `_get_prompt_vars()` in `core/agents.py`:

| Variable | Description |
|----------|-------------|
| `{project_name}` | Project name from config |
| `{project_description}` | Project description |
| `{app_dir}` | Absolute path to app directory |
| `{app_dir_rel}` | Relative path to app directory |
| `{data_dir}` | Absolute path to data directory |
| `{data_dir_rel}` | Relative path to data directory |
| `{specs_dir}` | Absolute path to specs directory |
| `{specs_dir_rel}` | Relative path to specs directory |
| `{quality_gate_fast}` | Fast gate command |
| `{quality_gate_full}` | Full gate command |
| `{protected_paths_formatted}` | Protected paths as bullet list |
| `{writable_paths_formatted}` | Writable paths as comma-separated list |
| `{known_gotchas}` | Top 20 insights from agent_insights.md |
| `{dev_branch}` | Dev branch name |
| `{project_root}` | Absolute path to project root |

## Server Dashboard

### Starting the Server

```bash
python -m multiagent.server
```

Starts a FastAPI server (default port 8000) with:
- Web UI for task monitoring
- REST API for task/source/group management
- WebSocket for real-time log streaming

### Key API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/tasks` | List all tasks with enriched status |
| `POST` | `/api/tasks/{id}/run` | Start a task |
| `POST` | `/api/tasks/{id}/stop` | Stop a running task |
| `GET` | `/api/sources` | List backlog sources |
| `POST` | `/api/sources` | Add a new source |
| `GET` | `/api/groups` | List spec groups |
| `POST` | `/api/groups` | Create a new group |
| `POST` | `/api/groups/{id}/run` | Run a group |
| `GET` | `/api/archive` | Get execution history |
| `GET` | `/api/state` | Get current orchestrator state |

### WebSocket Protocol

Connect to `/ws` for real-time updates:

```javascript
const ws = new WebSocket("ws://localhost:8000/ws");

// Subscribe to a specific task's logs
ws.send(JSON.stringify({ type: "subscribe", taskId: "FE5" }));

// Subscribe to all task events
ws.send(JSON.stringify({ type: "subscribe_all" }));

// Incoming messages include taskId and log data
ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    console.log(msg.taskId, msg.type, msg.data);
};
```

### Process Management

The server uses `ProcessManager` (`server/process_manager.py`) to manage
agent subprocesses:

- **Single-agent enforcement**: Only one task runs at a time
- **Task queue**: Tasks are queued and executed sequentially
- **Auto-restart on rate limit**: If a task exits with `rate_limited` status,
  the server automatically restarts it after `SERVER_RATE_LIMIT_DELAY` (30 min)
- **Group handling**: Groups run their tasks in sequence on a shared branch

## Related Documentation

- [Agents & Prompts](agents-and-prompts.md) — agent definitions and prompt system
- [Configuration Reference](configuration.md) — all config options
- [Architecture](architecture.md) — module map and dependencies
- [Pipeline Deep Dive](pipeline-deep-dive.md) — pipeline internals
