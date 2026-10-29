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

Code-complete; GPU smoke test pending behind a sibling render. Qwen3 peak VRAM
is about 4.3 GB, so it can coexist with the local LLM but not with Breeze.

## Related

- [[concepts/script-to-stem-pipeline]]
- [[concepts/gpu-serialization]]
- [[concepts/sfx-library-and-mix]]
