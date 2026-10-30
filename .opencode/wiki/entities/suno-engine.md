---
title: Suno engine
tags: [engine, suno, dialogue, tts, audio-drama]
sourceRefs:
  - src/drama_studio/engines/suno.py
  - src/drama_studio/suno.py
  - suno-bridge/server.mjs
lastReviewed: 2026-10-30  # was: 2026-10-30
---

# Suno engine

Suno as a dialogue TTS backend, `engines/suno.py`. It does not use the GPU;
it calls the local BetterSuno bridge (`SunoBridge`).

## Voice workflow

- Generates a clip whose lyrics are the stem text, style tags
  `"audio drama radio play"`, `style_weight=20` (20% style influence), mv one of
  `chirp-hawk` (v6), `chirp-hawk-wild` (v6-wild), `chirp-goose` (v6-mini).
- Optionally extracts the vocal stem via `make_stems` and downloads it as m4a
  ("without credits"), converting to WAV for the aligner/mixer.
- Suno voices (personas): browse via `GET /api/suno/personas` (own +
  followed, slimmed to id/name/model/image/root clip), preview a voice via
  `GET /api/suno/personas/{id}`, select via `PATCH .../characters/{speaker}`
  (`suno_persona_id` + `suno_persona_model`, `""` clears to preset voice).
  The Cast panel has a voice picker with preview audio. `design_reference()`
  is unsupported.
- `bettersuno-mcp` is a pinned git subproject (`third_party/bettersuno-mcp`);
  the bridge resolves it via `suno-bridge/node_modules` and falls back to
  `~/code/bettersuno-mcp`. Only one process may own `ws://127.0.0.1:9423` —
  `serve` auto-starts the bridge (`--no-suno-bridge` opts out).
- Suno is independent of Breeze: no reference clip is needed, a stem with the
  Suno engine counts as voice-ready, and `Project.default_engine = "suno"`
  applies to every un-overridden stem. The Cast panel shows "Suno voice" and a
  preset/persona badge instead of the Breeze "Design voice" button.
- `preview_url` on the take stores the original clip audio for playback before
  stem extraction; the take file is the vocals after extraction.

## Related

- [[concepts/engine-selection]]
- [[concepts/script-to-stem-pipeline]]
