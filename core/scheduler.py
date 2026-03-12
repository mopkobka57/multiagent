"""
Scheduler — timer-based execution of tasks and groups.

Supports "run in N seconds" and "run at specific time" scheduling.
Persistence: output/schedules.json with FileLock for thread safety.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Callable, Any

from filelock import FileLock

from ..config import OUTPUT_DIR


SCHEDULES_FILE = OUTPUT_DIR / "schedules.json"
_SCHEDULES_LOCK = FileLock(str(SCHEDULES_FILE) + ".lock", timeout=10)


@dataclass
class ScheduledItem:
    id: str                     # UUID
    item_type: str              # "task" | "group"
    task_id: str | None         # For tasks
    group_id: str | None        # For groups
    title: str                  # Display name
    schedule_type: str          # "delay" | "fixed"
    fire_at: float              # Unix timestamp when to fire
    status: str                 # "pending" | "fired" | "cancelled"
    created_at: str             # ISO timestamp


class Scheduler:
    """Timer-based scheduler for deferred task/group execution."""

    def __init__(self) -> None:
        self._timers: dict[str, asyncio.Task] = {}  # schedule_id → asyncio.Task
        self._items: dict[str, ScheduledItem] = {}   # schedule_id → item
        self._fire_callback: Callable[[ScheduledItem], Any] | None = None
        self._load_items()

    def set_fire_callback(self, fn: Callable[[ScheduledItem], Any]) -> None:
        """Set the callback invoked when a schedule fires."""
        self._fire_callback = fn

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_items(self) -> None:
        """Load scheduled items from disk."""
        if not SCHEDULES_FILE.exists():
            return
        try:
            with _SCHEDULES_LOCK:
                data = json.loads(SCHEDULES_FILE.read_text(encoding="utf-8"))
            for d in data:
                item = ScheduledItem(**d)
                self._items[item.id] = item
        except (json.JSONDecodeError, OSError, TypeError):
            pass

    def _save_items(self) -> None:
        """Save all scheduled items to disk."""
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        data = [asdict(item) for item in self._items.values()]
        with _SCHEDULES_LOCK:
            SCHEDULES_FILE.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

    # ------------------------------------------------------------------
    # Create / Cancel
    # ------------------------------------------------------------------

    def create_schedule(
        self,
        item_type: str,
        task_id: str | None = None,
        group_id: str | None = None,
        title: str = "",
        delay_seconds: float | None = None,
        fire_at_iso: str | None = None,
    ) -> ScheduledItem:
        """
        Create a new scheduled execution.

        Either delay_seconds or fire_at_iso must be provided.
        Returns the created ScheduledItem.
        """
        now = datetime.now()

        if delay_seconds is not None:
            fire_at = now.timestamp() + delay_seconds
            schedule_type = "delay"
        elif fire_at_iso is not None:
            fire_at = datetime.fromisoformat(fire_at_iso).timestamp()
            if fire_at <= now.timestamp():
                raise ValueError("fireAt must be in the future")
            schedule_type = "fixed"
        else:
            raise ValueError("Either delaySeconds or fireAt is required")

        item = ScheduledItem(
            id=str(uuid.uuid4()),
            item_type=item_type,
            task_id=task_id,
            group_id=group_id,
            title=title,
            schedule_type=schedule_type,
            fire_at=fire_at,
            status="pending",
            created_at=now.isoformat(),
        )

        self._items[item.id] = item
        self._save_items()

        # Start timer
        delay = max(0, fire_at - now.timestamp())
        self._timers[item.id] = asyncio.create_task(
            self._timer_fire(item.id, delay)
        )

        return item

    def cancel_schedule(self, schedule_id: str) -> bool:
        """Cancel a pending schedule. Returns True if cancelled."""
        item = self._items.get(schedule_id)
        if not item or item.status != "pending":
            return False

        # Cancel asyncio timer
        timer = self._timers.pop(schedule_id, None)
        if timer and not timer.done():
            timer.cancel()

        item.status = "cancelled"
        self._save_items()
        return True

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def list_schedules(self) -> list[dict]:
        """Return all schedules as dicts."""
        now = datetime.now().timestamp()
        result = []
        for item in self._items.values():
            d = asdict(item)
            d["fireAtIso"] = datetime.fromtimestamp(item.fire_at).isoformat()
            if item.status == "pending":
                d["remainingSeconds"] = round(max(0, item.fire_at - now), 1)
            else:
                d["remainingSeconds"] = 0
            result.append(d)
        # Sort: pending first, then by fire_at
        result.sort(key=lambda x: (0 if x["status"] == "pending" else 1, x["fire_at"]))
        return result

    def list_pending(self) -> list[ScheduledItem]:
        """Return only pending schedules."""
        return [item for item in self._items.values() if item.status == "pending"]

    # ------------------------------------------------------------------
    # Timer
    # ------------------------------------------------------------------

    async def _timer_fire(self, schedule_id: str, delay: float) -> None:
        """Wait for delay, then fire the schedule."""
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return

        item = self._items.get(schedule_id)
        if not item or item.status != "pending":
            return

        item.status = "fired"
        self._save_items()
        self._timers.pop(schedule_id, None)

        print(f"[Scheduler] Firing schedule {schedule_id}: "
              f"{item.item_type} {item.task_id or item.group_id}")

        if self._fire_callback:
            try:
                result = self._fire_callback(item)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                print(f"[Scheduler] Fire callback error: {e}")

    # ------------------------------------------------------------------
    # Server restart recovery
    # ------------------------------------------------------------------

    def reload_pending_timers(self) -> None:
        """
        On server startup, restart timers for pending schedules.
        Fires immediately (with small delay) if overdue.
        """
        now = datetime.now().timestamp()
        for item in list(self._items.values()):
            if item.status != "pending":
                continue

            remaining = item.fire_at - now
            if remaining <= 0:
                remaining = 2.0  # Fire in 2s if overdue
                print(f"[Scheduler] Overdue schedule {item.id}, firing in 2s")
            else:
                print(f"[Scheduler] Resuming schedule {item.id}, "
                      f"{remaining:.0f}s remaining")

            self._timers[item.id] = asyncio.create_task(
                self._timer_fire(item.id, remaining)
            )

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def stop_all(self) -> None:
        """Cancel all running timers (for server shutdown)."""
        for timer in self._timers.values():
            if not timer.done():
                timer.cancel()
        self._timers.clear()
