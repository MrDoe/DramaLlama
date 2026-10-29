# Drama Studio — agent notes

## What this is

Local single-user web app: paste a script → parse scenes/lines and attribute
speakers → group into per-character, per-scene stems → render with Breeze
(English) or Qwen3 (German) → align/cut line clips with Whisper → search/place
SFX → mix and export.

## Current state (M0–M5)

- Deterministic parser + optional Ollama LLM parse with verbatim validation.
- Stem builder, reference design, render + align worker, take/alignment storage.
- SFX index over `~/Sounds`, cue sheet, mixer with ducking/EQ/loudnorm.
- React UI: projects, script/parse, stems, render, sound, mix.
- 55 backend tests, web build green.
- English Breeze path validated on one real scene; German Qwen3 path code-complete
  but not yet GPU-validated.

## Hard constraints

- **Breeze is English + Chinese only.** German uses Qwen3-TTS CustomVoice.
  Never claim Breeze supports German.
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
- **Qwen3 CustomVoice has no reference cloning.** Use presets
  (`Vivian`, `Serena`, `Ryan`, `Aiden`); no `ref_audio_path`.

## Layout conventions

- Python package under `src/drama_studio/`; tests under `tests/`.
- App-specific engine code in `engines/`; vendor code stays read-only.
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
```

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
