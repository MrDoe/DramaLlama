# Drama Studio

A local-first web app that turns a pasted script into a finished audio drama.

```
pasted script
   │  deterministic scan + optional LLM speaker attribution
   ▼
scenes / lines ──group by scene+speaker──▶ stem scripts
   │                                           │ Breeze / Qwen3
   │                                           ▼
   │                                     stem WAV per character
   │                                           │ Whisper word alignment
   ▼                                           ▼
SFX cues ◀── local library search ────── line clips
   │                                           │
   └──────────────► mix / master ◀─────────────┘
```

## Status

Working end-to-end on the English path:

- Paste Markdown / screenplay / plain text, deterministic parse with cast-table
  detection, speaker aliases, stage directions and 100% dialogue coverage.
- Optional LLM parse through Ollama with strict verbatim validation and a
  deterministic fallback.
- Per-scene, per-character stem scripts.
- Breeze reference design → scene-stem render → Whisper CPU alignment → line
  clips. Validated on one real scene: 10.1 s load, 103.5 s render for 28.0 s
  audio, 60.8 s alignment, 6/6 lines at confidence 1.0, 8.47 GB peak VRAM.
- SFX library indexing over `~/Sounds` + uploads, cue sheet anchored to lines.
- Mix: dialogue assembly, bed ducking, telephone filter, −16/−23 LUFS
  loudnorm, WAV + MP3. Demo mix: 27 lines, 94.3 s, 1 cue, no warnings.
- German path implemented with Qwen3-TTS CustomVoice presets; the German
  generation smoke test is queued behind a sibling GPU render.

## Quick start

Use the existing TTS venv so torch and the audio stack are already available:

```bash
cd ~/code/drama-studio
~/.venvs/qwen3tts/bin/pip install -e . --no-deps

cd web
npm install
npm run build
cd ..

~/.venvs/qwen3tts/bin/drama-studio serve
# http://127.0.0.1:8770
```

`serve` starts the FastAPI app and an in-process worker. Use
`--no-worker` if you want to run `drama-studio worker` in a separate
terminal (recommended while debugging model loads).

```bash
~/.venvs/qwen3tts/bin/drama-studio worker --once
~/.venvs/qwen3tts/bin/drama-studio jobs
```

## Workflow

1. **Script** — paste a script, choose English or German.
   `Save & parse` is deterministic; `LLM parse` uses Ollama for messy
   attribution and falls back safely if the model is offline.
2. **Stems** — group lines by scene + speaker. Each stem is one continuous
   performance, not one TTS call per line.
3. **Render** — design a 6–11 s reference for each Breeze character (or choose a
   Qwen3 preset for German), then render a stem. The worker renders the stem on
   GPU, aligns it with Whisper on CPU, cuts line clips, and stores confidence.
4. **Sound** — search `~/Sounds` (plus `data/sfx` and uploads), preview, and
   attach beds / one-shots / music to specific lines.
5. **Mix** — assemble line clips, duck beds under speech, filter telephone
   lines, loudnorm to podcast (−16 LUFS) or broadcast (−23 LUFS), and download
   WAV/MP3.

## Engines

| Language | Engine | Voice workflow |
|---|---|---|
| English | Breeze TTS 2 | 6–11 s reference design → clone per scene stem |
| German | Qwen3-TTS CustomVoice | preset voice per character + instruction |
| tests | Fake | sine/noise stems, no GPU |

Breeze weights and outputs are research / non-commercial only. The vendored
stem/aligner is Apache 2.0 code; see `vendor/breeze_stems/VENDORED_FROM.md`.

## Layout

| Path | Purpose |
|---|---|
| `src/drama_studio/` | FastAPI, queue, worker, parser, engines, mixer |
| `src/drama_studio/engines/` | Breeze / Qwen3 / Fake adapters |
| `vendor/breeze_stems/` | pinned Breeze stem renderer + Whisper aligner |
| `web/` | React + Vite + TypeScript UI |
| `data/projects/<id>/` | project JSON, refs, takes, line clips, mix |
| `data/jobs.sqlite3` | job queue (SQLite, WAL) |
| `tests/` | 55 tests, no GPU required |

## Configuration

Environment variables:

| Variable | Default |
|---|---|
| `DRAMA_STUDIO_DATA` | `<repo>/data` |
| `DRAMA_STUDIO_HOST` / `PORT` | `127.0.0.1` / `8770` |
| `OLLAMA_URL` | `http://127.0.0.1:11434` |
| `DRAMA_STUDIO_LLM` | `gemma4:12b-16k` |
| `DRAMA_STUDIO_SFX_ROOTS` | `~/Sounds:<repo>/data/sfx` |

Personal subfolders (`juli`, `miller`, `Projects`, `Snippets`) are excluded
from the SFX index by default.

## Testing

```bash
~/.venvs/qwen3tts/bin/python -m pytest
cd web && npm run build
```

Real-model smoke tests are opt-in:

```bash
~/.venvs/qwen3tts/bin/python scripts/smoke_breeze.py --scene 1 --speaker MARA
~/.venvs/qwen3tts/bin/python scripts/smoke_qwen.py --scene 1 --speaker "ANNA BERG"
```

## Known gaps

- German Qwen3 generation needs a final GPU smoke test and a UI render pass.
- Reference monologues currently default to the character's first three lines;
  a dedicated editor is the next UI addition.
- Cue timing is line-anchored; a drag-and-drop waveform timeline is not built yet.
- LLM parse requires Ollama and is intentionally serialized against Breeze on
  the 16 GB GPU.
