"""Async image generation task queue.

When ChatGPT queues an image request, we store the pending conversation
info and poll in the background until the image is ready.
"""

from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Lock
from typing import Any


@dataclass
class ImageTask:
    id: str
    status: str  # "pending" | "polling" | "completed" | "failed"
    prompt: str
    model: str
    created_at: float
    # Set when polling starts
    conversation_id: str = ""
    access_token: str = ""
    device_id: str = ""
    # Set when completed
    result: dict | None = None
    error: str | None = None
    updated_at: float = 0


class TaskService:
    """In-memory task store with background polling."""

    def __init__(self, max_tasks: int = 500):
        self._lock = Lock()
        self._tasks: dict[str, ImageTask] = {}
        self._max_tasks = max_tasks
        self._executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="task-poll")

    def create_task(self, prompt: str, model: str) -> ImageTask:
        task = ImageTask(
            id=uuid.uuid4().hex[:16],
            status="pending",
            prompt=prompt,
            model=model,
            created_at=time.time(),
            updated_at=time.time(),
        )
        with self._lock:
            self._cleanup_old_tasks()
            self._tasks[task.id] = task
        return task

    def get_task(self, task_id: str) -> ImageTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list_tasks(self, limit: int = 50) -> list[ImageTask]:
        with self._lock:
            tasks = sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)
            return tasks[:limit]

    def update_task(self, task_id: str, **kwargs: Any) -> ImageTask | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            for key, value in kwargs.items():
                if hasattr(task, key):
                    setattr(task, key, value)
            task.updated_at = time.time()
            return task

    def submit_poll(self, task_id: str, poll_fn: Any) -> None:
        """Submit a background polling function for a task."""
        self._executor.submit(self._run_poll, task_id, poll_fn)

    def _run_poll(self, task_id: str, poll_fn: Any) -> None:
        try:
            self.update_task(task_id, status="polling")
            result = poll_fn()
            self.update_task(task_id, status="completed", result=result)
            print(f"[task-service] task={task_id} completed")
        except Exception as exc:
            self.update_task(task_id, status="failed", error=str(exc))
            print(f"[task-service] task={task_id} failed: {exc}")

    def _cleanup_old_tasks(self) -> None:
        """Remove old completed/failed tasks when over limit."""
        if len(self._tasks) < self._max_tasks:
            return
        # Remove tasks older than 1 hour that are done
        cutoff = time.time() - 3600
        to_remove = [
            tid for tid, t in self._tasks.items()
            if t.status in ("completed", "failed") and t.updated_at < cutoff
        ]
        for tid in to_remove:
            del self._tasks[tid]


task_service = TaskService()
