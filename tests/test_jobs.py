from __future__ import annotations

import concurrent.futures
from datetime import timedelta

import pytest

from drama_studio.jobs import JobQueue
from drama_studio.models import utcnow


@pytest.fixture()
def queue(tmp_path):
    q = JobQueue(tmp_path / "jobs.sqlite3")
    yield q
    q.close()


def test_enqueue_and_get(queue):
    job = queue.enqueue("demo.echo", {"hello": "world"})
    assert job.status == "queued"
    assert job.payload == {"hello": "world"}
    assert queue.get(job.id).id == job.id


def test_priority_and_fifo(queue):
    low = queue.enqueue("demo.echo", {}, priority=1)
    high = queue.enqueue("demo.echo", {}, priority=9)
    claimed = queue.claim("worker-a")
    assert claimed is not None
    assert claimed.id == high.id
    queue.complete(claimed.id)
    next_job = queue.claim("worker-a")
    assert next_job is not None
    assert next_job.id == low.id


def test_complete_sets_result(queue):
    job = queue.enqueue("demo.echo", {"x": 1})
    claimed = queue.claim("w")
    queue.complete(claimed.id, {"ok": True})
    done = queue.get(job.id)
    assert done.status == "succeeded"
    assert done.progress == 1.0
    assert done.result == {"ok": True}


def test_retry_then_fail(queue):
    job = queue.enqueue("demo.echo", {}, max_attempts=2)
    first = queue.claim("w")
    queue.fail(first.id, "boom", retryable=True)
    retried = queue.get(job.id)
    assert retried.status == "queued"
    assert retried.attempts == 1
    second = queue.claim("w")
    queue.fail(second.id, "boom", retryable=True)
    failed = queue.get(job.id)
    assert failed.status == "failed"
    assert failed.attempts == 2
    assert "boom" in (failed.error or "")


def test_cancel_queued(queue):
    job = queue.enqueue("demo.echo", {})
    queue.cancel(job.id)
    assert queue.get(job.id).status == "cancelled"
    assert queue.claim("w") is None


def test_cancel_running_sets_flag(queue):
    job = queue.enqueue("demo.echo", {})
    claimed = queue.claim("w")
    queue.cancel(claimed.id)
    assert queue.is_cancelled(claimed.id)
    assert queue.get(job.id).status == "running"
    queue.mark_cancelled(claimed.id)
    assert queue.get(job.id).status == "cancelled"


def test_recover_stale(queue):
    job = queue.enqueue("demo.echo", {})
    claimed = queue.claim("w")
    old = (utcnow() - timedelta(hours=2)).isoformat()
    queue._conn.execute(
        "UPDATE jobs SET started_at = ?, heartbeat_at = ? WHERE id = ?",
        (old, old, claimed.id),
    )
    queue._conn.commit()
    assert queue.recover_stale(300) == 1
    assert queue.get(job.id).status == "queued"


def test_concurrent_claims_are_unique(queue):
    for _ in range(20):
        queue.enqueue("demo.echo", {})

    def claim_one(_):
        job = queue.claim("w")
        return job.id if job else None

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        ids = [item for item in pool.map(claim_one, range(20)) if item]
    assert len(ids) == len(set(ids)) == 20


def test_stats_and_list(queue):
    a = queue.enqueue("demo.echo", {})
    queue.claim("w")
    queue.complete(a.id)
    queue.enqueue("demo.echo", {})
    stats = queue.stats()
    assert stats["succeeded"] == 1
    assert stats["queued"] == 1
    assert len(queue.list_jobs(status="succeeded")) == 1
