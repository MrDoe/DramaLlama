---
title: project.json and long jobs
tags: [concurrency, jobs, store, worker, sfx, gotcha]
sourceRefs:
  - src/drama_studio/store.py
  - src/drama_studio/handlers.py
  - src/drama_studio/worker.py
  - src/drama_studio/jobs.py
  - src/drama_studio/sfx.py
  - tests/test_worker_recovery.py
  - tests/test_render_voices.py
lastReviewed: 2026-11-13
---

Project content lives in one JSON file per project — `ProjectStore.save()`
serialises the whole [[entities/drama-llama-app]] `Project` and writes it
atomically. SQLite holds only the job queue. That is the right shape for a local
single-user app, and it has one sharp edge: **the last writer wins, and a job is
a long-running writer.**

## Lost updates

Handlers load the project, work for anything up to several minutes on the GPU,
then save the copy they loaded. Anything the user changed through the API in the
meantime is reverted. Observed in a live browser session: four voice directions
typed into the Voices panel, three of them gone afterwards, with the panel having
correctly reported "saved".

The rule is therefore: a job saves **what it owns**, onto a fresh read.

- `_save_character(project_id, speaker, mutate)` in `handlers.py` re-reads and
  replays one character. Used by both voice jobs.
- `_save_job_result(project, stem_id=…, alignment_key=…)` re-reads and replays
  the rendered stem, plus the alignment the job actually produced. Used by
  `render_stem`.

Re-rendering the same stem still wins over an older render of that stem, which is
the intent; it just no longer reverts a cue or a description the user touched
while the GPU was busy.

## Two processes, one index

`SfxLibrary` is per-process. The API and the worker each hold one, so a file the
worker adds to an indexed root — every downloaded Suno track — is invisible to
the API until its copy is re-read. Symptom: `POST /cues` answering *"sfx not
found"* about the track the worker had just created, and the panel's own preview
404ing for the same reason.

`SfxLibrary.get()` now rescans once on a miss, rate limited to one re-read per
two seconds so cues pointing at deleted files cannot each trigger a folder walk.
The "Rescan" button in the Sound step remains the manual escape hatch.

## Leases

`jobs.py` treats a running job as abandoned when its heartbeat is older than
`recover_stale_after_s` (300 s). Two things follow.

`ctx.progress()` writes `heartbeat_at`, so a job that reports progress holds its
lease. A single engine call longer than the timeout does not — so the worker
refreshes the lease itself on a timer while a handler runs
(`Worker._keep_lease`). Without that, any periodic recovery would steal healthy
renders and run them twice.

Recovery used to run **once, at worker startup**. A job orphaned by a crash or
restart is seconds old at that moment, so it is skipped, and then holds the queue
— which serialises GPU work — until the *next* restart. Recovery now repeats on
`recovery_interval_s`. The test suite pins both directions: an orphan is
reclaimed without a restart, and a silent-but-alive job is not.

## Related

- [[entities/job-worker]] — queue, claims, retries.
- [[concepts/gpu-serialization]] — why jobs run one at a time in the first place.
- [[concepts/spatial-stage-rendering]] — the mix path that reads the same file.
