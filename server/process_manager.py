"""
Process manager for Agent Monitor server.

Manages agent subprocess lifecycle: start, stop, monitor.
Persists running PIDs for orphan recovery on server restart.
Enforces single-agent execution with a task queue.
Supports rate-limit auto-restart with configurable delay.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Callable

from ..config import (
    OUTPUT_DIR, TASK_LOGS_DIR, PROJECT_ROOT, DEV_BRANCH,
    SERVER_RATE_LIMIT_DELAY, SERVER_RATE_LIMIT_MAX_RETRIES,
)
from ..core.git import commit_work, checkout_branch, count_changed_files, git_run
from ..core.groups import (
    get_group, load_groups, save_groups,
    GroupTaskResult,
)

RUNS_FILE = OUTPUT_DIR / "server_runs.json"
PYTHON = str(Path(__file__).resolve().parents[1] / ".venv" / "bin" / "python")


class ProcessManager:
    """Manages agent subprocesses with a task queue."""

    def __init__(self) -> None:
        self._processes: dict[str, dict] = {}
        self._queue: deque[dict] = deque()
        self._monitor_task: asyncio.Task | None = None
        self._on_crash: Callable[[str], None] | None = None
        self._on_complete: Callable[[str], None] | None = None
        self._on_queue_start: Callable[[str, dict], None] | None = None
        self._on_group_progress: Callable | None = None
        self._on_rate_limit_waiting: Callable | None = None

        # Rate limit retry state: {task_id: {asyncio_task, info, retries, ...}}
        self._rate_limit_timers: dict[str, dict] = {}

        # Currently executing group (persisted for server restart)
        self._active_group_id: str | None = None

        self._load_persisted_runs()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_persisted_runs(self) -> None:
        """Recover tracking of orphaned processes from previous server run."""
        if not RUNS_FILE.exists():
            return
        try:
            data = json.loads(RUNS_FILE.read_text(encoding="utf-8"))
            # Handle both old format (flat dict) and new format (with queue)
            processes = data.get("processes", data) if isinstance(data, dict) else {}
            queue_data = data.get("queue", []) if isinstance(data, dict) else []

            for task_id, info in processes.items():
                pid = info.get("pid")
                if pid and self._pid_alive(pid):
                    self._processes[task_id] = {
                        "pid": pid,
                        "proc": None,
                        "title": info.get("title", ""),
                        "source": info.get("source", ""),
                        "source_id": info.get("source_id", "default"),
                        "started_at": info.get("started_at", ""),
                        "log_path": info.get("log_path", ""),
                        "group_id": info.get("group_id"),
                        "group_branch": info.get("group_branch"),
                    }

            for item in queue_data:
                self._queue.append(item)

            self._active_group_id = data.get("active_group_id") if isinstance(data, dict) else None

        except (json.JSONDecodeError, OSError):
            pass

    def _persist_runs(self) -> None:
        """Save running process info, queue, and rate limit retries to disk."""
        processes = {}
        for task_id, info in self._processes.items():
            processes[task_id] = {
                "pid": info["pid"],
                "title": info.get("title", ""),
                "source": info.get("source", ""),
                "source_id": info.get("source_id", "default"),
                "started_at": info.get("started_at", ""),
                "log_path": info.get("log_path", ""),
                "group_id": info.get("group_id"),
                "group_branch": info.get("group_branch"),
            }

        # Persist rate limit retries for server restart recovery
        rate_limit_retries = {}
        for task_id, rl_info in self._rate_limit_timers.items():
            rate_limit_retries[task_id] = {
                "fire_at": rl_info["fire_at"],
                "retries": rl_info["retries"],
                "run_info": rl_info["run_info"],
                "exit_data": rl_info["exit_data"],
            }

        data = {
            "processes": processes,
            "queue": list(self._queue),
            "rate_limit_retries": rate_limit_retries,
            "active_group_id": self._active_group_id,
        }
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        RUNS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------
    # Start / Stop
    # ------------------------------------------------------------------

    def start_agent(self, task_id: str, title: str = "", source: str = "",
                    source_id: str = "default",
                    group_id: str | None = None,
                    group_branch: str | None = None,
                    branch_override: str | None = None) -> dict:
        """
        Spawn an agent subprocess for a task.
        Only one agent can run at a time.
        Returns dict with pid, task_id, started_at.
        """
        # Cancel pending rate limit retry if exists (avoid duplicate runs)
        self.cancel_rate_limit_retry(task_id)

        if self._processes:
            raise RuntimeError("Another agent is already running")

        # --- Prepare log files ---
        task_log_dir = TASK_LOGS_DIR / task_id
        task_log_dir.mkdir(parents=True, exist_ok=True)

        # Rotate existing execution.log
        exec_log = task_log_dir / "execution.log"
        if exec_log.exists() and exec_log.stat().st_size > 0:
            n = 1
            while (task_log_dir / f"execution.log_{n}").exists():
                n += 1
            exec_log.rename(task_log_dir / f"execution.log_{n}")

        # Clear live.log for fresh run
        log_path = task_log_dir / "live.log"
        log_path.write_text("", encoding="utf-8")

        # Clear exit_status.json from previous run
        exit_status_file = task_log_dir / "exit_status.json"
        if exit_status_file.exists():
            exit_status_file.unlink()

        log_file = open(log_path, "a", buffering=1, encoding="utf-8")

        cmd = [PYTHON, "-u", "-m", "multiagent", "--task", task_id,
               "--mode", "batch", "--source-id", source_id]

        # Branch override (for rate limit restart on same branch)
        effective_branch = branch_override or group_branch
        if effective_branch:
            cmd += ["--branch", effective_branch, "--no-branch-cleanup"]

        env = {**os.environ, "PYTHONUNBUFFERED": "1"}

        proc = subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            cwd=str(PROJECT_ROOT),
            env=env,
        )

        started_at = datetime.now().isoformat()

        self._processes[task_id] = {
            "pid": proc.pid,
            "proc": proc,
            "title": title,
            "source": source,
            "source_id": source_id,
            "started_at": started_at,
            "log_path": str(log_path),
            "log_file": log_file,
            "group_id": group_id,
            "group_branch": group_branch,
        }
        self._persist_runs()

        return {
            "taskId": task_id,
            "pid": proc.pid,
            "startedAt": started_at,
        }

    def stop_agent(self, task_id: str) -> bool:
        """
        Gracefully stop a running agent.
        SIGINT -> wait 10s -> SIGTERM -> wait 5s -> SIGKILL.
        Also cancels any pending rate-limit retry for this task.
        """
        # Cancel rate limit retry if waiting
        self.cancel_rate_limit_retry(task_id)

        info = self._processes.get(task_id)
        if not info:
            return False

        pid = info["pid"]

        try:
            pgid = os.getpgid(pid)
        except OSError:
            self._cleanup_process(task_id)
            return True

        # Step 1: SIGINT (graceful)
        try:
            os.killpg(pgid, signal.SIGINT)
        except OSError:
            self._cleanup_process(task_id)
            return True

        if self._wait_for_exit(pid, timeout=10):
            self._cleanup_process(task_id)
            return True

        # Step 2: SIGTERM
        try:
            os.killpg(pgid, signal.SIGTERM)
        except OSError:
            self._cleanup_process(task_id)
            return True

        if self._wait_for_exit(pid, timeout=5):
            self._cleanup_process(task_id)
            return True

        # Step 3: SIGKILL
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            pass

        self._cleanup_process(task_id)
        return True

    def _wait_for_exit(self, pid: int, timeout: float) -> bool:
        """Wait for a process to exit, return True if it exited."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self._pid_alive(pid):
                return True
            time.sleep(0.5)
        return False

    def _cleanup_process(self, task_id: str) -> None:
        """Remove process from tracking, reap zombie, close log file, safety-commit."""
        info = self._processes.pop(task_id, None)
        if info:
            # Reap zombie process
            proc = info.get("proc")
            if proc is not None:
                try:
                    proc.wait(timeout=1)
                except Exception:
                    pass
            log_file = info.get("log_file")
            if log_file and not log_file.closed:
                log_file.close()
            # Safety-net: commit partial work
            is_group = bool(info.get("group_id"))
            branch = info.get("group_branch") or f"auto/{task_id}"
            try:
                commit_work(task_id, branch, success=False)
                if not is_group:
                    checkout_branch(DEV_BRANCH)
            except Exception:
                pass
        self._persist_runs()

    # ------------------------------------------------------------------
    # Unified Queue (tasks + groups mixed)
    # ------------------------------------------------------------------

    def enqueue_agent(self, task_id: str, title: str = "", source: str = "",
                      source_id: str = "default") -> dict:
        """Add a standalone task to the queue. Returns queue entry."""
        # Check not already queued
        for item in self._queue:
            if item.get("taskId") == task_id:
                raise RuntimeError(f"Task {task_id} is already in the queue")
        # Check not already running
        if task_id in self._processes:
            raise RuntimeError(f"Task {task_id} is already running")

        entry = {
            "type": "task",
            "taskId": task_id,
            "title": title,
            "source": source,
            "sourceId": source_id,
            "queuedAt": datetime.now().isoformat(),
        }
        self._queue.append(entry)
        self._persist_runs()
        return entry

    def enqueue_group(self, group_id: str, group_name: str = "") -> dict:
        """Add a group to the unified queue. Returns queue entry."""
        # Check not already queued
        for item in self._queue:
            if item.get("type") == "group" and item.get("groupId") == group_id:
                raise RuntimeError(f"Group {group_id} is already in the queue")
        # Check not actively executing
        if self._active_group_id == group_id:
            raise RuntimeError(f"Group {group_id} is already running")

        entry = {
            "type": "group",
            "groupId": group_id,
            "groupName": group_name,
            "queuedAt": datetime.now().isoformat(),
        }
        self._queue.append(entry)
        self._persist_runs()
        return entry

    def dequeue_agent(self, task_id: str) -> bool:
        """Remove a task from the queue by taskId. Backward compat alias."""
        return self.dequeue_item(task_id)

    def dequeue_item(self, item_id: str) -> bool:
        """
        Remove an item from the queue by taskId or groupId.
        Returns True if found and removed.
        """
        for i, item in enumerate(self._queue):
            entry_type = item.get("type", "task")
            if entry_type == "task" and item.get("taskId") == item_id:
                del self._queue[i]
                self._persist_runs()
                return True
            if entry_type == "group" and item.get("groupId") == item_id:
                del self._queue[i]
                self._persist_runs()
                return True
        return False

    def get_queue(self) -> list[dict]:
        """Return the current queue as a list."""
        return list(self._queue)

    def _start_next_from_queue(self) -> dict | None:
        """
        Try to start the next item from the unified queue.
        Handles both task and group entries.
        Returns start info dict or None.
        """
        if not self._queue or self._processes:
            return None
        entry = self._queue.popleft()
        entry_type = entry.get("type", "task")

        if entry_type == "group":
            return self._start_group_from_queue(entry)
        else:
            return self._start_task_from_queue(entry)

    def _start_task_from_queue(self, entry: dict) -> dict | None:
        """Start a standalone task from a queue entry."""
        try:
            result = self.start_agent(
                task_id=entry["taskId"],
                title=entry.get("title", ""),
                source=entry.get("source", ""),
                source_id=entry.get("sourceId", "default"),
            )
            return result
        except RuntimeError:
            self._queue.appendleft(entry)
            self._persist_runs()
            return None

    def _start_group_from_queue(self, entry: dict) -> dict | None:
        """
        Start a group from a queue entry.
        Sets up git branch, resets group state, starts first task.
        """
        group_id = entry["groupId"]
        group = get_group(group_id)
        if not group:
            print(f"[Queue] Group {group_id} not found, skipping")
            # Try next item
            return self._start_next_from_queue()

        if not group.tasks:
            print(f"[Queue] Group {group_id} has no tasks, skipping")
            return self._start_next_from_queue()

        # Reset group state for fresh start
        groups = load_groups()
        for g in groups:
            if g.id == group_id:
                g.current_index = 0
                g.status = "running"
                g.task_results = {}
                g.updated_at = datetime.now().isoformat()
                break
        save_groups(groups)

        # Git: checkout auto-dev, pull, create/checkout group branch
        git_run(f"checkout {DEV_BRANCH}")
        git_run(f"pull origin {DEV_BRANCH}")
        ok, _ = git_run(f"checkout -b {group.branch}")
        if not ok:
            git_run(f"checkout {group.branch}")  # already exists

        # Start first task
        first_task = group.tasks[0]
        self._active_group_id = group_id
        try:
            result = self.start_agent(
                task_id=first_task.task_id,
                title=first_task.title,
                source=first_task.source,
                source_id=first_task.source_id,
                group_id=group.id,
                group_branch=group.branch,
            )
            self._persist_runs()
            return result
        except RuntimeError as e:
            print(f"[Queue] Failed to start group {group_id} first task: {e}")
            self._active_group_id = None
            # Mark group as stopped
            groups = load_groups()
            for g in groups:
                if g.id == group_id:
                    g.status = "stopped"
                    g.updated_at = datetime.now().isoformat()
                    break
            save_groups(groups)
            self._persist_runs()
            return None

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def _is_running(self, info: dict) -> bool:
        """Check if a tracked process is still running (handles zombies)."""
        proc = info.get("proc")
        if proc is not None:
            return proc.poll() is None
        return self._pid_alive(info["pid"])

    def is_task_running(self, task_id: str) -> bool:
        """Check if a task is currently running."""
        info = self._processes.get(task_id)
        if not info:
            return False
        return self._is_running(info)

    def get_active_runs(self) -> list[dict]:
        """Return list of all running agent processes."""
        runs = []
        for task_id, info in list(self._processes.items()):
            if not self._is_running(info):
                continue
            elapsed = 0.0
            if info.get("started_at"):
                try:
                    start = datetime.fromisoformat(info["started_at"])
                    elapsed = (datetime.now() - start).total_seconds()
                except ValueError:
                    pass
            runs.append({
                "taskId": task_id,
                "title": info.get("title", ""),
                "source": info.get("source", ""),
                "source_id": info.get("source_id", "default"),
                "pid": info["pid"],
                "startedAt": info.get("started_at", ""),
                "elapsedSeconds": round(elapsed, 1),
                "logPath": info.get("log_path", ""),
                "groupId": info.get("group_id"),
                "groupBranch": info.get("group_branch"),
            })
        return runs

    # ------------------------------------------------------------------
    # Rate limit auto-restart
    # ------------------------------------------------------------------

    def _check_rate_limited_exit(self, task_id: str) -> dict | None:
        """Read exit_status.json to check if process exited due to rate limit."""
        status_file = TASK_LOGS_DIR / task_id / "exit_status.json"
        if not status_file.exists():
            return None
        try:
            data = json.loads(status_file.read_text(encoding="utf-8"))
            if data.get("status") == "rate_limited":
                return data
        except (json.JSONDecodeError, OSError):
            pass
        return None

    def _schedule_rate_limit_retry(
        self, task_id: str, run_info: dict, exit_data: dict,
    ) -> None:
        """Schedule an auto-restart after SERVER_RATE_LIMIT_DELAY seconds."""
        # Check retry count
        existing = self._rate_limit_timers.get(task_id)
        retries = (existing["retries"] + 1) if existing else 1

        if retries > SERVER_RATE_LIMIT_MAX_RETRIES:
            print(f"[RateLimit] Max retries ({SERVER_RATE_LIMIT_MAX_RETRIES}) "
                  f"exhausted for {task_id}. Giving up.")
            return  # Fall through to normal crash handling

        fire_at = datetime.now().timestamp() + SERVER_RATE_LIMIT_DELAY
        print(f"[RateLimit] Scheduling retry #{retries} for {task_id} "
              f"in {SERVER_RATE_LIMIT_DELAY}s")

        # Cancel existing timer if any
        if existing and "async_task" in existing:
            existing["async_task"].cancel()

        async_task = asyncio.create_task(
            self._rate_limit_restart_after_delay(task_id, SERVER_RATE_LIMIT_DELAY)
        )

        self._rate_limit_timers[task_id] = {
            "async_task": async_task,
            "fire_at": fire_at,
            "retries": retries,
            "run_info": run_info,
            "exit_data": exit_data,
        }
        self._persist_runs()

    async def _rate_limit_restart_after_delay(
        self, task_id: str, delay: float,
    ) -> None:
        """Wait delay seconds, then restart the agent."""
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            print(f"[RateLimit] Timer cancelled for {task_id}")
            return

        rl_info = self._rate_limit_timers.get(task_id)
        if not rl_info:
            return

        run_info = rl_info["run_info"]
        exit_data = rl_info["exit_data"]
        branch = exit_data.get("branch", "")

        print(f"[RateLimit] Timer fired for {task_id}, attempting restart "
              f"(retry #{rl_info['retries']})")

        # Remove from timers BEFORE restart attempt
        self._rate_limit_timers.pop(task_id, None)

        if self._processes:
            # Another agent is running — enqueue instead
            print(f"[RateLimit] Agent busy, enqueueing {task_id}")
            try:
                self.enqueue_agent(
                    task_id=task_id,
                    title=run_info.get("title", ""),
                    source=run_info.get("source", ""),
                    source_id=run_info.get("source_id", "default"),
                )
            except RuntimeError:
                pass
            self._persist_runs()
            return

        try:
            result = self.start_agent(
                task_id=task_id,
                title=run_info.get("title", ""),
                source=run_info.get("source", ""),
                source_id=run_info.get("source_id", "default"),
                group_id=run_info.get("group_id"),
                group_branch=run_info.get("group_branch"),
                branch_override=branch if branch and not run_info.get("group_branch") else None,
            )
            print(f"[RateLimit] Restarted {task_id} (pid={result['pid']})")
            if self._on_rate_limit_waiting:
                self._on_rate_limit_waiting(task_id, "restarted")
        except RuntimeError as e:
            print(f"[RateLimit] Failed to restart {task_id}: {e}")

        self._persist_runs()

    def cancel_rate_limit_retry(self, task_id: str) -> bool:
        """Cancel a pending rate-limit retry timer. Returns True if cancelled."""
        rl_info = self._rate_limit_timers.pop(task_id, None)
        if not rl_info:
            return False
        async_task = rl_info.get("async_task")
        if async_task and not async_task.done():
            async_task.cancel()
        self._persist_runs()
        print(f"[RateLimit] Cancelled retry for {task_id}")
        return True

    def get_rate_limit_waiting(self) -> list[dict]:
        """Return list of tasks waiting for rate-limit retry."""
        result = []
        now = datetime.now().timestamp()
        for task_id, rl_info in self._rate_limit_timers.items():
            fire_at = rl_info["fire_at"]
            remaining = max(0, fire_at - now)
            result.append({
                "taskId": task_id,
                "retries": rl_info["retries"],
                "maxRetries": SERVER_RATE_LIMIT_MAX_RETRIES,
                "fireAt": datetime.fromtimestamp(fire_at).isoformat(),
                "remainingSeconds": round(remaining, 1),
                "branch": rl_info["exit_data"].get("branch", ""),
                "groupId": rl_info["run_info"].get("group_id"),
            })
        return result

    def reload_rate_limit_timers(self) -> None:
        """
        On server startup, reload persisted rate-limit timers and
        reschedule them with the remaining time.
        """
        if not RUNS_FILE.exists():
            return
        try:
            data = json.loads(RUNS_FILE.read_text(encoding="utf-8"))
            retries_data = data.get("rate_limit_retries", {})
        except (json.JSONDecodeError, OSError):
            return

        now = datetime.now().timestamp()
        for task_id, rl_data in retries_data.items():
            fire_at = rl_data.get("fire_at", 0)
            remaining = fire_at - now

            if remaining <= 0:
                # Overdue — fire immediately (with small delay for event loop)
                remaining = 2.0
                print(f"[RateLimit] Overdue timer for {task_id}, firing in 2s")
            else:
                print(f"[RateLimit] Resuming timer for {task_id}, "
                      f"{remaining:.0f}s remaining")

            async_task = asyncio.create_task(
                self._rate_limit_restart_after_delay(task_id, remaining)
            )

            self._rate_limit_timers[task_id] = {
                "async_task": async_task,
                "fire_at": fire_at,
                "retries": rl_data.get("retries", 1),
                "run_info": rl_data.get("run_info", {}),
                "exit_data": rl_data.get("exit_data", {}),
            }

    # ------------------------------------------------------------------
    # Background monitor
    # ------------------------------------------------------------------

    def start_monitor(
        self,
        on_crash: Callable[[str], None] | None = None,
        on_complete: Callable[[str], None] | None = None,
        on_queue_start: Callable[[str, dict], None] | None = None,
        on_group_progress: Callable | None = None,
        on_rate_limit_waiting: Callable | None = None,
    ) -> asyncio.Task:
        """Start background monitor that checks PID liveness every 3s."""
        self._on_crash = on_crash
        self._on_complete = on_complete
        self._on_queue_start = on_queue_start
        self._on_group_progress = on_group_progress
        self._on_rate_limit_waiting = on_rate_limit_waiting
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        return self._monitor_task

    async def _monitor_loop(self) -> None:
        """Periodically check running processes for crashes/completions."""
        while True:
            try:
                await asyncio.sleep(3)
                for task_id in list(self._processes.keys()):
                    info = self._processes.get(task_id)
                    if not info:
                        continue
                    if not self._is_running(info):
                        group_id = info.get("group_id")
                        proc = info.get("proc")
                        returncode = proc.returncode if proc is not None else 0

                        if group_id:
                            # Group task finished — delegate to group handler
                            self._handle_group_task_done(task_id, info, returncode)
                        else:
                            # Standalone task — check for rate limit first
                            is_crash = returncode is not None and returncode != 0

                            if is_crash:
                                exit_data = self._check_rate_limited_exit(task_id)
                                if exit_data:
                                    # Rate limited — schedule retry instead of crash
                                    run_info = {
                                        "taskId": task_id,
                                        "title": info.get("title", ""),
                                        "source": info.get("source", ""),
                                        "source_id": info.get("source_id", "default"),
                                        "startedAt": info.get("started_at", ""),
                                        "group_id": info.get("group_id"),
                                        "group_branch": info.get("group_branch"),
                                    }
                                    self._cleanup_process(task_id)
                                    self._schedule_rate_limit_retry(
                                        task_id, run_info, exit_data,
                                    )
                                    if self._on_rate_limit_waiting:
                                        self._on_rate_limit_waiting(task_id, "waiting")
                                    continue

                            # Normal crash or completion
                            run_info = {
                                "taskId": task_id,
                                "title": info.get("title", ""),
                                "source": info.get("source", ""),
                                "startedAt": info.get("started_at", ""),
                            }
                            if is_crash:
                                if self._on_crash:
                                    self._on_crash(task_id, run_info)
                            else:
                                if self._on_complete:
                                    self._on_complete(task_id, run_info)
                            self._cleanup_process(task_id)

                            # Try to start next queued task
                            result = self._start_next_from_queue()
                            if result and self._on_queue_start:
                                self._on_queue_start(result["taskId"], result)

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[Monitor] Error: {e}")

    def stop_monitor(self) -> None:
        """Cancel the background monitor task."""
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()

    # ------------------------------------------------------------------
    # Group task completion handler
    # ------------------------------------------------------------------

    def _handle_group_task_done(self, task_id: str, info: dict, returncode: int | None) -> None:
        """Handle completion of a group task. Auto-assess and decide next step."""
        group_id = info["group_id"]
        branch = info["group_branch"]

        # Check for rate limit exit BEFORE normal assessment
        is_crash = returncode is not None and returncode != 0
        if is_crash:
            exit_data = self._check_rate_limited_exit(task_id)
            if exit_data:
                run_info = {
                    "taskId": task_id,
                    "title": info.get("title", ""),
                    "source": info.get("source", ""),
                    "source_id": info.get("source_id", "default"),
                    "startedAt": info.get("started_at", ""),
                    "group_id": group_id,
                    "group_branch": branch,
                }
                # Record rate limit in group results for observability
                groups = load_groups()
                group_obj = next((g for g in groups if g.id == group_id), None)
                if group_obj:
                    group_obj.task_results[task_id] = GroupTaskResult(
                        status="rate_limited",
                        cost_usd=0.0,
                        started_at=info.get("started_at", ""),
                        finished_at=datetime.now().isoformat(),
                        reason="Rate limit exceeded, retry scheduled",
                        has_changes=False,
                        auto_continued=True,
                    )
                    save_groups(groups)
                self._cleanup_process(task_id)
                self._schedule_rate_limit_retry(task_id, run_info, exit_data)
                if self._on_rate_limit_waiting:
                    self._on_rate_limit_waiting(task_id, "waiting")
                return

        # Load fresh group state
        groups = load_groups()
        group = next((g for g in groups if g.id == group_id), None)
        if not group:
            print(f"[Groups] Group {group_id} not found, cleaning up")
            self._cleanup_process(task_id)
            return

        # 1. Assess result
        changed_files = count_changed_files(branch)
        has_changes = changed_files > 0

        # 2. Determine task result
        if not is_crash:
            task_status = "done"
        elif has_changes:
            task_status = "failed"  # but will auto-continue
        else:
            task_status = "failed"  # real failure, will stop

        # 3. Record result
        now = datetime.now().isoformat()
        group.task_results[task_id] = GroupTaskResult(
            status=task_status,
            cost_usd=0.0,
            started_at=info.get("started_at", ""),
            finished_at=now,
            reason=f"exit code {returncode}" if is_crash else None,
            has_changes=has_changes,
            auto_continued=is_crash and has_changes,
        )

        # 4. Cleanup (group-aware, won't checkout)
        self._cleanup_process(task_id)

        # 5. Auto-assess: continue or stop?
        should_continue = True
        if is_crash and not has_changes:
            should_continue = False  # Real crash, no work done

        if should_continue and group.current_index + 1 < len(group.tasks):
            # Start next task
            group.current_index += 1
            group.status = "running"
            group.updated_at = now
            save_groups(groups)

            next_task = group.tasks[group.current_index]
            try:
                self.start_agent(
                    task_id=next_task.task_id,
                    title=next_task.title,
                    source=next_task.source,
                    source_id=next_task.source_id,
                    group_id=group_id,
                    group_branch=branch,
                )
            except RuntimeError as e:
                print(f"[Groups] Failed to start next task: {e}")
                group.status = "stopped"
                group.updated_at = datetime.now().isoformat()
                save_groups(groups)

            if self._on_group_progress:
                self._on_group_progress(group_id, "continued", task_id, task_status)

        elif should_continue:
            # Last task in group — completed!
            group.current_index = len(group.tasks)
            group.status = "completed"
            group.updated_at = now
            save_groups(groups)
            self._active_group_id = None
            try:
                checkout_branch(DEV_BRANCH)
            except Exception:
                pass
            if self._on_group_progress:
                self._on_group_progress(group_id, "completed", task_id, task_status)

            # Try to start next queued item
            result = self._start_next_from_queue()
            if result and self._on_queue_start:
                self._on_queue_start(result["taskId"], result)
        else:
            # Real failure — stop group
            group.status = "stopped"
            group.updated_at = now
            save_groups(groups)
            self._active_group_id = None
            try:
                checkout_branch(DEV_BRANCH)
            except Exception:
                pass
            if self._on_group_progress:
                self._on_group_progress(group_id, "stopped", task_id, task_status)

            # Try to start next queued item
            result = self._start_next_from_queue()
            if result and self._on_queue_start:
                self._on_queue_start(result["taskId"], result)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        """Check if a process with given PID is still running."""
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
