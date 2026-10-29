# Drama Studio — agent notes

## What this is

Local single-user web app: paste a script → parse scenes/lines and attribute
speakers → group into per-character, per-scene stems → render with Breeze
(English) or Chatterbox (German) → align/cut line clips with Whisper → search/place
SFX → mix and export.

## Current state (M0–M5)

- Deterministic parser + optional Ollama LLM parse with verbatim validation.
- Stem builder, reference design, render + align worker, take/alignment storage.
- SFX index over `~/Sounds`, cue sheet, mixer with ducking/EQ/loudnorm.
- React UI: projects, script/parse, stems, render, sound, mix.
- 60 backend tests, web build green.
- English Breeze path validated on one real scene; German Chatterbox path
  validated end-to-end (20.7 s ANNA stem, 7/7 cuts). Suno music generation and
  WAV download validated through the BetterSuno bridge.
- Per-stem engine selection (Breeze / Chatterbox / Fake) with engine-labelled
  takes for A/B comparison.

## Hard constraints

- **Breeze is English + Chinese only.** German uses Chatterbox Multilingual
  0.5B, never Breeze. Qwen3-TTS was removed from Drama Studio; do not
  reintroduce it.
- **One GPU job at a time.** Breeze eager peaks ~8.5 GB; the local LLM ~7.6 GB;
  Whisper + Breeze exceeds 16 GB. Align on CPU int8. The app serializes jobs,
  but other sessions can still hold VRAM — check `nvidia-smi` before a load.
- **Do not fork `align_cuts`.** It is owned by the sibling `~/tts_test`
  session (Kepler). Vendor the stable module under `vendor/breeze_stems/`,
  pin hashes in `VENDORED_FROM.md`, and send fixes upstream.
- **LLM parse must preserve dialogue verbatim.** The validator rejects
  paraphrase (<85% substring coverage) and the API falls back to deterministic.
- **Breeze reference clips:** never send a `speaker` field with a reference;
  it prepends `[S0]` and overrides the reference timbre.
- **Chatterbox needs a running server** at `CHATTERBOX_URL` (default
  `http://127.0.0.1:4123`). It clones an uploaded voice sample; without one it
  falls back to the server's default voice. It has no voice-design mode.
- **Suno needs the BetterSuno extension** and a live `suno.com` tab. The
  `suno-bridge/` sidecar owns `ws://127.0.0.1:9423`; only one process can own
  it. Download music as WAV (the M4A path is not reliably decodable).

## Layout conventions

- Python package under `src/drama_studio/`; tests under `tests/`.
- App-specific engine code in `engines/`; `vendor/` and `suno-bridge/node_modules/`
  stay read-only.
- `suno-bridge/` is a Node HTTP sidecar that imports the installed
  `bettersuno-mcp` modules. It must be started separately from `serve`.
- Runtime data under `data/` (gitignored). Projects are file-based:
  `data/projects/<slug>/project.json`, `refs/`, `audio/takes/`, `lines/`, `mix/`.
- SQLite only for the job queue, never for project content.
- GPU work runs in `handlers.py`; the API process must not import torch.

## Commands

```bash
~/.venvs/qwen3tts/bin/python -m pytest
cd web && npm run build

~/.venvs/qwen3tts/bin/drama-studio init
~/.venvs/qwen3tts/bin/drama-studio serve              # API + in-process worker
~/.venvs/qwen3tts/bin/drama-studio serve --no-worker  # separate worker
~/.venvs/qwen3tts/bin/drama-studio worker --once
~/.venvs/qwen3tts/bin/drama-studio suno-bridge
```

The Suno sidecar needs `bettersuno-mcp` installed under
`suno-bridge/node_modules` and the BetterSuno browser extension connected.

## Gotchas

- First SFX scan of `~/Sounds` (322 files, 14 GB) takes ~26 s; later scans use
  `data/sfx_index.json` and are fast. Personal folders are excluded by default.
- Breeze stem render is ~3.7× real time on the 4090 laptop; a 28 s stem took
  103.5 s. Whisper large-v3 CPU alignment took 60.8 s.
- The 16 GB GPU often has a persistent ~4.3 GB `python main.py` process; Breeze
  still fits but the local LLM may not run concurrently.
- `align_cuts` returns `(cuts, confidences)` and takes `language="en"|"de"`.
- Job payloads/result JSON use relative project paths; resolve against
  `ProjectStore.project_dir(project_id)`.
- Engine takes are merged across providers. A re-render replaces only takes
  from the same engine and leaves the others visible for A/B comparison.
- Suno `download_song` can fail on M4A even when a progressive media URL is
  listed; request `format: "wav"` and let Suno convert. The bridge's `/download`
  endpoint defaults to the caller's format, so Drama Studio always asks for WAV.
- Chatterbox is an external GPU service; `EngineManager` does not unload it.
  Its server owns the GPU memory, not the Drama Studio worker process.
