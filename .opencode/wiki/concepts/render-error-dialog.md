---
title: Render error dialog — actionable failures and captcha recovery
tags: [ui, suno, captcha, render, error-handling]
sourceRefs:
  - web/src/pages/ProjectPage.tsx
  - src/drama_studio/engines/suno.py
  - src/drama_studio/handlers.py
  - web/src/api.ts
lastReviewed: 2026-11-03
---

# Render error dialog

The Render tab surfaces job failures through a modal `ErrorDialog` instead of a
raw traceback, and pairs a Suno captcha failure with a "Re-check verification"
button that resumes a paused batch.

## Shape

- `ErrorDialog` (`web/src/pages/ProjectPage.tsx`) takes `title`, `body`, `onClose`,
  optional `onRecheck` and `rechecking`.
- The body is the last non-traceback line of `job.error` (extracted by
  `lastErrorLine`), so a Python traceback collapses to its actionable message.
- A `captcha` flag is computed from the body (`/CAPTCHA_REQUIRED|turnstile|verify you are human/i`).
  When set, the dialog shows a hint pointing at `suno.com/create` and a
  "Re-check verification" button.
- The backdrop closes on click; the inner dialog stops propagation.

## Captcha path

1. `SunoEngine.render_stem` raises `CaptchaRequiredError` (message prefixed
   `CAPTCHA_REQUIRED:`) when the BetterSuno bridge reports a Turnstile
   failure.
2. `handlers.render_stem` converts it to `PermanentJobError` so the queue never
   retries a captcha failure.
3. The frontend's `renderAll` pauses the batch and opens the dialog with
   `title = "Verification needed · <speaker>"`.
4. "Re-check verification" calls `POST /api/suno/captcha` (bridge `POST /captcha`),
   which forces the extension to mint a token; on success the batch resumes
   (`void renderAll(project.stems, false)`).

## Details modal

A separate Details modal shows the full `job.error` for debugging; the dialog
body itself stays short.

## Related

- [[concepts/suno-turnstile-captcha]]
- [[concepts/batch-render-and-captcha-recovery]]
- [[entities/suno-bridge]]
- [[entities/suno-engine]]
