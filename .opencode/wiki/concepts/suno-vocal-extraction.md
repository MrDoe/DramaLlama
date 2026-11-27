---
title: Suno vocal extraction
tags: [suno, demucs, vocals, isolation, stems, alignment]
sourceRefs:
  - src/drama_studio/handlers.py
  - src/drama_studio/api.py
  - tests/test_render_voices.py
  - web/src/pages/ProjectPage.tsx
  - web/src/api.ts
lastReviewed: 2026-11-21
---

# Suno vocal extraction

Suno dialogue arrives as a full mix, which Whisper aligns against badly
(confidence 0.08–0.52). The project therefore supports an explicit vocal
extraction step and a new automated follow-up.

## The job

`POST /api/projects/{id}/stems/{stem_id}/extract` enqueues `stem.extract` with
`method` either `demucs` (CPU, `htdemucs --two-stems vocals`) or `suno`
(`POST /stems` through the bridge, **Pro/Premier plans only** — free accounts
get 403). The handler writes a vocals-only `Take` with
`vocal_isolated=True`, optionally re-aligns it with Whisper, adds the
instrumental to the SFX library, and applies `_freeze_legacy_cuts` so a mixed
cut sheet keeps its existing take choices. See [[concepts/take-aware-cuts]].

If no isolated vocal could be produced, the render path uses
`_estimate_alignment()` — word-count-proportional slices with confidence 0.05
and `aligner="estimate"` — instead of trusting Whisper on the full song.

## Automatic follow-up

`RenderBody.auto_extract_vocals` (Stems page: "extract vocals after render",
honoured by single renders, "Generate all" and the captcha resume) travels in
the render job payload. `render_stem` queues `stem.extract` only after the
render succeeded, with the `take_id` it selected, and returns it as
`extract_job_id` so the UI can follow it. A failed or cancelled render queues
nothing.

`POST /api/projects/{id}/stems/extract-all` ("Extract all vocals") covers each
stem's current Suno take (`suno_take_needing_extraction()` in `cuts.py`) and
skips stems whose take already has a vocal, so it never switches to another
generation. Both extraction endpoints use `active_job()` to avoid duplicate jobs
per stem; the render-chained job names a newly created take and skips that check.

`extract_stem` reloads the project before writing, so edits made while Demucs
or Whisper run are kept.

## Related

- [[entities/suno-engine]]
- [[entities/suno-bridge]]
- [[concepts/take-aware-cuts]]
- [[concepts/script-to-stem-pipeline]]
- [[concepts/suno-turnstile-captcha]]
