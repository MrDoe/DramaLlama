from __future__ import annotations

import pytest

from drama_studio.jobs import JobQueue
from drama_studio.models import Job
from drama_studio.worker import (
    JobCancelled,
    JobContext,
    PermanentJobError,
    Worker,
)


@pytest.fixture()
def queue(tmp_path):
    q = JobQueue(tmp_path / "jobs.sqlite3")
    yield q
    q.close()


def test_worker_success_and_progress(queue):
    seen = []

    def handler(job: Job, ctx: JobContext):
        seen.append(job.id)
        ctx.progress(0.5, "half")
        ctx.progress(1.0, "done")
        return {"answer": 42}

    job = queue.enqueue("test.work", {"x": 1})
    worker = Worker(queue, {"test.work": handler}, worker_id="w")
    assert worker.run_once() is True
    done = queue.get(job.id)
    assert done.status == "succeeded"
    assert done.result == {"answer": 42}
    assert seen == [job.id]


def test_worker_retries_then_fails(queue):
    def handler(job: Job, ctx: JobContext):
        raise RuntimeError("nope")

    job = queue.enqueue("test.work", {}, max_attempts=2)
    worker = Worker(queue, {"test.work": handler}, worker_id="w")
    worker.run_once()
    assert queue.get(job.id).status == "queued"
    worker.run_once()
    failed = queue.get(job.id)
    assert failed.status == "failed"
    assert "nope" in (failed.error or "")


def test_permanent_error_is_not_retried(queue):
    def handler(job: Job, ctx: JobContext):
        raise PermanentJobError("bad payload")

    job = queue.enqueue("test.work", {}, max_attempts=5)
    worker = Worker(queue, {"test.work": handler}, worker_id="w")
    worker.run_once()
    failed = queue.get(job.id)
    assert failed.status == "failed"
    assert failed.attempts == 1


def test_cancellation(queue):
    def handler(job: Job, ctx: JobContext):
        queue.cancel(job.id)
        ctx.check_cancelled()
        return {}

    job = queue.enqueue("test.work", {})
    worker = Worker(queue, {"test.work": handler}, worker_id="w")
    worker.run_once()
    assert queue.get(job.id).status == "cancelled"


def test_missing_handler_fails(queue):
    job = queue.enqueue("unknown.job", {})
    worker = Worker(queue, {}, worker_id="w")
    worker.run_once()
    failed = queue.get(job.id)
    assert failed.status == "failed"
    assert "no handler" in (failed.error or "")


def test_run_once_empty(queue):
    worker = Worker(queue, {}, worker_id="w")
    assert worker.run_once() is False
