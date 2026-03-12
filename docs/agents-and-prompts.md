# Agents & Prompt System

How the 6 agents are defined, how prompts are templated and rendered, and how to
customize or add new agents.

## Agent Types

The system has 1 Orchestrator and 5 subagents, defined in `core/agents.py`:

| Agent | Model | Tools | Role |
|-------|-------|-------|------|
| **Orchestrator** | Opus | Task, Bash, Read, Glob, Grep, Edit, Write | Plans, delegates, verifies. Never writes code directly. |
| **Product** | Sonnet | Read, Glob, Grep, Edit, Write | Defines UX flows, edge cases, scope for feature specs |
| **Analyst** | Sonnet | Read, Glob, Grep, Edit | Reads codebase, writes technical approach and implementation plan |
| **Implementor** | Sonnet | Read, Glob, Grep, Edit, Write, Bash | Writes code per plan, runs quality gates |
| **Reviewer** | Sonnet | Read, Glob, Grep, Bash | Reviews git diff for bugs, security issues, pattern violations |
| **Visual Tester** | Sonnet | Read, Bash, Glob | Captures screenshots, checks for visual regressions |

### Orchestrator

The Orchestrator is NOT defined as a subagent — it's the top-level agent that
runs the Claude Agent SDK `query()` call. Its prompt is built by
`prompt_builder.py:build_orchestrator_prompt()` and includes:

- Task details (ID, title, description, priority, complexity)
- Task spec content (with enrichment instructions based on status)
- Project context (product_context.md, agent_insights.md)
- Foundational specs (_project-conventions.md, etc.)
- Quality gate commands
- Protected/writable paths
- Git branch information
- Agent delegation instructions

### Subagents

Subagents are defined as `AgentDefinition` objects from the Claude Agent SDK:

```python
AgentDefinition(
    description="...",    # What the agent does (for Orchestrator's delegation)
    prompt="...",         # System prompt (rendered from template)
    tools=["..."],        # Allowed tools
    model="sonnet",       # Model to use
)
```

The Orchestrator delegates to subagents using the `Task` tool. When it calls
`Task(subagent_type="implementor", prompt="Write the login form...")`, the SDK
routes the request to the Implementor agent with its system prompt and tools.

## Agent Definition

All subagents are created by `create_agents()` in `core/agents.py`:

```python
def create_agents() -> dict[str, AgentDefinition]:
    return {
        "product": AgentDefinition(
            description="Product Designer — defines UX...",
            prompt=_load_and_render_prompt("product_system.md"),
            tools=["Read", "Glob", "Grep", "Edit", "Write"],
            model=config.SUBAGENT_MODEL,
        ),
        "analyst": AgentDefinition(...),
        "implementor": AgentDefinition(...),
        "reviewer": AgentDefinition(...),
        "visual-tester": AgentDefinition(...),
    }
```

The dictionary keys (`"product"`, `"analyst"`, etc.) are the `subagent_type`
values that the Orchestrator uses when delegating.

## Prompt Templates

System prompts are Markdown files in the `prompts/` directory:

```
prompts/
├── orchestrator_system.md     # NOT used as template (built programmatically)
├── product_system.md
├── analyst_system.md
├── implementor_system.md
├── reviewer_system.md
└── visual_tester_system.md
```

### Template Variables

Templates use Python `str.format_map()` syntax with `{variable_name}` placeholders.
Unknown variables are silently replaced with empty strings (via `defaultdict`).

Available variables (from `_get_prompt_vars()`):

| Variable | Source | Example |
|----------|--------|---------|
| `{project_name}` | `config.PROJECT_NAME` | `"my-app"` |
| `{project_description}` | `config.PROJECT_DESCRIPTION` | `"Next.js SaaS platform"` |
| `{app_dir}` | `config.APP_DIR` (absolute) | `"/home/user/my-app/frontend"` |
| `{app_dir_rel}` | `config.APP_DIR` (relative) | `"frontend"` |
| `{data_dir}` | `config.DATA_DIR` (absolute) | `"/home/user/my-app/multiagent_specs"` |
| `{data_dir_rel}` | `config.DATA_DIR` (relative) | `"multiagent_specs"` |
| `{specs_dir}` | `config.SPECS_DIR` (absolute) | `"/home/user/my-app/multiagent_specs/specs"` |
| `{specs_dir_rel}` | `config.SPECS_DIR` (relative) | `"multiagent_specs/specs"` |
| `{quality_gate_fast}` | `config.QUALITY_GATES["fast"]` | `"cd frontend && npx tsc --noEmit"` |
| `{quality_gate_full}` | `config.QUALITY_GATES["full"]` | `"cd frontend && npm run build"` |
| `{protected_paths_formatted}` | Formatted list | `"- multiagent/\n- CLAUDE.md"` |
| `{writable_paths_formatted}` | Comma-separated | `"frontend/, docs/"` |
| `{known_gotchas}` | From agent_insights.md | First 20 critical insights |
| `{dev_branch}` | `config.DEV_BRANCH` | `"auto-dev"` |
| `{project_root}` | `config.PROJECT_ROOT` | `"/home/user/my-app"` |

### Prompt Rendering

```python
def _load_and_render_prompt(filename: str) -> str:
    template = (config.PROMPTS_DIR / filename).read_text(encoding="utf-8")
    vars = _get_prompt_vars()
    return template.format_map(defaultdict(str, **vars))
```

The `defaultdict(str)` ensures that any unrecognized `{placeholder}` in the
template is replaced with an empty string instead of raising a `KeyError`.

## Orchestrator Prompt

The Orchestrator prompt is built programmatically by
`prompt_builder.py:build_orchestrator_prompt()`. It's NOT a template file —
it's constructed in Python to include dynamic content:

1. **Task details** — ID, source, title, description, priority, complexity
2. **Task spec** — found by `find_task_spec()`, with enrichment instructions
3. **Resume plan** — if resuming an interrupted task
4. **Project context** — loaded from `context_files`
5. **Foundational specs** — loaded from `foundational_specs`
6. **Git setup** — current branch, instructions not to push/merge
7. **Instructions** — step-by-step delegation workflow
8. **Quality gate commands** — fast and full gate commands
9. **Protected/writable paths** — guardrails information
10. **Output markers** — `[TASK_SUMMARY]`, `[USER_NOTICE]`, `[NEW INSIGHT]` format

### Audit Prompt

`build_audit_prompt()` creates a different prompt for audit tasks:
- Read-only instructions (no Implementor, no code changes)
- Audit criteria from the spec
- Previous audit report for comparison
- `[NEW TASK]` output format for generating new backlog entries

## Customizing Prompts

### Modifying an Existing Agent's Prompt

1. Edit the prompt file in `prompts/` (e.g., `prompts/implementor_system.md`)
2. Use any available `{variable}` from the template variables table
3. No code changes needed — prompts are loaded at runtime

### Adding a New Template Variable

1. Add the variable to `_get_prompt_vars()` in `core/agents.py`:

```python
def _get_prompt_vars() -> dict[str, str]:
    return {
        # ... existing vars ...
        "my_custom_var": "value",
    }
```

2. Use `{my_custom_var}` in any prompt template.

### Gotchas When Customizing Prompts

- **Literal braces**: Use `{{` and `}}` for literal curly braces in templates
  (Python format_map syntax)
- **Missing variables**: Unknown `{var}` silently becomes empty string
- **Variable scope**: Template variables are project-level, not task-level.
  Task-specific data is only in the Orchestrator prompt (built programmatically).

## Adding a New Agent Type

1. **Create the prompt file** at `prompts/my_agent_system.md`:

```markdown
You are the {project_name} My Agent.

Your role is to [describe role].

PROJECT ROOT: {project_root}
APP DIRECTORY: {app_dir}

INSTRUCTIONS:
[specific instructions for this agent]
```

2. **Add to `create_agents()`** in `core/agents.py`:

```python
def create_agents() -> dict[str, AgentDefinition]:
    return {
        # ... existing agents ...
        "my-agent": AgentDefinition(
            description=(
                "My Agent — does specific thing. "
                "Use when you need [specific capability]."
            ),
            prompt=_load_and_render_prompt("my_agent_system.md"),
            tools=["Read", "Glob", "Grep"],  # choose appropriate tools
            model=config.SUBAGENT_MODEL,
        ),
    }
```

3. **Reference in orchestrator instructions** (if the Orchestrator should use it
   automatically). Edit the orchestrator prompt in `prompt_builder.py` to include
   delegation instructions for your new agent.

That's it — the Claude Agent SDK discovers agents from the dictionary returned
by `create_agents()`. The Orchestrator can delegate to any agent in the dict.

## Related Documentation

- [Architecture](architecture.md) — module map and dependencies
- [Pipeline Deep Dive](pipeline-deep-dive.md) — how agents are invoked in the pipeline
- [Extending](extending.md) — full extension guide
- [Safety & Guardrails](safety-and-guardrails.md) — tool restrictions per agent
