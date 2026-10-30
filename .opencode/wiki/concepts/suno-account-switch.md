---
title: Suno account switch breaks downloads
tags: [suno, bettersuno, bridge, auth, gotcha]
sourceRefs:
  - third_party/bettersuno-mcp/src/auth.js
  - third_party/bettersuno-mcp/src/ws-bridge.js
  - src/drama_studio/engines/suno.py
lastReviewed: 2026-10-30
---

# Suno account switch breaks downloads

If the Suno login changes (log out/in as a different user) while the bridge
is running, **every** download fails with:

> `Access denied: song <id> is not in your library (owned by @<your own handle>).`

even for clips minted seconds ago under the current login. Generations keep
working — only the ownership gate rejects.

## Why

`ws-bridge.js` accepts a fresh token on every extension reconnect, but
`auth.js` cached the `/api/user/me` identity forever. After a switch the
cache holds the *old* account while new clips belong to the *new* one, so
`assertOwned`/`assertCanCover`/`search_songs` compare against the wrong user.

## Fix (2026-10-30)

- The cached identity is tied to the token: a changed token refetches.
- `assertOwned`/`assertCanCover` refresh once before denying (covers a
  rotation landing mid-call). Genuinely foreign clips are still denied.
- `SunoEngine.render_stem` now includes the last per-clip errors in its
  final `RuntimeError` instead of the bare
  "Suno clips finished but no audio could be downloaded".

## Recovery

Restart the sidecar (`node suno-bridge/server.mjs`) to drop the stale cache,
then retry the failed renders — completed clips owned by the current account
download without regenerating.

## Second failure mode (same symptom, 2026-10-30)

`Suno clips finished but no audio could be downloaded` with
`download failed: Suno download not available` and a ~40 s job means
`wait_for_clip` returned while the clip was still `streaming`: the old
`_clip_ready` treated *any* `media_urls` entry as ready, but streaming clips
only carry the encrypted webm relay asset — the downloadable progressive mix
doesn't exist yet. Fixed in `src/drama_studio/suno.py` to require `complete`
status, a non-forbidden `audio_url`, or a progressive audio entry. Python-side
fixes go live in each serve/worker on restart.

## Related

- [[entities/suno-bridge]]
- [[entities/suno-engine]]
