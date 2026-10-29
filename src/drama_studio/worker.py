from __future__ import annotations

import os
import socket
import threading
import time
import traceback
from collections.abc import Callable
from typing import Any

from .jobs import JobQueue
from .models import Job


class JobCancelled(Exception):
    """Raised by a handler when the user cancelled the job."""


class PermanentJobError(Exception):
    """A failure that should not be retried."""


class JobContext:
    def __init__(self, queue: JobQueue, job: Job) -> None:
        self.queue = queue
        self.job = job
        self._last_write = 0.0
        self._last_progress = -1.0

    def progress(self, value: float, message: str = "") -> None:
        self._last_progress = value
        self._last_write = time.monotonic()
        self.queue.progress(self.job.id, value, message)

    def log(self, message: str) -> None:
        self.queue.progress(self.job.id, self._last_progress, message)

    def heartbeat(self) -> None:
        self.queue.heartbeat(self.job.id)

    def cancelled(self) -> bool:
        return self.queue.is_cancelled(self.job.id)

    def check_cancelled(self) -> None:
        if self.cancelled():
            raise JobCancelled(f"job {self.job.id} cancelled")

    def sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.check_cancelled()
            time.sleep(min(0.1, deadline - time.monotonic()))


Handler = Callable[[Job, JobContext], dict[str, Any] | None]


class Worker:
    """Polls the queue and executes one job at a time.

    The worker is deliberately single-threaded: there is one GPU and Breeze,
    Chatterbox and the local LLM must never be resident together. Long CPU work
    (Whisper alignment) can be added as a separate worker later.
    """

    def __init__(
        self,
        queue: JobQueue,
        handlers: dict[str, Handler],
        *,
        worker_id: str | None = None,
        poll_interval: float = 0.5,
        recover_stale_after_s: float | None = 300,
    ) -> None:
        self.queue = queue
        self.handlers = handlers
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
        self.poll_interval = poll_interval
        self.recover_stale_after_s = recover_stale_after_s
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run_once(self) -> bool:
        job = self.queue.claim(self.worker_id)
        if job is None:
            return False
        self._execute(job)
        return True

    def run_forever(self, stop_event: threading.Event | None = None) -> None:
        if self.recover_stale_after_s is not None:
            self.queue.recover_stale(self.recover_stale_after_s)
        stop = stop_event or self._stop
        while not stop.is_set():
            try:
                worked = self.run_once()
            except Exception:
                # A broken queue should not spin at full speed.
                traceback.print_exc()
                worked = False
            if not worked:
                stop.wait(self.poll_interval)

    def _execute(self, job: Job) -> None:
        handler = self.handlers.get(job.type)
        ctx = JobContext(self.queue, job)
        if handler is None:
            self.queue.fail(
                job.id,
                f"no handler registered for job type {job.type!r}",
                retryable=False,
            )
            return
        try:
            result = handler(job, ctx)
            ctx.check_cancelled()
            self.queue.complete(job.id, result or {})
        except JobCancelled:
            self.queue.mark_cancelled(job.id)
        except PermanentJobError as exc:
            self.queue.fail(job.id, str(exc), retryable=False)
        except Exception:
            self.queue.fail(job.id, traceback.format_exc(), retryable=True)
