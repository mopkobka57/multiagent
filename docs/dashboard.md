# Web Dashboard (Agent Monitor)

The Agent Monitor is a real-time web interface for managing and observing the multi-agent system. Instead of running tasks one-by-one from the CLI, the dashboard lets you browse your backlog, launch agents, edit specs, group tasks, schedule runs, and watch execution logs — all from the browser.

```bash
python -m multiagent.server
# Opens at http://localhost:8000
```

## Overview

![Overview](screenshots/overview.png)

The dashboard is a single-page application built with Alpine.js + Tailwind CSS, backed by a FastAPI server with WebSocket support. It connects to the same multiagent engine as the CLI — same config, same specs, same git strategy.

**Key areas of the interface:**

- **Top bar** — current git branch indicator, Launch Agent button, Add Source, Select for Group
- **Running Agents** — currently executing tasks with real-time status
- **Spec Groups** — named collections of tasks that run sequentially on a shared branch
- **Task list** — full backlog with sorting, filtering, spec status, and inline spec viewer
- **Archive** — history of completed/failed/stopped runs with full details

---

## Task List

![Task List](screenshots/task-list.png)

The task list shows every task from your backlog(s), enriched with runtime state.

### Columns

| Column | Description |
|--------|-------------|
| **ID** | Task identifier (e.g., FE23, TD6) |
| **Title** | Task name from backlog |
| **Type** | `feature`, `tech-debt`, `refactor`, `audit`, `bugfix` — color-coded badges |
| **PH** | Roadmap phase number |
| **IMP** | Importance score (0–100) |
| **CPLX** | Complexity score (1–5) |
| **SPEC** | Spec status: `full`, `partial`, `stub`, or empty (missing) |
| **HUMAN** | Human input requirement: `auto`, `decision`, `design` |

### Sorting and Filtering

- Click any column header to sort ascending/descending
- **Source tabs** at the top filter by backlog source (Main Backlog, custom sources)
- The default sort is by importance (descending), matching the priority the CLI uses

### Task Types

Tasks are color-coded by type, and each type follows a different agent pipeline:

| Type | Color | Pipeline | Use for |
|------|-------|----------|---------|
| **feature** | Green | Product → Analyst → Implementor → Reviewer | New functionality, UI components |
| **tech-debt** | Orange | Analyst → Implementor → Reviewer | Code cleanup, dependency updates |
| **refactor** | Blue | Analyst → Implementor → Reviewer | Restructuring without behavior change |
| **bugfix** | Red | Analyst → Implementor → Reviewer | Fixing broken behavior |
| **audit** | Purple | Analyst (read-only) | Code quality analysis, no code changes |

**Feature** tasks get the full pipeline: the Product Agent defines UX flows and edge cases, then the Analyst adds technical approach. All other types skip the Product Agent since they don't need UX design.

**Audit** tasks are special — they only analyze code and produce a report. No code is written, no branch is created.

### Launching a Task

Click any task row to open the spec panel. From there you can:

1. **Launch** — start the agent immediately (or enqueue if another is running)
2. **Schedule** — set a timer (delay or fixed time) for deferred execution
3. **Delete** — remove the spec and backlog entry (with confirmation)

If an agent is already running, the new task goes into a **queue** and starts automatically when the current one finishes.

---

## Spec Viewer & Editor

![Spec Editor](screenshots/spec-editor.png)

Clicking a task row opens the spec panel below the task list. It shows:

- **Spec file path** — full path to the Markdown file on disk
- **Rendered spec content** — Markdown rendered with headings, lists, code blocks
- **Action buttons** — Launch, Schedule, Delete

### AI-Powered Editing

The spec editor has a natural language input field at the bottom. Type an instruction like:

- *"Add a section about error handling for network timeouts"*
- *"Change acceptance criteria to include mobile responsiveness"*
- *"Expand the technical approach with caching strategy"*

Press **Ctrl+Enter** (or click Edit) and the AI will modify the spec content. You can review the changes and save or discard.

### Spec Statuses

| Status | Badge | Meaning |
|--------|-------|---------|
| **full** | Green | All sections filled — ready for implementation |
| **partial** | Orange | Some sections present — agents will enrich before coding |
| **stub** | Yellow | Minimal description — agents will generate full spec |
| *(empty)* | Gray | No spec file — agents create one from the backlog description |

You don't need to write perfect specs. Write a stub with Overview + Acceptance Criteria, and the Product + Analyst agents will fill in the rest before implementation begins.

---

## Running Agents

When a task is executing, it appears in the **Running Agents** section at the top. Each running task shows:

- Task ID and title
- Task type badge
- Current status (e.g., `running`)
- Elapsed time (live counter)
- **Stop** button

### Expanding a Running Task

Click a running task to expand it and see:

- **Real-time log stream** — agent output as it happens (via WebSocket)
- Current pipeline stage
- Token usage and cost

Logs update in real-time through WebSocket connection. A fallback poll (every 3 seconds) ensures you don't miss anything if WebSocket disconnects.

### Task Queue

If you launch a task while another is running, it enters a queue. The queue is visible in the UI and tasks start automatically in order. You can:

- See all queued tasks
- Remove tasks from the queue
- Queue tasks from different sources

---

## Spec Groups

![Overview with Groups](screenshots/overview.png)

Spec groups let you bundle multiple tasks to run **sequentially on a single shared branch**. This is useful when tasks are related and their changes should be combined.

### Why Groups?

Without groups, each task runs on its own `auto/{task_id}` branch. When tasks are related (e.g., "add i18n support" involves 5 separate specs), you want them all on one branch so each subsequent task sees the changes from the previous ones.

### Creating a Group

1. Click **"Select for Group"** in the top bar — enters selection mode
2. Click task rows to select them (checkboxes appear)
3. Click **"Create Group (N selected)"** — a green button appears showing count
4. Name the group (e.g., "i18n support") and confirm

The group gets its own branch: `auto/{group-name}` (slugified).

### Group Execution

When you start a group:

1. System creates/checks out the group branch from `auto-dev`
2. Runs the **first task** on that branch
3. When it completes → automatically starts the **next task** (on the same branch)
4. Continues until all tasks complete or one fails

### Group Controls

Each group card shows:

| Element | Description |
|---------|-------------|
| **Name** | Group name (e.g., "i18n support") |
| **Task count** | "5 specs" |
| **Branch** | `auto/i18n-support` |
| **Progress** | "3/5 done" |
| **Status** | `idle`, `running`, `completed`, `stopped` |

**Actions on a group:**

| Action | When available | What it does |
|--------|----------------|-------------|
| **Start / Restart** | idle, completed, stopped | (Re)starts from the first task |
| **Stop** | running | Stops the current task, pauses the group |
| **Continue** | stopped | Skips the failed task, moves to the next one |
| **Retry** | stopped | Re-runs the current (failed) task |
| **Schedule** | idle, completed, stopped | Deferred start via timer |
| **Enqueue** | any (if another agent runs) | Adds group to the queue |

### Expanded Group View

Click a group to expand it and see the ordered task list:

```
1  ● MVP34  Settings Modal + Theme to Sidebar     ← running (green dot)
2  ○ MVP35  Profile Page Redesign                  ← pending
3  ○ MVP36  Subscription + Coins Display Fix        ← pending
4  ○ MVP37  Coins Statistics Page Overhaul          ← pending
5  ○ MVP38  Sidebar on All Pages + Navigation       ← pending
6  ○ MVP39  Auto-save + Session Tier Fix            ← pending
```

Each task shows its execution status within the group context.

---

## Archive

![Archive](screenshots/archive.png)

The archive shows the history of all executed tasks — completed, failed, and stopped. Each entry displays:

| Field | Description |
|-------|-------------|
| **Task ID + Title** | What was executed |
| **Branch** | Git branch with the changes |
| **Status** | `done` (green), `failed` (red), `stopped` (yellow) |
| **Cost** | Total API cost in USD |
| **Date** | When the task finished |

### Expanded Archive Entry

Click an archive entry to see full execution details:

- **Start/finish timestamps** and branch name
- **Checkout** button — switch git to that branch to review changes
- **Show files** — list of files changed on the branch vs `auto-dev`
- **Summary** — what was done, what was checked, implementation notes

### Artifacts

Each completed task produces artifacts accessible via tabs:

| Tab | Content |
|-----|---------|
| **Execution** | Full orchestrator execution log |
| **Gates** | Quality gate results (tsc, build) |
| **Live** | Raw agent output log |
| **Report** | Final review report from the Reviewer agent |

### Filtering

Use the filter buttons to show:
- **All** — everything
- **Done** — only successful completions
- **Failed** — only failures and crashes
- **Stopped** — only manually stopped

---

## Multiple Backlog Sources

The dashboard supports multiple backlog sources — separate folders with their own backlog.md, specs, and registry. This is useful for:

- Working across multiple feature sets
- Client-specific work
- Separating long-term roadmap from quick fixes

### Adding a Source

1. Click **"+ Add Source"** in the top bar
2. A native OS folder picker opens (macOS: Finder dialog, Linux: Zenity)
3. Select the folder containing backlog.md and specs/
4. The source appears as a new tab in the task list

### Source Tabs

Each source gets its own tab. Click a tab to filter the task list to that source's tasks. The active source affects which backlog and specs are used when launching tasks.

---

## Scheduling

Tasks and groups can be scheduled for deferred execution:

1. Click **Schedule** on a task or group
2. Choose mode:
   - **Delay** — run after N minutes (default: 30)
   - **Fixed time** — run at a specific time
3. Confirm

Scheduled items appear in the UI with a live countdown. When the timer fires:
- If no agent is running → starts immediately
- If an agent is running → enqueues automatically

Schedules survive server restarts (persisted to disk).

---

## Real-Time Updates

The dashboard maintains a WebSocket connection to the server for instant updates:

- **Log streaming** — agent output appears line-by-line as it's generated
- **Status changes** — task started/completed/failed/stopped
- **Queue updates** — tasks added/removed from queue
- **Group progress** — which task in a group is currently running
- **Rate limit events** — when agents hit API rate limits and are waiting

The connection indicator in the top-right shows `Connected` (green) or reconnecting status.

---

## Git Integration

The top bar shows the current git branch and provides branch management:

- **Branch indicator** — shows current branch (e.g., `auto/random-upgrades`)
- **Branch links** — clickable `main` and `auto-dev` links for quick checkout
- **Checkout** — switch between branches to review changes from different tasks

The archive entries also have **Checkout** buttons to jump to a specific task's branch.

---

## Technical Details

### Architecture

```
Browser (Alpine.js SPA)
    ↕ REST API + WebSocket
FastAPI Server (app.py)
    ├── ProcessManager — spawns/monitors agent subprocesses
    ├── LogWatcher — polls live.log files, streams via WebSocket
    ├── StateWatcher — monitors state.json changes
    ├── Scheduler — deferred execution timers
    └── WebSocketHub — manages client connections and subscriptions
```

### Server Files

| File | Purpose |
|------|---------|
| `server/app.py` | FastAPI application, all REST + WebSocket routes |
| `server/process_manager.py` | Agent subprocess lifecycle, queue management |
| `server/parsers.py` | Backlog + state enrichment for API responses |
| `server/spec_editor.py` | AI-powered spec editing via Claude API |
| `server/static/index.html` | SPA markup (Alpine.js + Tailwind) |
| `server/static/app.js` | Client-side state management and API calls |

### API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/tasks` | Enriched task list |
| GET | `/api/tasks/{id}/spec` | Spec content for a task |
| POST | `/api/tasks/{id}/spec/edit` | AI edit spec |
| POST | `/api/tasks/{id}/spec/save` | Save edited spec |
| DELETE | `/api/tasks/{id}/spec` | Delete spec + backlog entry |
| GET | `/api/runs/active` | Running agent processes |
| POST | `/api/runs/start` | Launch or enqueue a task |
| POST | `/api/runs/{id}/stop` | Stop a running task |
| GET | `/api/runs/queue` | Task queue |
| DELETE | `/api/runs/queue/{id}` | Remove from queue |
| GET | `/api/runs/archive` | Execution history |
| GET | `/api/runs/{id}/log` | Task log (last N lines) |
| GET | `/api/runs/{id}/artifact/{name}` | Execution artifacts |
| GET | `/api/groups` | All spec groups |
| POST | `/api/groups` | Create group |
| POST | `/api/groups/{id}/start` | Start/restart group |
| POST | `/api/groups/{id}/stop` | Stop group |
| POST | `/api/groups/{id}/continue` | Skip to next task |
| POST | `/api/groups/{id}/retry` | Retry failed task |
| GET | `/api/sources` | Backlog sources |
| POST | `/api/sources` | Add source |
| GET | `/api/git/status` | Current branch |
| POST | `/api/git/checkout` | Switch branch |
| POST | `/api/schedule` | Create schedule |
| GET | `/api/schedule` | List schedules |
| WS | `/ws` | Real-time updates |
