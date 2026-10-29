---
title: GPU serialization
tags: [gpu, vram, breeze, chatterbox, whisper, ollama]
sourceRefs:
  - src/drama_studio/handlers.py
  - src/drama_studio/worker.py
  - src/drama_studio/aligner.py
  - scripts/smoke_breeze.py
lastReviewed: 2026-10-29
---

# GPU serialization

There is one 16 GB RTX 4090 Laptop GPU. Everything must be scheduled around it.

| Workload | Approx. VRAM |
|---|---:|
| Breeze TTS 2 eager | 8.5 GB peak |
| Chatterbox Multilingual 0.5B (external server) | ~2–4 GB |
| Ollama `gemma4:12b` | ~7.6 GB |
| faster-whisper large-v3 | CPU int8 (no GPU) |

Breeze + Ollama does not fit. Breeze + Whisper on GPU does not fit. The worker
is single-threaded and the API never imports torch; LLM calls happen before a
render and Ollama is invoked with `keep_alive: 0`. Chatterbox and Suno live in
separate server processes, so `EngineManager` only swaps local Breeze models.

## Preflight

Always check `nvidia-smi` before a model load. Other local sessions and a
persistent `python main.py` can hold ~4.3 GB. The app can still load Breeze
with that baseline, but not alongside another 5–8 GB process.

## Measured M0b run

One MARA scene-1 stem, 28.0 s audio:

- Breeze load: 10.1 s
- stem render: 103.5 s (~3.7× real time)
- Whisper CPU align: 60.8 s
- line cuts: 6/6 at confidence 1.0
- peak VRAM: 8.47 GB

## Related

- [[entities/job-worker]]
- [[entities/breeze-engine]]
- [[entities/chatterbox-engine]]
