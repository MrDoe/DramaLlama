"""Lease recovery while a worker is running, not only when one starts.

A render orphaned by a restart sat "running" with an idle GPU and nothing behind
it: recovery ran once, at worker startup, when the job was seconds old and
therefore not yet stale. The queue stays wedged until the *next* restart, which
nobody triggers from a browser.
"""

from __future__ import annotations

import sqlite3
import threading
import time

from drama_studio.jobs import JobQueue
from drama_studio.worker import Worker

OLD = "2020-01-01T00:00:00+00:00"


def _orphan(queue: JobQueue, job_type: str = "slow") -> str:
    """A claimed job whose worker has stopped breathing."""
    job = queue.enqueue(job_type, {})
    claimed = queue.claim("dead-worker")
    assert claimed is not None and claimed.id == job.id
    queue.progress(job.id, 0.4, "halfway")
    with sqlite3.connect(queue.db_path) as handle:
        handle.execute("UPDATE jobs SET heartbeat_at = ? WHERE id = ?", (OLD, job.id))
        handle.commit()
    return job.id


def _wait_for(queue: JobQueue, job_id: str, status: str, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if queue.get(job_id).status == status:
            return True
        time.sleep(0.02)
    return False


def test_recovery_reclaims_an_orphan_without_a_restart(tmp_path):
    queue = JobQueue(tmp_path / "jobs.sqlite3")
    done = threading.Event()

    def handler(job, ctx):
        done.set()
        return {"ok": True}

    orphan = _orphan(queue)
    worker = Worker(
        queue,
        {"slow": handler},
        worker_id="alive",
        recover_stale_after_s=1.0,
        recovery_interval_s=0.05,
        poll_interval=0.01,
    )
    thread = threading.Thread(target=worker.run_forever, daemon=True)
    thread.start()
    try:
        assert _wait_for(queue, orphan, "succeeded"), "the orphan was never reclaimed"
    finally:
        worker.stop()
        thread.join(timeout=2)
    assert done.is_set()


def test_a_healthy_job_keeps_its_lease_without_reporting_progress(tmp_path):
    """The keep-alive must outrun recovery, or a real render gets stolen."""
    queue = JobQueue(tmp_path / "jobs.sqlite3")

    def handler(job, ctx):
        # No ctx.progress at all: this is a single long call into the engine.
        time.sleep(0.45)
        return {"ok": True}

    job = queue.enqueue("slow")
    worker = Worker(
        queue,
        {"slow": handler},
        worker_id="alive",
        recover_stale_after_s=0.15,
        recovery_interval_s=0.05,
        heartbeat_interval_s=0.05,
        poll_interval=0.01,
    )
    thread = threading.Thread(target=worker.run_forever, daemon=True)
    thread.start()
    try:
        assert _wait_for(queue, job.id, "succeeded", timeout=4.0), (
            "a live render was reclaimed mid-flight"
        )
    finally:
        worker.stop()
        thread.join(timeout=2)
