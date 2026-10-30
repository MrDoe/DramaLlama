---
title: Batch render and captcha recovery — implementation plan
tags: [suno, render, batch, captcha, ux, plan]
sourceRefs:
  - web/src/pages/ProjectPage.tsx
  - web/src/api.ts
  - src/drama_studio/api.py
  - src/drama_studio/worker.py
  - src/drama_studio/jobs.py
  - suno-bridge/server.mjs
lastReviewed: 2026-10-30
---

# Batch render and captcha recovery — implementation plan

Goal: turn "one human tick per stem" into **at most one tick per batch**, and
make the tick land in the right place when Cloudflare insists. See
[[concepts/suno-turnstile-captcha]] for the failure analysis.

## Constraints discovered in code

- The worker is **single-threaded** (`worker.run_once` → one job at a time) and
  the queue is `ORDER BY priority DESC, created_at ASC`, so N enqueued
  `stem.render` jobs already run **sequentially, in order**. No new queue
  machinery is needed.
- `POST /projects/{id}/stems/{stem}/render` refuses a second job for the *same
  stem* (`active_job`) but happily queues different stems.
- The frontend tracks jobs per stem key (`jobs[stem.id]`) via `trackJob`, which
  opens an SSE stream per job and refreshes the project on success.
- `suno-bridge` now exposes `POST /captcha` (forces the extension to mint a
  token, 120 s timeout) — added during the investigation.
- Suno generations are already spaced by `MCP_GENERATION_INTERVAL_MS` (20 s) in
  `suno-client.js`; bursts are what escalate Cloudflare.

## Design decision: client-side orchestration, not a server batch job

A server-side `stem.render.batch` handler would block the queue for the whole
batch, complicate cancellation, and hide per-stem progress. Instead the client
drives a **sequential loop** and keeps every stem as its own job:

```
for each candidate stem (in scene/speaker order):
    enqueue render -> await terminal status via SSE
    succeeded  -> continue
    failed with captcha signature -> pause batch, open recovery dialog
    failed otherwise -> record, continue (configurable)
```

Rationale: each generation still gets its own fresh Turnstile token (required —
tokens are single-use), the human tick happens once per batch because the
extension reuses the resulting widget token / `execute()` afterwards, and a
failure stops the loop instead of burning N doomed attempts.

## Phase A — backend (small)

1. **Proxy the forced solve** (so the UI never talks to the bridge directly):
   `POST /api/suno/captcha` → `SunoBridge.request_captcha()` →
   `POST {SUNO_BRIDGE_URL}/captcha`; return `{ok, error?}` (never the token).
   Add `request_captcha()` to `src/drama_studio/suno.py`.
2. **Classify failures**: add a helper `is_captcha_error(message)` in
   `src/drama_studio/engines/suno.py` (matches `Turnstile`, `hCaptcha`,
   `could not be solved`, `Verify you are human`) so the UI does not string
   match in TypeScript.
   - Either include `captcha_required: true` in the failed job result, or
     expose the helper via a constant list the frontend mirrors. Prefer the
     job result flag: `worker` stores `error` only, so add a lightweight
     `PermanentJobError` subclass `CaptchaRequiredError` whose message is
     preserved; the frontend still matches on a short stable prefix
     (`CAPTCHA_REQUIRED:`).
3. Tests: `test_suno_captcha.py` — proxy returns ok/502, classifier matches the
   real message, non-captcha failures unaffected.

## Phase B — frontend orchestrator

New hook `useBatchRender` (co-located in `ProjectPage.tsx` or a small
`web/src/hooks/useBatchRender.ts`):

State: `idle | running | paused_captcha | done | cancelled`,
`current: stemId`, `completed: string[]`, `failed: {stemId, error}[]`.

Rules:
- **Candidates**: stems whose resolved engine is available, that are not busy,
  and (default) have no takes. "Re-render all" modifier includes rendered stems.
- **Sequential**: `await api.renderStem(...)` then wait for terminal state via a
  promise wrapper around `api.watchJob` (do not reuse `trackJob`, which is
  fire-and-forget; instead extend `trackJob` to return a completion promise).
- **Stop on captcha**: pause, keep the queue empty (nothing else enqueued), open
  the recovery dialog, and offer *Retry this stem* / *Skip* / *Cancel batch*.
- **Cancel**: stop after the in-flight stem finishes (jobs cannot be
  interrupted mid-generation safely); mark remaining as skipped.
- **Non-captcha failure**: record and continue; surface a summary at the end.
- UI: `Render all` button in the Render-queue toolbar with `n/total` progress,
  `Cancel`, and a per-stem "queued/rendering/skipped" hint derived from the
  existing job badges.

Tests (vitest not present — keep logic pure and testable in TS via `npm run
build` type-checking plus a manual browser pass; if a test runner is added
later, this hook is the unit).

## Phase C — error dialog + recovery UX

1. Replace the card's `Traceback (most recent call last):` first-line with the
   **last non-traceback line** (the actionable message) plus a `Details` button.
2. `Details` opens the modal already styled (`error-dialog*` in `styles.css`)
   showing the full `job.error` in a scrollable monospace block.
3. For captcha errors the dialog shows a **Re-check verification** button →
   `POST /api/suno/captcha` → on ok, "Verification refreshed — retry" and the
   batch resumes automatically at the paused stem.
4. Copy: name the exact fix ("Open suno.com/create in the browser with
   BetterSuno, tick 'Verify you are human', then Re-check").

## Phase D — BetterSuno hardening (separate repo, `~/code/BetterSuno`)

Fixes the "no widget ever appears" branch (empty `executeScript` result):

1. `chrome.tabs.update(tab.id, {active:true})`; if `tab.discarded`, reload and
   wait for `status === "complete"` before injecting.
2. Injection retry ladder: `{tabId, frameIds:[0]}` → `{tabId}` →
   `{tabId, allFrames:true}`; log which form produced a result.
3. Fallback that cannot be skipped: `chrome.tabs.sendMessage(tab.id,
   {type:"renderCaptchaWidget", sitekey})` handled in `content.js` (guaranteed
   alive on a loaded suno.com page), rendering the same visible widget.
4. Emit `captcha_debug` over the WS bridge (`tabUrl`, `discarded`, `resultsLen`,
   `sitekey`, widget error) and log it in `suno-bridge`; surface the real cause
   in the thrown error instead of the generic human-check text.
5. Rebuild with `node build.js`, install `dist/firefox`, bump version.

## Phase E — verification

1. **Batch sequencing without captcha**: set a scratch project's stems to
   `fake`, run `Render all`, assert 3 jobs run one after another and each stem
   gets takes (browser-driven, per AGENTS.md UI rules; delete the project).
2. **Captcha pause**: point one stem at Suno with the bridge extension
   disconnected (`extensionConnected:false` path) → batch pauses, dialog shows
   the actionable error, `Re-check` surfaces the 503 message.
3. **Real run**: `hinterkaifeck-c07cef`, Andreas on `chirp-goose` (v6-mini),
   tick once if asked, confirm `succeeded` + take present; then `Render all`
   and count how many generations pass without another tick (target: all).
4. Regression: `pytest` (142+) and `npm run build`.

## Risks / rollback

| Risk | Mitigation |
|---|---|
| Batch burns credits/allowance | Sequential + stop-on-captcha + explicit count in the button label |
| Client closes mid-batch | Jobs already enqueued keep running; batch state is client-only, so reopening shows per-stem status (acceptable) |
| Turnstile token reuse across generations | Each generation requests a fresh token; reuse is only within the extension's own widget lifecycle |
| Extension patch breaks normal generation | Version bump + keep the old xpi; changes are additive with fallbacks |

## Out of scope

- Solving Turnstile programmatically (impossible by design; `navigator.webdriver`
  is checked).
- Changing Suno's rate limits or using unofficial APIs.

## Related

- [[concepts/suno-turnstile-captcha]]
- [[entities/suno-engine]]
- [[entities/suno-bridge]]
- [[concepts/engine-selection]]
