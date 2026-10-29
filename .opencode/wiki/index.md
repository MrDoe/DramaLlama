# Drama Studio wiki

Working knowledge for the local script-to-audio-drama app.

## Start here

- [[entities/drama-studio-app]] — product surface, routes, storage.
- [[concepts/script-to-stem-pipeline]] — paste → parse → stem → align.
- [[concepts/gpu-serialization]] — why Breeze, Qwen3, Ollama and Whisper never share the GPU.
- [[concepts/sfx-library-and-mix]] — local SFX index, cue model, mix chain.

## Engines

- [[entities/breeze-engine]] — English reference design + scene stems.
- [[entities/qwen3-engine]] — German preset voices, no cloning.
- [[entities/job-worker]] — SQLite queue and single GPU worker.

## Sources

- [[sources/kepler-stem-pipeline]] — vendored stem/aligner contract and hashes.
