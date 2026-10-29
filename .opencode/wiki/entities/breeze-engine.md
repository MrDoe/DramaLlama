---
title: Breeze engine
tags: [engine, breeze, english, voice-clone]
sourceRefs:
  - src/drama_studio/engines/breeze.py
  - vendor/breeze_stems/breeze_drama.py
  - vendor/breeze_stems/breeze_stems.py
lastReviewed: 2026-10-29
---

# Breeze engine

English engine, `engines/breeze.py`. Breeze TTS 2 is English + Chinese only;
the app exposes English.

## Voice workflow

1. `design_reference()` runs Breeze voice design at cfg 4 with a 6–11 s
   monologue. The transcript must match the audio word for word.
2. `render_stem()` sends `ref_audio_path` + `ref_text` and **no speaker field**.
   A speaker prefix (`[S0]`) overrides the reference timbre.
3. Default cfg 2.5 for line/stem direction. N takes are scored by MFCC cosine
   (prefer ≥ 0.98) and median F0 distance to the reference.
4. `align_stem()` delegates to the shared CPU Whisper aligner; it does not load
   Breeze.

## Gotchas

- The reference carries emotion, not just timbre; design in the character's
  baseline mood.
- Short references drift. Use 6–11 s.
- `fast_all` needs ~14.4 GB and a long CUDA-graph warmup; this app uses eager.
- `load_runtime` expects a `Path`, not a string.
- Weights/outputs are research / non-commercial.

## Related

- [[concepts/script-to-stem-pipeline]]
- [[concepts/gpu-serialization]]
- [[sources/kepler-stem-pipeline]]
