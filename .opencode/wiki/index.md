# Drama Llama wiki

Working knowledge for Drama Llama, the local script-to-audio-drama app.

## Start here

- [[entities/drama-llama-app]] — product surface, routes, storage, rename notes.
- [[entities/drama-llama-mascot]] — llogo.png avatar assets, palette, rename boundaries.
- [[concepts/script-to-stem-pipeline]] — paste → parse → stem → align.
- [[concepts/cut-board-multitrack]] — shared Cut timeline, script-order clips, WebAudio transport.
- [[concepts/take-aware-cuts]] — per-clip take selection, render append/dedupe, remap rules.
- [[concepts/gpu-serialization]] — why Breeze, Chatterbox, Ollama and Whisper are scheduled carefully.
- [[concepts/engine-selection]] — project default only, no per-stem override.
- [[entities/cuts-module]] — shared `cuts.py` domain (freeze/remap/validate).
- [[concepts/sfx-library-and-mix]] — local SFX index, cue model, mix chain.
- [[concepts/suno-turnstile-captcha]] — why Suno renders stall on Cloudflare Turnstile, and the fix plan.
- [[concepts/batch-render-and-captcha-recovery]] — sequential "render all", pause-on-captcha, recovery dialog.
- [[concepts/suno-account-switch]] — stale identity cache after a Suno login change, and the fix.
- [[concepts/gpu-optional-install]] — CPU-only installs, CUDA wheel channels, opt-in engines, size envelope.

## Engines and music

- [[entities/breeze-engine]] — English reference design + scene stems.
- [[entities/chatterbox-engine]] — German/English voice-sample cloning.
- [[entities/suno-engine]] — Suno dialogue TTS (project-level engine, audio-drama style, optional personas).
- [[entities/suno-bridge]] — Suno music generation through BetterSuno.
- [[entities/job-worker]] — SQLite queue and single GPU worker.
- [[entities/start-ui-launcher]] — one-command build + serve, dev mode, health preflight.

## Distribution

- [[entities/installer]] — PySide6 wizard + stdlib core, AppImage/Inno packaging, headless CLI.

## Sources

- [[sources/kepler-stem-pipeline]] — vendored stem/aligner contract and hashes.
