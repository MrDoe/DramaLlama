---
title: Suno music bridge
tags: [suno, music, bettersuno, bridge]
sourceRefs:
  - suno-bridge/server.mjs
  - src/drama_studio/suno.py
  - src/drama_studio/handlers.py
lastReviewed: 2026-10-30
---

# Suno music bridge

Drama Llama generates music beds, themes and songs through the
[`bettersuno-mcp`](https://github.com/MrDoe/bettersuno-mcp) modules. Because
those modules are Node and need the BetterSuno browser extension, a small
sidecar wraps them in HTTP.

```
Drama Llama (Python)
  → http://127.0.0.1:9430
  → suno-bridge/server.mjs
  → bettersuno-mcp src/{ws-bridge,suno-client,tools}
  → ws://127.0.0.1:9423 ← BetterSuno extension (token + Turnstile)
  → Suno API
```

## Endpoints

| Path | Purpose |
|---|---|
| `GET /health` | bridge + extension connection status |
| `POST /generate` | `inspire_song`/`create_song`; returns clip IDs |
| `GET /clip/{id}` | poll generation status |
| `POST /download` | download WAV into a project/SFX folder |

Drama Llama enqueues `suno.generate`, polls the clip, downloads **WAV** (the
M4A path can return an undecodable stream), indexes it in `SfxLibrary`, and
appends a `SunoTrack` to the project. The Sound UI previews it and can add it
as a `music` cue.

## Constraints

- Only one process can own `ws://127.0.0.1:9423`; stop other
  `bettersuno-mcp` instances.
- The extension and an open `suno.com` tab are required.
- Generation is serialised and captcha-gated; failures usually mean the
  extension is disconnected or Turnstile needs a human click.
- After a Suno account switch (logout/login), downloads fail until the
  bridge is restarted — see [[concepts/suno-account-switch]].

## Related

- [[concepts/sfx-library-and-mix]]
- [[entities/drama-llama-app]]
