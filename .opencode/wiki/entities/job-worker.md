---
title: Job worker
tags: [jobs, sqlite, worker, api, sse]
sourceRefs:
  - src/drama_studio/jobs.py
  - src/drama_studio/worker.py
  - src/drama_studio/api.py
  - src/drama_studio/cli.py
lastReviewed: 2026-10-29
---

# Job worker

## Queue

`JobQueue` is a SQLite table in WAL mode. Jobs move
`queued → running → succeeded | failed | cancelled`. Claiming is a
`BEGIN IMMEDIATE` transaction; priority is descending, then FIFO. Failed jobs
retry until `max_attempts`, then stay failed. `recover_stale()` requeues
running jobs whose heartbeat is older than the lease.

## Worker

`Worker` is single-threaded and polls one job at a time. Handlers receive a
`JobContext` with `progress()`, `cancelled()`, `check_cancelled()` and
`sleep()`. `JobCancelled` marks the job cancelled; `PermanentJobError` skips
retries.

`serve` starts an in-process worker thread by default. `--no-worker` keeps the
API separate; `drama-studio worker` runs the same handlers in another process.
The API and worker share the SQLite queue, not Python objects.

## Job types

| Type | Work |
|---|---|
| `reference.design` | Breeze voice design or Qwen3 preset validation |
| `stem.render` | GPU stem render + CPU Whisper alignment |
| `stem.render.fake` | GPU-free test path |
| `mix.render` | timeline assembly, SFX, loudnorm, export |
| `engine.unload` | drop the live model |

## API

`POST /api/jobs`, `GET /api/jobs/{id}`, `POST /api/jobs/{id}/cancel`, and
`GET /api/jobs/{id}/events` (SSE). Project-specific helpers enqueue render,
reference and mix jobs and deduplicate active ones.

## Related

- [[concepts/gpu-serialization]]
- [[entities/drama-studio-app]]
