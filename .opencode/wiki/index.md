# Drama Studio wiki

Working knowledge for the local script-to-audio-drama app.

## Start here

- [[entities/drama-studio-app]] — product surface, routes, storage.
- [[concepts/script-to-stem-pipeline]] — paste → parse → stem → align.
- [[concepts/gpu-serialization]] — why Breeze, Chatterbox, Ollama and Whisper are scheduled carefully.
- [[concepts/engine-selection]] — per-stem providers and A/B takes.
- [[concepts/sfx-library-and-mix]] — local SFX index, cue model, mix chain.

## Engines and music

- [[entities/breeze-engine]] — English reference design + scene stems.
- [[entities/chatterbox-engine]] — German/English voice-sample cloning.
- [[entities/suno-bridge]] — Suno music generation through BetterSuno.
- [[entities/job-worker]] — SQLite queue and single GPU worker.

## Sources

- [[sources/kepler-stem-pipeline]] — vendored stem/aligner contract and hashes.
