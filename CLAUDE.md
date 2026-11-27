# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

The canonical agent notes live in `AGENTS.md` (constraints, layout, commands,
gotchas, mandatory UI verification). Read and follow it:

@AGENTS.md

## Extra commands

```bash
~/.venvs/qwen3tts/bin/python -m pytest tests/test_mixer.py              # one file
~/.venvs/qwen3tts/bin/python -m pytest tests/test_mixer.py -k ducking   # one test
~/.venvs/qwen3tts/bin/ruff check src tests                              # lint (config in pyproject.toml)
cd web && npm run dev                                                   # Vite dev server
```

## Request flow (big picture)

- `api.py` (FastAPI) handles project CRUD and enqueues jobs into the SQLite
  queue (`jobs.py`); it never imports torch.
- `worker.py` pulls jobs one at a time and dispatches to `handlers.py`, which
  loads engines from `engines/` (Breeze, Breeze-DE sidecar, Chatterbox docker,
  Suno bridge, Fake) — this serialization is what enforces "one GPU job".
- Project content is files via `store.py` (`ProjectStore`) using Pydantic
  models in `models.py`; job payloads carry project-relative paths.
- Pipeline modules: `parser.py`/`llm.py` (script → scenes/lines) →
  `stems.py` (per-character, per-scene stems) → engine render →
  `aligner.py`/`cuts.py` (Whisper alignment, vendored `align_cuts`) →
  `sfx.py` (cue search) → `mixer.py` (+ optional `spatial.py`) → export.
- The React UI (`web/src`) talks to the API through `api.ts`; workflow steps
  are wired in `pages/ProjectPage.tsx`.
