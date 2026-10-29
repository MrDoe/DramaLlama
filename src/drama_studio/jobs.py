from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import Job, JobStatus, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    priority INTEGER NOT NULL DEFAULT 0,
    progress REAL NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    result TEXT,
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 1,
    worker_id TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    started_at TEXT,
    heartbeat_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_claim
    ON jobs(status, priority DESC, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
"""

TERMINAL: set[JobStatus] = {"succeeded", "failed", "cancelled"}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class JobQueue:
    """SQLite-backed job queue shared by the API and the worker process.

    A single connection guarded by a re-entrant lock is enough for one local
    user. WAL mode keeps the API responsive while the worker writes progress.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.db_path), timeout=30, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ write

    def enqueue(
        self,
        job_type: str,
        payload: dict[str, Any] | None = None,
        *,
        priority: int = 0,
        max_attempts: int = 1,
        job_id: str | None = None,
    ) -> Job:
        job_id = job_id or uuid.uuid4().hex
        created = utcnow()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO jobs (id, type, payload, status, priority, max_attempts, created_at)
                VALUES (?, ?, ?, 'queued', ?, ?, ?)
                """,
                (
                    job_id,
                    job_type,
                    json.dumps(payload or {}, ensure_ascii=False),
                    priority,
                    max(1, max_attempts),
                    _iso(created),
                ),
            )
            self._conn.commit()
        return self.get(job_id)

    def claim(self, worker_id: str) -> Job | None:
        now = utcnow()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    """
                    SELECT * FROM jobs
                    WHERE status = 'queued'
                    ORDER BY priority DESC, created_at ASC
                    LIMIT 1
                    """
                ).fetchone()
                if row is None:
                    self._conn.commit()
                    return None
                self._conn.execute(
                    """
                    UPDATE jobs
                    SET status = 'running',
                        worker_id = ?,
                        started_at = ?,
                        heartbeat_at = ?,
                        progress = 0,
                        message = '',
                        result = NULL,
                        error = NULL,
                        cancel_requested = 0,
                        attempts = attempts + 1
                    WHERE id = ? AND status = 'queued'
                    """,
                    (worker_id, _iso(now), _iso(now), row["id"]),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return self.get(str(row["id"]))

    def heartbeat(self, job_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET heartbeat_at = ? WHERE id = ? AND status = 'running'",
                (_iso(utcnow()), job_id),
            )
            self._conn.commit()

    def progress(self, job_id: str, progress: float, message: str = "") -> None:
        value = max(0.0, min(1.0, float(progress)))
        with self._lock:
            self._conn.execute(
                """
                UPDATE jobs
                SET progress = ?, message = ?, heartbeat_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (value, message, _iso(utcnow()), job_id),
            )
            self._conn.commit()

    def complete(self, job_id: str, result: dict[str, Any] | None = None) -> Job:
        now = utcnow()
        with self._lock:
            self._conn.execute(
                """
                UPDATE jobs
                SET status = 'succeeded', progress = 1, result = ?,
                    error = NULL, finished_at = ?, heartbeat_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (
                    json.dumps(result or {}, ensure_ascii=False),
                    _iso(now),
                    _iso(now),
                    job_id,
                ),
            )
            self._conn.commit()
        return self.get(job_id)

    def fail(self, job_id: str, error: str, *, retryable: bool = True) -> Job:
        now = utcnow()
        with self._lock:
            row = self._conn.execute(
                "SELECT attempts, max_attempts FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown job {job_id}")
            if retryable and row["attempts"] < row["max_attempts"]:
                self._conn.execute(
                    """
                    UPDATE jobs
                    SET status = 'queued', worker_id = NULL, started_at = NULL,
                        heartbeat_at = NULL, progress = 0, error = ?
                    WHERE id = ?
                    """,
                    (error, job_id),
                )
            else:
                self._conn.execute(
                    """
                    UPDATE jobs
                    SET status = 'failed', error = ?, finished_at = ?,
                        heartbeat_at = ?, worker_id = NULL
                    WHERE id = ?
                    """,
                    (error, _iso(now), _iso(now), job_id),
                )
            self._conn.commit()
        return self.get(job_id)

    def mark_cancelled(self, job_id: str) -> Job:
        now = utcnow()
        with self._lock:
            self._conn.execute(
                """
                UPDATE jobs
                SET status = 'cancelled', finished_at = ?, heartbeat_at = ?,
                    worker_id = NULL
                WHERE id = ? AND status NOT IN ('succeeded', 'failed', 'cancelled')
                """,
                (_iso(now), _iso(now), job_id),
            )
            self._conn.commit()
        return self.get(job_id)

    def cancel(self, job_id: str) -> Job:
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown job {job_id}")
            if row["status"] == "queued":
                return self.mark_cancelled(job_id)
            if row["status"] == "running":
                self._conn.execute(
                    "UPDATE jobs SET cancel_requested = 1 WHERE id = ?", (job_id,)
                )
                self._conn.commit()
        return self.get(job_id)

    def recover_stale(self, older_than_s: float = 300) -> int:
        cutoff = utcnow() - timedelta(seconds=older_than_s)
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, started_at, heartbeat_at FROM jobs WHERE status = 'running'"
            ).fetchall()
            stale = []
            for row in rows:
                stamp = _parse(row["heartbeat_at"] or row["started_at"])
                if stamp is None or stamp < cutoff:
                    stale.append(row["id"])
            for job_id in stale:
                self._conn.execute(
                    """
                    UPDATE jobs
                    SET status = 'queued', worker_id = NULL, started_at = NULL,
                        heartbeat_at = NULL, progress = 0,
                        message = 'recovered after stale lease'
                    WHERE id = ?
                    """,
                    (job_id,),
                )
            self._conn.commit()
        return len(stale)

    # ------------------------------------------------------------------- read

    def get(self, job_id: str) -> Job:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown job {job_id}")
        return self._to_job(row)

    def find(self, job_id: str) -> Job | None:
        try:
            return self.get(job_id)
        except KeyError:
            return None

    def is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT cancel_requested FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return bool(row and row["cancel_requested"])

    def list_jobs(
        self, status: JobStatus | None = None, limit: int = 50
    ) -> list[Job]:
        sql = "SELECT * FROM jobs"
        params: list[Any] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(500, limit)))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._to_job(row) for row in rows]

    def stats(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
            ).fetchall()
        counts = {status: 0 for status in ("queued", "running", "succeeded", "failed", "cancelled")}
        counts.update({str(row["status"]): int(row["n"]) for row in rows})
        return counts

    def _to_job(self, row: sqlite3.Row) -> Job:
        return Job(
            id=row["id"],
            type=row["type"],
            payload=json.loads(row["payload"]),
            status=row["status"],
            priority=row["priority"],
            progress=row["progress"],
            message=row["message"],
            result=json.loads(row["result"]) if row["result"] else None,
            error=row["error"],
            attempts=row["attempts"],
            max_attempts=row["max_attempts"],
            worker_id=row["worker_id"],
            cancel_requested=bool(row["cancel_requested"]),
            created_at=_parse(row["created_at"]),
            started_at=_parse(row["started_at"]),
            finished_at=_parse(row["finished_at"]),
        )
