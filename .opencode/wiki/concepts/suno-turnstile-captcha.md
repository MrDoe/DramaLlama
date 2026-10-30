---
title: Suno Turnstile captcha — diagnosis and fix plan
tags: [suno, captcha, turnstile, bettersuno, extension, debugging]
sourceRefs:
  - src/drama_studio/engines/suno.py
  - src/drama_studio/suno.py
  - suno-bridge/server.mjs
  - third_party/bettersuno-mcp/src/suno-client.js
  - third_party/bettersuno-mcp/src/ws-bridge.js
  - /home/christoph/code/BetterSuno/background.js
lastReviewed: 2026-10-30
---

# Suno Turnstile captcha — diagnosis and fix plan

Suno stem renders fail with:

```
Suno requires a Cloudflare Turnstile check that could not be solved
automatically (Suno requires a human Turnstile check — tick the
"Verify you are human" checkbox in the Suno tab, then retry)
```

## The chain (verified in code)

1. `SunoClient` pre-calls `POST /api/c/check {ctype:"generation"}` →
   `{required:true, captcha_version:2}` (Turnstile). Confirmed in
   `data/suno-bridge.log` on every attempt.
2. `requestCaptcha(2)` (`third_party/bettersuno-mcp/src/ws-bridge.js`) sends
   `{type:"captcha_required", captcha_version:2}` to the extension and waits
   `MCP_CAPTCHA_TIMEOUT_MS` (120 s).
3. `BetterSuno/background.js` → `handleMcpCaptchaRequest`:
   - `chrome.tabs.query({url:"https://suno.com/*"})`, prefers `/create`;
     throws `No Suno tab available…` if none.
   - `chrome.scripting.executeScript({target:{tabId, frameIds:[0]}, func:
     solveCaptchaInPage, args:[FALLBACK_CAPTCHA_SITEKEY]})`.
4. `solveCaptchaInPage` (in-page, MAIN world via `wrappedJSObject`):
   1. reuse an unexpired token from an existing `cf-turnstile-response`
      widget (then `reset`s it);
   2. `turnstile.execute(id)` on an existing widget (25 s);
   3. render its own widget with a visible "tick the checkbox" hint (110 s);
   - throws `Turnstile is not loaded on the Suno tab…` when `turnstile` is
     missing.
5. **Observed error is the `!validResult` branch** (background.js ~line 431):
   `executeScript` resolved but produced **no result object** — so the widget
   was never rendered and no timeout was reached.

## What has been ruled out

| Hypothesis | Verdict |
|---|---|
| Extension not connected | Ruled out — `/api/suno/status` shows `extensionConnected: true` |
| Wrong protocol (`ln` vs `captcha_required`) | Ruled out — installed 2.5.0 and source 2.5.4 both use `captcha_required`/`captcha_token` |
| Old extension missing a fix | Ruled out — `handleMcpCaptchaRequest` and `solveCaptchaInPage` are byte-identical between installed 2.5.0 (`~/.config/mozilla/firefox/pbbnnadv.default-release/extensions/bettersuno@bettersuno.ext.xpi`) and source 2.5.4 |
| Firefox rejects `frameIds:[0]` | Ruled out — probe extension returned `[{frameId:0, result:42}]` |
| Firefox drops async `func` results | Ruled out — async and promise-returning funcs both resolve values |

The probe extension lives at `/tmp/opencode/ext-test` and is installed
temporarily in the automation browser only.

## Remaining hypotheses (ranked)

1. **The tab we inject into is not a live page.** `tabs.query` matches the URL
   even for discarded/unloaded or background tabs. Injection into a discarded
   tab resolves with `[]` — exactly the observed symptom (no throw, no result,
   no widget). Fix: activate the tab (`tabs.update({active:true})`) and, if the
   tab is discarded, `tabs.reload()` before injecting.
2. **The message reached a different browser than the one being watched.**
   `ws-bridge.js` keeps only the last authenticated socket (`extensionWs = ws`).
   If BetterSuno is installed in Chrome *and* Firefox (the repo ships a
   `chrome.crx`), the captcha request can land in a browser with no usable
   Suno tab or a logged-out one. Fix: log which client answers; enforce one
   connection.
3. **Isolated-world/sitekey mismatch.** The solver runs in the extension's
   isolated world and reaches the page through `wrappedJSObject`. If Suno's
   page is mid-navigation or the sitekey scan fails, step 3 renders with
   `FALLBACK_CAPTCHA_SITEKEY` and Cloudflare rejects silently. Fix: log the
   discovered sitekey and the widget error callback.

## Fix plan

### Phase 1 — instrumentation (fast, decisive)

- Add a WS message `captcha_debug` from the extension to the bridge carrying
  `{tabUrl, tabDiscarded, resultsLen, resultType, sitekey, widgetError}`;
  bridge logs it. One render then identifies the true cause.
- Alternative without rebuilding the extension: in the extension's
  `about:debugging` console, run `handleMcpCaptchaRequest(2)` manually and read
  the thrown error / results.

### Phase 2 — harden `handleMcpCaptchaRequest` (BetterSuno patch)

1. Wake the tab first: `chrome.tabs.update(tab.id, {active: true})`; if
   `tab.discarded`, `chrome.tabs.reload(tab.id)` and wait for `status:"complete"`.
2. Retry ladder for injection:
   `{tabId, frameIds:[0]}` → `{tabId}` → `{tabId, allFrames:true}`; accept the
   first non-empty result and log which form worked.
3. If every form returns empty, fall back to **content-script rendering**:
   `chrome.tabs.sendMessage(tab.id, {type:"renderCaptchaWidget", sitekey})`,
   because `content.js` is guaranteed to be alive in a loaded page (it already
   runs on `https://suno.com/*`). Move the visible-widget code there so the
   checkbox cannot be skipped by an injection quirk.
4. Include `results`/`tab.discarded` in the thrown error so the bridge surfaces
   something actionable instead of the generic human-check text.

### Phase 3 — bridge/UX

- `POST /captcha` (already added to `suno-bridge/server.mjs`) forces a solve on
  demand; wire a "Re-check verification" button to it in the Render error
  dialog (see [[concepts/render-error-dialog]]).
- Show the actionable part of `job.error` (last non-traceback line) in the
  dialog instead of `Traceback (most recent call last):`.
- Keep generation spacing (`MCP_GENERATION_INTERVAL_MS`, default 20 s): bursts
  are what escalate Cloudflare to interactive challenges.

### Phase 4 — verification

- Rebuild the extension (`node build.js`, install `dist/firefox`), open
  `suno.com/create` in the profile that owns the extension, call
  `POST http://127.0.0.1:9430/captcha`, then retry the Andreas render on
  `hinterkaifeck-c07cef` (stem `s1-andreas`, model `chirp-goose` = v6-mini).
- Success criteria: `/captcha` returns a token, the render reaches
  `succeeded`, and `stem.takes` contains a Suno take.

## Operational notes

- Automated browsers can never pass: `navigator.webdriver === true` is checked
  by Cloudflare. Do not try to solve Turnstile with Selenium/CDP.
- Turnstile tokens are single-use and short-lived; request one immediately
  before generating (the client already does this per call).
- The extension only solves Turnstile (version 2); hCaptcha (version 1) fails
  fast by design.

## Related

- [[entities/suno-bridge]]
- [[entities/suno-engine]]
- [[concepts/render-error-dialog]]
