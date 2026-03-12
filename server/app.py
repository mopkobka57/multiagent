"""
Agent Monitor — FastAPI application.

Provides REST API, WebSocket hub, and file watchers
for real-time monitoring of multi-agent system.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import re
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from ..config import OUTPUT_DIR, TASK_LOGS_DIR, SPECS_DIR, DEV_BRANCH
from ..core.git import git_run, checkout_branch
from ..core.state import load_state, atomic_state_update
from ..core.archive import archive_fail
from ..core.registry import registry_fail_task
from ..core.sources import load_sources, add_source, remove_source, get_source_by_id
from ..core.groups import (
    load_groups, get_group, create_group, update_group, delete_group,
    save_groups, GroupTask, GroupTaskResult,
)
from .parsers import get_enriched_tasks, get_task_spec_content, get_archive_entries
from .process_manager import ProcessManager
from .spec_editor import edit_spec_with_ai
from ..core.spec_manager import delete_task_spec, remove_backlog_entry, SpecDeleteError
from ..core.scheduler import Scheduler

STATIC_DIR = Path(__file__).parent / "static"
STATE_FILE = OUTPUT_DIR / "state.json"


# ---------------------------------------------------------------------------
# WebSocket Hub
# ---------------------------------------------------------------------------

class WebSocketHub:
    """Manages WebSocket connections and per-task subscriptions."""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._subscriptions: dict[WebSocket, set[str]] = {}
        self._subscribe_all: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.add(ws)
        self._subscriptions[ws] = set()

    def disconnect(self, ws: WebSocket) -> None:
        self._connections.discard(ws)
        self._subscriptions.pop(ws, None)
        self._subscribe_all.discard(ws)

    def subscribe(self, ws: WebSocket, task_id: str) -> None:
        if ws in self._subscriptions:
            self._subscriptions[ws].add(task_id)

    def unsubscribe(self, ws: WebSocket, task_id: str) -> None:
        if ws in self._subscriptions:
            self._subscriptions[ws].discard(task_id)

    def subscribe_all(self, ws: WebSocket) -> None:
        self._subscribe_all.add(ws)

    async def broadcast(self, task_id: str, message: dict) -> None:
        """Send message to subscribers of this task + subscribe_all."""
        message["taskId"] = task_id
        data = json.dumps(message)
        targets = set()
        for ws, subs in self._subscriptions.items():
            if task_id in subs:
                targets.add(ws)
        targets |= self._subscribe_all
        dead = []
        for ws in targets:
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    async def broadcast_all(self, message: dict) -> None:
        """Send message to all connected clients."""
        data = json.dumps(message)
        dead = []
        for ws in self._connections:
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


# ---------------------------------------------------------------------------
# Log Watcher
# ---------------------------------------------------------------------------

class LogWatcher:
    """Polls live.log files every 0.5s for running agents."""

    def __init__(self, hub: WebSocketHub, pm: ProcessManager) -> None:
        self._hub = hub
        self._pm = pm
        self._file_positions: dict[str, int] = {}
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._watch_loop())

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()

    async def _watch_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(0.5)
                active = self._pm.get_active_runs()
                active_ids = {r["taskId"] for r in active}

                # Clean up positions for stopped tasks
                for tid in list(self._file_positions.keys()):
                    if tid not in active_ids:
                        del self._file_positions[tid]

                for run in active:
                    task_id = run["taskId"]
                    log_path = run.get("logPath", "")
                    if not log_path:
                        log_path = str(TASK_LOGS_DIR / task_id / "live.log")

                    if not os.path.exists(log_path):
                        continue

                    pos = self._file_positions.get(task_id, 0)
                    file_size = os.path.getsize(log_path)

                    if file_size > pos:
                        try:
                            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                                f.seek(pos)
                                new_content = f.read()
                                self._file_positions[task_id] = f.tell()
                        except OSError:
                            continue

                        for line in new_content.splitlines():
                            if line.strip():
                                await self._hub.broadcast(task_id, {
                                    "type": "log",
                                    "line": line,
                                })

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[LogWatcher] Error: {e}")


# ---------------------------------------------------------------------------
# State Watcher
# ---------------------------------------------------------------------------

class StateWatcher:
    """Polls state.json mtime every 2s and broadcasts changes."""

    def __init__(self, hub: WebSocketHub) -> None:
        self._hub = hub
        self._last_mtime: float = 0
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._watch_loop())

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()

    async def _watch_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(2)
                if not STATE_FILE.exists():
                    continue

                mtime = STATE_FILE.stat().st_mtime
                if mtime <= self._last_mtime:
                    continue
                self._last_mtime = mtime

                state = load_state()
                if state.current_task:
                    await self._hub.broadcast(state.current_task.task_id, {
                        "type": "status",
                        "status": state.current_task.status,
                        "costUsd": state.total_cost_usd,
                        "currentStep": state.current_task.status,
                    })

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[StateWatcher] Error: {e}")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

hub = WebSocketHub()
pm = ProcessManager()
scheduler = Scheduler()
log_watcher = LogWatcher(hub, pm)
state_watcher = StateWatcher(hub)


def _resolve_source_registry(run_info: dict | None) -> Path | None:
    """Get registry path for a task's source, if it has one."""
    sid = run_info.get("source_id", "default") if run_info else "default"
    if sid == "default":
        return None
    src = get_source_by_id(sid)
    return src.registry_file if src and src.registry_file.exists() else None


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Start monitors on startup, stop on shutdown."""
    def on_crash(task_id: str, run_info: dict | None = None):
        registry_fail_task(
            task_id=task_id,
            title=run_info.get("title", task_id) if run_info else task_id,
            branch=f"auto/{task_id}",
            started=run_info.get("startedAt", "") if run_info else "",
            cost_usd=0.0,
            reason="Agent process crashed (non-zero exit code)",
            status="failed",
            registry_path=_resolve_source_registry(run_info),
        )
        archive_fail(
            task_id=task_id,
            title=run_info.get("title", task_id) if run_info else task_id,
            branch=f"auto/{task_id}",
            started=run_info.get("startedAt", "") if run_info else "",
            cost_usd=0.0,
            reason="Agent process crashed (non-zero exit code)",
            status="failed",
            source_id=run_info.get("source_id", "default") if run_info else "default",
        )
        asyncio.create_task(hub.broadcast(task_id, {"type": "crashed"}))

    def on_complete(task_id: str, run_info: dict | None = None):
        asyncio.create_task(hub.broadcast(task_id, {"type": "completed"}))

    def on_queue_start(task_id: str, result: dict | None = None):
        asyncio.create_task(hub.broadcast_all({
            "type": "started",
            "taskId": task_id,
            **(result or {}),
        }))

    def on_group_progress(group_id: str, event: str, task_id: str, task_status: str):
        asyncio.create_task(hub.broadcast_all({
            "type": f"group_{event}",
            "groupId": group_id,
            "taskId": task_id,
            "taskStatus": task_status,
        }))

    def on_rate_limit_waiting(task_id: str, event: str):
        asyncio.create_task(hub.broadcast_all({
            "type": "rate_limit_waiting",
            "taskId": task_id,
            "event": event,
        }))

    async def on_schedule_fire(item):
        """Callback when a scheduled item fires."""
        if item.item_type == "task" and item.task_id:
            if pm.get_active_runs():
                # Busy — enqueue
                try:
                    pm.enqueue_agent(item.task_id, title=item.title)
                    await hub.broadcast_all({
                        "type": "schedule_fired_queued",
                        "scheduleId": item.id,
                        "taskId": item.task_id,
                    })
                except RuntimeError:
                    pass
            else:
                try:
                    result = pm.start_agent(item.task_id, title=item.title)
                    await hub.broadcast_all({
                        "type": "schedule_fired_started",
                        "scheduleId": item.id,
                        "taskId": item.task_id,
                        **result,
                    })
                except RuntimeError:
                    pass
        elif item.item_type == "group" and item.group_id:
            # Always enqueue groups, then try to start from queue if idle
            try:
                pm.enqueue_group(item.group_id, group_name=item.title)
            except RuntimeError:
                pass
            if not pm.get_active_runs():
                result = pm._start_next_from_queue()
                if result and on_queue_start:
                    on_queue_start(result["taskId"], result)
            await hub.broadcast_all({
                "type": "schedule_fired_queued",
                "scheduleId": item.id,
                "groupId": item.group_id,
            })

    scheduler.set_fire_callback(on_schedule_fire)

    pm.start_monitor(
        on_crash=on_crash, on_complete=on_complete,
        on_queue_start=on_queue_start, on_group_progress=on_group_progress,
        on_rate_limit_waiting=on_rate_limit_waiting,
    )
    pm.reload_rate_limit_timers()
    scheduler.reload_pending_timers()
    log_watcher.start()
    state_watcher.start()
    print("[Agent Monitor] Watchers started")
    yield
    log_watcher.stop()
    state_watcher.stop()
    scheduler.stop_all()
    pm.stop_monitor()
    print("[Agent Monitor] Watchers stopped")


app = FastAPI(title="Agent Monitor", lifespan=lifespan)


# ---------------------------------------------------------------------------
# REST routes
# ---------------------------------------------------------------------------

@app.get("/")
async def index():
    """Serve the main UI page."""
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        return HTMLResponse("<h1>index.html not found</h1>", status_code=404)
    return FileResponse(index_path, media_type="text/html")


@app.get("/api/tasks")
async def api_tasks():
    """Get enriched task list from backlog + state."""
    return JSONResponse(get_enriched_tasks())


@app.get("/api/tasks/{task_id}/spec")
async def api_task_spec(task_id: str, sourceId: str | None = None):
    """Get spec content for a specific task."""
    return JSONResponse(get_task_spec_content(task_id, source_id=sourceId))


@app.delete("/api/tasks/{task_id}/spec")
async def api_delete_spec(task_id: str, sourceId: str = "default"):
    """Delete spec file(s) and backlog entry for a task."""
    source_id = sourceId

    # Safety: refuse if task is running, queued, or waiting for rate-limit retry
    for run in pm.get_active_runs():
        if run["taskId"] == task_id:
            raise HTTPException(409, "Cannot delete spec while task is running")
    for item in pm.get_queue():
        if item.get("taskId") == task_id:
            raise HTTPException(409, "Cannot delete spec while task is queued")
    for rl_item in pm.get_rate_limit_waiting():
        if rl_item["taskId"] == task_id:
            raise HTTPException(409, "Cannot delete spec while task has pending retry")

    try:
        deleted_files = delete_task_spec(task_id, source_id=source_id)
    except SpecDeleteError as e:
        raise HTTPException(400, str(e))

    backlog_removed = remove_backlog_entry(task_id, source_id=source_id)

    await hub.broadcast_all({"type": "spec_deleted", "taskId": task_id})
    return JSONResponse({
        "taskId": task_id,
        "deletedFiles": deleted_files,
        "backlogRemoved": backlog_removed,
    })


# ---------------------------------------------------------------------------
# Sources API
# ---------------------------------------------------------------------------

@app.get("/api/sources")
async def api_sources():
    """Get all backlog sources (default + custom)."""
    from dataclasses import asdict
    sources = load_sources()
    return JSONResponse([asdict(s) for s in sources])


@app.post("/api/sources")
async def api_add_source(body: dict):
    """Add a new backlog source folder."""
    folder_path = body.get("path", "").strip()
    if not folder_path:
        raise HTTPException(status_code=400, detail="path is required")
    task_prefix = body.get("task_prefix", "").strip()
    try:
        source = add_source(folder_path, task_prefix=task_prefix)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    from dataclasses import asdict
    return JSONResponse(asdict(source))


@app.post("/api/browse-source")
async def api_browse_and_add_source():
    """Open a native folder picker dialog and return the selected path (does not create a source)."""
    folder_path = await _pick_folder()
    if folder_path is None:
        return JSONResponse({"cancelled": True})
    return JSONResponse({"path": folder_path})


async def _pick_folder() -> str | None:
    """Open a native OS folder picker. Returns absolute path or None if cancelled."""
    if platform.system() == "Darwin":
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e",
            'POSIX path of (choose folder with prompt "Select backlog source folder")',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return None
        path = stdout.decode().strip().rstrip("/")
        return path if path else None

    # Linux fallback: zenity
    proc = await asyncio.create_subprocess_exec(
        "zenity", "--file-selection", "--directory",
        "--title=Select backlog source folder",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return None
    path = stdout.decode().strip()
    return path if path else None


@app.delete("/api/sources/{source_id}")
async def api_remove_source(source_id: str):
    """Remove a non-default backlog source."""
    if source_id == "default":
        raise HTTPException(status_code=400, detail="Cannot remove default source")
    removed = remove_source(source_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Source not found")
    return JSONResponse({"success": True})


@app.get("/api/runs/active")
async def api_active_runs():
    """Get currently running agent processes."""
    return JSONResponse(pm.get_active_runs())


@app.get("/api/runs/archive")
async def api_archive_runs():
    """Get completed and failed runs from registry."""
    return JSONResponse(get_archive_entries())


@app.get("/api/runs/{task_id}/artifact/{name:path}")
async def api_artifact(task_id: str, name: str):
    """
    Read an artifact file from output directory.
    Path-traversal protection: reject any '..' in the name.
    """
    if ".." in name or name.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid artifact path")

    file_path = OUTPUT_DIR / name
    if not file_path.exists():
        # Try under logs/{task_id}/
        file_path = TASK_LOGS_DIR / task_id / name
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Artifact not found")

    # Verify the resolved path is under OUTPUT_DIR
    try:
        file_path.resolve().relative_to(OUTPUT_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")

    content = file_path.read_text(encoding="utf-8", errors="replace")
    return JSONResponse({"content": content, "name": name})


@app.get("/api/runs/{task_id}/log")
async def api_run_log(task_id: str, tail: int = 200):
    """Read last N lines of a task's live.log."""
    log_path = TASK_LOGS_DIR / task_id / "live.log"
    if not log_path.exists():
        return JSONResponse({"lines": [], "taskId": task_id})
    try:
        content = log_path.read_text(encoding="utf-8", errors="replace")
        lines = [l for l in content.splitlines() if l.strip()]
        return JSONResponse({"lines": lines[-tail:], "taskId": task_id})
    except OSError:
        return JSONResponse({"lines": [], "taskId": task_id})


@app.post("/api/runs/start")
async def api_start_run(body: dict):
    """Launch an agent for a task, or enqueue if one is already running."""
    task_id = body.get("taskId")
    title = body.get("title", "")
    source = body.get("source", "")
    source_id = body.get("sourceId", "default")

    if not task_id:
        raise HTTPException(status_code=400, detail="taskId is required")

    # Clear audit cooldown so the task becomes actionable for the subprocess
    def _clear_cooldown(state):
        if task_id in state.audit_history:
            del state.audit_history[task_id]
        return state
    atomic_state_update(_clear_cooldown)

    # If an agent is already running, enqueue instead
    if pm.get_active_runs():
        try:
            entry = pm.enqueue_agent(task_id, title=title, source=source, source_id=source_id)
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e))
        await hub.broadcast_all({"type": "queued", "taskId": task_id, **entry})
        return JSONResponse({"queued": True, **entry})

    try:
        result = pm.start_agent(task_id, title=title, source=source, source_id=source_id)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))

    await hub.broadcast_all({"type": "started", "taskId": task_id, **result})
    return JSONResponse(result)


@app.get("/api/runs/queue")
async def api_queue():
    """Get the current task queue."""
    return JSONResponse(pm.get_queue())


@app.post("/api/runs/queue/group")
async def api_enqueue_group(body: dict):
    """Add a group to the unified queue."""
    group_id = body.get("groupId")
    if not group_id:
        raise HTTPException(400, "groupId is required")
    group = get_group(group_id)
    if not group:
        raise HTTPException(404, "Group not found")
    try:
        entry = pm.enqueue_group(group_id, group_name=group.name)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    await hub.broadcast_all({"type": "queued", "groupId": group_id, **entry})
    return JSONResponse({"queued": True, **entry})


@app.delete("/api/runs/queue/{item_id}")
async def api_dequeue(item_id: str):
    """Remove a task or group from the queue."""
    removed = pm.dequeue_item(item_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Item not found in queue")
    await hub.broadcast_all({"type": "dequeued", "itemId": item_id})
    return JSONResponse({"itemId": item_id, "removed": True})


@app.get("/api/runs/rate-limit-waiting")
async def api_rate_limit_waiting():
    """Get tasks currently waiting for rate-limit retry."""
    return JSONResponse(pm.get_rate_limit_waiting())


@app.post("/api/runs/{task_id}/stop")
async def api_stop_run(task_id: str):
    """Stop a running agent and record in registry."""
    # Get run info before stopping
    run_info = None
    for run in pm.get_active_runs():
        if run["taskId"] == task_id:
            run_info = run
            break

    stopped = pm.stop_agent(task_id)
    if not stopped:
        raise HTTPException(status_code=404, detail="Task not found or not running")

    # Record as stopped in registry and archive (source-aware)
    state = load_state()
    registry_fail_task(
        task_id=task_id,
        title=run_info["title"] if run_info else task_id,
        branch="auto-dev",
        started=run_info["startedAt"] if run_info else "",
        cost_usd=state.total_cost_usd,
        reason="Stopped by user",
        status="stopped",
        registry_path=_resolve_source_registry(run_info),
    )
    archive_fail(
        task_id=task_id,
        title=run_info["title"] if run_info else task_id,
        branch="auto-dev",
        started=run_info["startedAt"] if run_info else "",
        cost_usd=state.total_cost_usd,
        reason="Stopped by user",
        status="stopped",
        source_id=run_info.get("source_id", "default") if run_info else "default",
    )

    await hub.broadcast_all({"type": "stopped", "taskId": task_id})
    return JSONResponse({"taskId": task_id, "stopped": True})


# ---------------------------------------------------------------------------
# Groups API
# ---------------------------------------------------------------------------

@app.get("/api/groups")
async def api_groups():
    """Get all spec groups."""
    from dataclasses import asdict
    groups = load_groups()
    result = []
    for g in groups:
        d = {
            "id": g.id,
            "name": g.name,
            "branch": g.branch,
            "tasks": [asdict(t) for t in g.tasks],
            "currentIndex": g.current_index,
            "status": g.status,
            "task_results": {
                k: asdict(v) for k, v in g.task_results.items()
            },
            "createdAt": g.created_at,
            "updatedAt": g.updated_at,
        }
        result.append(d)
    return JSONResponse(result)


@app.post("/api/groups")
async def api_create_group(body: dict):
    """Create a new spec group."""
    name = body.get("name", "").strip()
    tasks_data = body.get("tasks", [])
    if not name:
        raise HTTPException(400, "name is required")
    if not tasks_data or len(tasks_data) < 1:
        raise HTTPException(400, "at least 1 task is required")

    tasks = [
        GroupTask(
            task_id=t["taskId"],
            title=t.get("title", ""),
            source=t.get("source", ""),
            source_id=t.get("sourceId", "default"),
        )
        for t in tasks_data
    ]
    group = create_group(name, tasks)
    from dataclasses import asdict
    return JSONResponse({
        "id": group.id,
        "name": group.name,
        "branch": group.branch,
        "status": group.status,
    })


@app.put("/api/groups/{group_id}")
async def api_update_group(group_id: str, body: dict):
    """Update a group (rename, reorder tasks). Only when idle."""
    group = get_group(group_id)
    if not group:
        raise HTTPException(404, "Group not found")
    if group.status not in ("idle", "completed", "stopped"):
        raise HTTPException(400, "Cannot edit a running group")

    kwargs = {}
    if "name" in body:
        kwargs["name"] = body["name"]
    if "tasks" in body:
        kwargs["tasks"] = [
            GroupTask(
                task_id=t["taskId"],
                title=t.get("title", ""),
                source=t.get("source", ""),
                source_id=t.get("sourceId", "default"),
            )
            for t in body["tasks"]
        ]

    updated = update_group(group_id, **kwargs)
    if not updated:
        raise HTTPException(404, "Group not found")
    return JSONResponse({"success": True})


@app.delete("/api/groups/{group_id}")
async def api_delete_group(group_id: str):
    """Delete a group. Only when idle/completed/stopped."""
    group = get_group(group_id)
    if not group:
        raise HTTPException(404, "Group not found")
    if group.status == "running":
        raise HTTPException(400, "Cannot delete a running group")
    deleted = delete_group(group_id)
    if not deleted:
        raise HTTPException(404, "Group not found")
    return JSONResponse({"success": True})


@app.post("/api/groups/{group_id}/start")
async def api_start_group(group_id: str, body: dict | None = None):
    """Start or restart execution of a spec group. Pass {"enqueue": true} to enqueue if busy."""
    group = get_group(group_id)
    if not group:
        raise HTTPException(404, "Group not found")
    if group.status not in ("idle", "stopped", "completed"):
        raise HTTPException(400, "Group is not in startable state")

    # Check no other agent is running — enqueue if requested
    if pm.get_active_runs():
        if (body or {}).get("enqueue"):
            try:
                entry = pm.enqueue_group(group_id, group_name=group.name)
            except RuntimeError as e:
                raise HTTPException(409, str(e))
            await hub.broadcast_all({"type": "queued", "groupId": group_id, **entry})
            return JSONResponse({"queued": True, **entry})
        raise HTTPException(409, "Another agent is already running. Stop it first or pass {\"enqueue\": true}.")

    # Reset state for start/restart
    group.current_index = 0
    group.status = "running"
    group.task_results = {}

    # Save via load+modify+save pattern
    groups = load_groups()
    for i, g in enumerate(groups):
        if g.id == group_id:
            groups[i] = group
            break
    save_groups(groups)

    # Create branch from auto-dev (or checkout existing)
    git_run(f"checkout {DEV_BRANCH}")
    git_run(f"pull origin {DEV_BRANCH}")
    ok, _ = git_run(f"checkout -b {group.branch}")
    if not ok:
        git_run(f"checkout {group.branch}")  # already exists

    # Start first task
    first_task = group.tasks[0]
    try:
        pm.start_agent(
            task_id=first_task.task_id,
            title=first_task.title,
            source=first_task.source,
            source_id=first_task.source_id,
            group_id=group.id,
            group_branch=group.branch,
        )
    except RuntimeError as e:
        raise HTTPException(409, str(e))

    await hub.broadcast_all({
        "type": "group_started",
        "groupId": group.id,
        "taskId": first_task.task_id,
    })
    return JSONResponse({"groupId": group.id, "taskId": first_task.task_id, "started": True})


@app.post("/api/groups/{group_id}/stop")
async def api_stop_group(group_id: str):
    """Stop the currently running task in a group."""
    group = get_group(group_id)
    if not group:
        raise HTTPException(404, "Group not found")
    if group.status != "running":
        raise HTTPException(400, "Group is not running")

    # Find and stop the running task
    current_task = group.tasks[group.current_index] if 0 <= group.current_index < len(group.tasks) else None
    if current_task:
        pm.stop_agent(current_task.task_id)

    # Update group status
    groups = load_groups()
    for g in groups:
        if g.id == group_id:
            g.status = "stopped"
            break
    save_groups(groups)

    await hub.broadcast_all({
        "type": "group_stopped",
        "groupId": group.id,
        "taskId": current_task.task_id if current_task else None,
    })
    return JSONResponse({"groupId": group.id, "stopped": True})


@app.post("/api/groups/{group_id}/continue")
async def api_continue_group(group_id: str):
    """Force continue a stopped group — skip to next task."""
    group = get_group(group_id)
    if not group:
        raise HTTPException(404, "Group not found")
    if group.status != "stopped":
        raise HTTPException(400, "Group is not stopped")

    if pm.get_active_runs():
        raise HTTPException(409, "Another agent is already running")

    # Move to next task
    next_index = group.current_index + 1
    if next_index >= len(group.tasks):
        # No more tasks — mark completed
        groups = load_groups()
        for g in groups:
            if g.id == group_id:
                g.status = "completed"
                g.current_index = len(g.tasks)
                break
        save_groups(groups)
        try:
            checkout_branch(DEV_BRANCH)
        except Exception:
            pass
        return JSONResponse({"groupId": group.id, "completed": True})

    # Update group state
    groups = load_groups()
    for g in groups:
        if g.id == group_id:
            g.current_index = next_index
            g.status = "running"
            break
    save_groups(groups)

    # Checkout group branch and start next task
    git_run(f"checkout {group.branch}")
    next_task = group.tasks[next_index]
    try:
        pm.start_agent(
            task_id=next_task.task_id,
            title=next_task.title,
            source=next_task.source,
            source_id=next_task.source_id,
            group_id=group.id,
            group_branch=group.branch,
        )
    except RuntimeError as e:
        raise HTTPException(409, str(e))

    await hub.broadcast_all({
        "type": "group_progress",
        "groupId": group.id,
        "taskId": next_task.task_id,
        "event": "force_continued",
    })
    return JSONResponse({"groupId": group.id, "taskId": next_task.task_id, "continued": True})


@app.post("/api/groups/{group_id}/retry")
async def api_retry_group_task(group_id: str):
    """Retry the current (failed) task in a group."""
    group = get_group(group_id)
    if not group:
        raise HTTPException(404, "Group not found")
    if group.status != "stopped":
        raise HTTPException(400, "Group is not stopped")

    if pm.get_active_runs():
        raise HTTPException(409, "Another agent is already running")

    current_index = group.current_index
    if current_index < 0 or current_index >= len(group.tasks):
        raise HTTPException(400, "No current task to retry")

    # Update group state
    groups = load_groups()
    for g in groups:
        if g.id == group_id:
            g.status = "running"
            # Remove previous result for this task
            current_task_id = g.tasks[current_index].task_id
            g.task_results.pop(current_task_id, None)
            break
    save_groups(groups)

    # Checkout group branch and retry
    git_run(f"checkout {group.branch}")
    current_task = group.tasks[current_index]
    try:
        pm.start_agent(
            task_id=current_task.task_id,
            title=current_task.title,
            source=current_task.source,
            source_id=current_task.source_id,
            group_id=group.id,
            group_branch=group.branch,
        )
    except RuntimeError as e:
        raise HTTPException(409, str(e))

    await hub.broadcast_all({
        "type": "group_progress",
        "groupId": group.id,
        "taskId": current_task.task_id,
        "event": "retried",
    })
    return JSONResponse({"groupId": group.id, "taskId": current_task.task_id, "retried": True})


# ---------------------------------------------------------------------------
# Git API
# ---------------------------------------------------------------------------

@app.get("/api/git/status")
async def api_git_status():
    """Get current branch name."""
    ok, output = git_run("branch --show-current")
    return JSONResponse({"branch": output.strip() if ok else "unknown"})


@app.get("/api/git/branches")
async def api_git_branches():
    """Get list of all local branch names."""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "branch", "--format=%(refname:short)"],
            capture_output=True, text=True, cwd=str(PROJECT_ROOT),
        )
        branches = [b.strip() for b in result.stdout.splitlines() if b.strip()] if result.returncode == 0 else []
    except Exception:
        branches = []
    return JSONResponse({"branches": branches})


@app.post("/api/git/checkout")
async def api_git_checkout(body: dict):
    """Checkout a branch in the main repo."""
    branch = body.get("branch", "")
    if not re.match(r'^[a-zA-Z0-9/_.-]+$', branch):
        raise HTTPException(400, "Invalid branch name")
    ok, output = git_run(f"checkout {branch}")
    if not ok:
        raise HTTPException(500, f"Checkout failed: {output}")
    return JSONResponse({"branch": branch, "success": True})


@app.get("/api/git/files/{branch:path}")
async def api_git_files(branch: str):
    """Get list of files changed on a branch vs auto-dev."""
    if not re.match(r'^[a-zA-Z0-9/_.-]+$', branch):
        raise HTTPException(400, "Invalid branch name")
    ok, output = git_run(f"diff --name-only {DEV_BRANCH}...{branch}")
    files = [f for f in output.strip().split("\n") if f.strip()] if ok else []
    return JSONResponse({"files": files, "branch": branch})


# ---------------------------------------------------------------------------
# Schedule API
# ---------------------------------------------------------------------------

@app.post("/api/schedule")
async def api_create_schedule(body: dict):
    """Schedule a task or group for deferred execution."""
    item_type = body.get("type")
    if item_type not in ("task", "group"):
        raise HTTPException(400, "type must be 'task' or 'group'")

    task_id = body.get("taskId")
    group_id = body.get("groupId")
    title = body.get("title", "")
    delay_seconds = body.get("delaySeconds")
    fire_at_iso = body.get("fireAt")

    if item_type == "task" and not task_id:
        raise HTTPException(400, "taskId is required for task schedules")
    if item_type == "group" and not group_id:
        raise HTTPException(400, "groupId is required for group schedules")

    try:
        item = scheduler.create_schedule(
            item_type=item_type,
            task_id=task_id,
            group_id=group_id,
            title=title,
            delay_seconds=delay_seconds,
            fire_at_iso=fire_at_iso,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    from dataclasses import asdict
    result = asdict(item)
    result["fireAtIso"] = datetime.fromtimestamp(item.fire_at).isoformat()
    await hub.broadcast_all({"type": "scheduled", **result})
    return JSONResponse(result)


@app.get("/api/schedule")
async def api_list_schedules():
    """Get all schedules (pending/fired/cancelled)."""
    return JSONResponse(scheduler.list_schedules())


@app.delete("/api/schedule/{schedule_id}")
async def api_cancel_schedule(schedule_id: str):
    """Cancel a pending schedule."""
    cancelled = scheduler.cancel_schedule(schedule_id)
    if not cancelled:
        raise HTTPException(404, "Schedule not found or not pending")
    await hub.broadcast_all({"type": "schedule_cancelled", "scheduleId": schedule_id})
    return JSONResponse({"scheduleId": schedule_id, "cancelled": True})


# ---------------------------------------------------------------------------
# Spec Editor
# ---------------------------------------------------------------------------

@app.post("/api/tasks/{task_id}/spec/edit")
async def api_spec_edit(task_id: str, body: dict):
    """Ask AI to edit a task spec based on a natural language instruction."""
    content = body.get("content")
    instruction = body.get("instruction")
    if not content or not instruction:
        raise HTTPException(status_code=400, detail="content and instruction are required")

    result = await edit_spec_with_ai(content, instruction)
    if not result["success"]:
        raise HTTPException(status_code=502, detail=result["content"])

    return JSONResponse({"taskId": task_id, "success": True, "content": result["content"]})


@app.post("/api/tasks/{task_id}/spec/save")
async def api_spec_save(task_id: str, body: dict):
    """Save edited spec content to disk."""
    content = body.get("content")
    spec_path_str = body.get("specPath")
    if not content or not spec_path_str:
        raise HTTPException(status_code=400, detail="content and specPath are required")

    spec_path = Path(spec_path_str)

    # Security: specPath must resolve inside SPECS_DIR or a known source folder
    allowed = False
    try:
        spec_path.resolve().relative_to(SPECS_DIR.resolve())
        allowed = True
    except ValueError:
        pass

    if not allowed:
        for src in load_sources():
            if src.is_default:
                continue
            try:
                spec_path.resolve().relative_to(Path(src.path).resolve())
                allowed = True
                break
            except ValueError:
                pass

    if not allowed:
        raise HTTPException(status_code=403, detail="specPath must be inside specs or source directory")

    if not spec_path.exists():
        raise HTTPException(status_code=404, detail="Spec file not found")

    spec_path.write_text(content, encoding="utf-8")
    return JSONResponse({"taskId": task_id, "success": True, "specPath": str(spec_path)})


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """WebSocket endpoint for real-time updates."""
    await hub.connect(ws)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            action = msg.get("action")
            task_id = msg.get("taskId")

            if action == "subscribe" and task_id:
                hub.subscribe(ws, task_id)
            elif action == "unsubscribe" and task_id:
                hub.unsubscribe(ws, task_id)
            elif action == "subscribe_all":
                hub.subscribe_all(ws)

    except WebSocketDisconnect:
        hub.disconnect(ws)
    except Exception:
        hub.disconnect(ws)


# Static files
@app.get("/static/{filename:path}")
async def serve_static(filename: str):
    """Serve static files (app.js, etc.)."""
    file_path = STATIC_DIR / filename
    if not file_path.exists():
        raise HTTPException(404, f"Static file not found: {filename}")
    media_types = {".js": "application/javascript", ".css": "text/css"}
    media_type = media_types.get(file_path.suffix, "application/octet-stream")
    return FileResponse(file_path, media_type=media_type)
