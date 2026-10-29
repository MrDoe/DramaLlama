---
title: Qwen3 engine
tags: [engine, qwen3, german, presets]
sourceRefs:
  - src/drama_studio/engines/qwen3.py
  - src/drama_studio/api.py
lastReviewed: 2026-10-29
---

# Qwen3 engine

German engine, `engines/qwen3.py`. Qwen3-TTS 12 Hz 1.7B CustomVoice.

## Voice workflow

- No reference cloning. Each character stores a `qwen_speaker` preset:
  `Vivian`, `Serena`, `Ryan`, `Aiden`.
- `render_stem()` calls `generate_custom_voice(text, language="German",
  speaker=preset, instruct=instruction)` with sampling.
- The same Whisper aligner cuts the stem; `align_cuts` now accepts
  `language="de"` and folds umlauts (`ä→ae`, `ö→oe`, `ü→ue`, `ß→ss`).
- The model output is 24 kHz mono, like Breeze.

The UI cast panel shows a preset dropdown for German projects instead of the
Breeze reference designer.

## Status

Validated on one German scene (ANNA BERG, scene 1, preset Vivian):

- load 10.0 s
- render 54.4 s for 32.0 s audio
- German Whisper alignment 39.0 s
- 7/7 line cuts at confidence 1.0
- peak VRAM 4.92 GB

The full worker path was also validated end-to-end: a 24.9 s ANNA stem, 7/7
line cuts at confidence 1.0, and a 7-line mix (46.8 s) with the expected
missing-clip warnings for lines whose stems had not been rendered.

Qwen3 can coexist with the local LLM on a 16 GB card, but not with Breeze;
`EngineManager` unloads the other engine before switching.

## Related

- [[concepts/script-to-stem-pipeline]]
- [[concepts/gpu-serialization]]
- [[concepts/sfx-library-and-mix]]
