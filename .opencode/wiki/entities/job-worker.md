---
title: Job worker
tags: [jobs, sqlite, worker, api, sse]
sourceRefs:
  - src/drama_studio/jobs.py
  - src/drama_studio/worker.py
  - src/drama_studio/api.py
  - src/drama_studio/cli.py
lastReviewed: 2026-11-13
---

# Job worker

## Queue

`JobQueue` is a SQLite table in WAL mode. Jobs move
`queued → running → succeeded | failed | cancelled`. Claiming is a
`BEGIN IMMEDIATE` transaction; priority is descending, then FIFO. Failed jobs
retry until `max_attempts`, then stay failed. `recover_stale()` requeues
running jobs whose heartbeat is older than the lease.

Recovery runs **periodically** (`recovery_interval_s`, default 60 s), not once at
worker startup. A job orphaned by a crash or restart is seconds old when the
worker begins, so a single startup pass skips it and the queue — serialised on
one GPU — stays wedged behind a `running` job nobody is executing until the next
restart, which nothing in the browser can trigger. That is only safe because the
worker also refreshes the lease itself on a timer while a handler runs
(`Worker._keep_lease`): `ctx.progress()` already beats, but a single engine call
longer than the stale timeout does not, and a long Breeze take is exactly that.
Without the keep-alive, periodic recovery would steal healthy renders. The suite
pins both directions — an orphan is reclaimed without a restart, and a silent but
live job is not. See [[concepts/project-json-concurrency]] and
[[concepts/gpu-serialization]].

## Worker

`Worker` is single-threaded and polls one job at a time. Handlers receive a
`JobContext` with `progress()`, `cancelled()`, `check_cancelled()` and
`sleep()`. `JobCancelled` marks the job cancelled; `PermanentJobError` skips
retries.

`serve` starts an in-process worker thread by default. `--no-worker` keeps the
API separate; `drama-llama worker` runs the same handlers in another process.
The API and worker share the SQLite queue, not Python objects.

## Job types

| Type | Work |
|---|---|
| `reference.design` | Breeze voice design |
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
- [[entities/drama-llama-app]]
