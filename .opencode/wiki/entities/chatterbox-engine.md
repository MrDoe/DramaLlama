---
title: Chatterbox engine
tags: [engine, chatterbox, german, voice-clone]
sourceRefs:
  - src/drama_studio/engines/chatterbox.py
  - src/drama_studio/config.py
lastReviewed: 2026-11-21
---

# Chatterbox engine

German/English engine, `engines/chatterbox.py`. It talks to a separate
Chatterbox Multilingual 0.5B HTTP server at `CHATTERBOX_URL`
(default `http://127.0.0.1:4123`); the server owns the model and GPU memory.

## Container lifecycle

Drama Llama owns the Docker container rather than leaving it running idle.
`ChatterboxEngine.load()` runs `docker start` on
`DRAMA_LLAMA_CHATTERBOX_CONTAINER` (default `chatterbox-tts-api`, with a legacy
`DRAMA_STUDIO_*` fallback) and polls `/health` for up to 180 s. `unload()` and
the `finally` in `render_stem()` run `docker stop`, so the server's ~4.4 GB of
VRAM is released when each render finishes; the next render restarts it. An
empty container name disables Docker management and only health-checks an
external server, which keeps tests hermetic. See [[concepts/gpu-serialization]].

## Voice workflow

- Upload a voice sample per character (`POST .../voice-sample`); the adapter
  sends it as `voice_file` to `/v1/audio/speech/upload`.
- Without a sample, the server uses its configured default voice.
- `exaggeration` is derived from the stem cfg scale (cfg 2.5 → 0.5), with
  `cfg_weight=0.5` and `temperature=0.8`.
- Output is 24 kHz mono WAV; the same Whisper aligner cuts line clips.
- No voice-design mode: `design_reference()` raises `UnsupportedOperation`.

## Validation

A 4.38 s German test line rendered in 3.4 s; the worker path produced a 20.7 s
ANNA stem and 7/7 line cuts. Qwen3-TTS is **not** part of Drama Llama.

## Related

- [[concepts/script-to-stem-pipeline]]
- [[concepts/engine-selection]]
- [[concepts/gpu-serialization]]
