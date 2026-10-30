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

English engine, `engines/breeze.py`. The **local open-weight** Breeze TTS 2
checkpoint (`~/models/Breeze-TTS-2`) is English + Chinese only; the app
exposes English.

## German support: IMPLEMENTED via breeze-de sidecar

`breeze-de` is now a registered engine (see `engines/breeze_de.py`). It calls
the Breeze-TTS-2.cpp sidecar on port 8137 (Articulatio-DE Q8_0 GGUF) and
supports voice design and clone. Sidecar must be running:
`drama-llama breeze-de` (sets `BREEZE_DE_URL=http://127.0.0.1:8137`).

## Background: the German finetune (community)

Yes — `eigenhand/Articulatio-DE-Stream-TTS-v1.5-GGUF` is a merged German
finetune of Breeze TTS 2 (Breeze weights + the Articulatio-DE adapter merged),
Q8_0/Q4_K GGUF, 24 kHz, zero-shot voice cloning + voice design via instruction.
On 105 German + 25 English held-out sentences it hits ~3.8% WER (German).
It is research/non-commercial under the BreezeBlue license (no watermark).

**Catch:** it is **not** a PyTorch checkpoint for the official inference code.
It runs on the community C++ engine `HoppouAI/Breeze-TTS-2.cpp` (ggml, Vulkan
backend, NVIDIA/AMD/Intel + CPU fallback) via `breeze-server`, which exposes
an HTTP API (`POST /v1/audio/speech`, multipart form with `text`, `instruction`,
`ref_audio`, `ref_text`, `cfg_scale`, `seed`; returns streamed s16le PCM or
WAV via `response_format`). A prebuilt Linux x64 Vulkan binary exists
(v0.1.3), and NVIDIA Vulkan (`libGLX_nvidia.so`) is present on this machine.

So integrating "German Breeze" means running `breeze-server` with the
Articulatio GGUF as a sidecar (like Chatterbox) and adding a new engine
adapter that calls its HTTP API — not reusing `BreezeEngine`. The official
hosted `breeze-tts-2-multilingual` cloud API remains a separate option.

## Related

- [[entities/chatterbox-engine]] (same external-service adapter pattern)
- [[concepts/engine-selection]]

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
