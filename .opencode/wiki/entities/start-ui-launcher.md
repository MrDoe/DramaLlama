---
title: start-ui.sh launcher
tags: [operations, ui, build, vite]
sourceRefs:
  - start-ui.sh
  - README.md
  - src/drama_studio/config.py
  - src/drama_studio/api.py
  - web/vite.config.ts
lastReviewed: 2026-10-30
---

# start-ui.sh launcher

`./start-ui.sh` is the one-command entry point for the React UI. It replaces
the manual "npm install → npm run build → drama-llama serve" sequence in the
README quick start.

## Default mode

1. Resolves `drama-llama` from `$DRAMA_LLAMA_BIN`, then the TTS venv, then
   `PATH`; the legacy `drama-studio` binary and `$DRAMA_STUDIO_BIN` still work
   as fallbacks.
2. Probes `GET /api/health` on the configured port. If a Drama Llama API
   already answers, it prints the URL and exits 0 instead of failing on an
   address collision.
3. Rebuilds `web/dist` when it is missing, or when anything under `web/src`,
   `web/public`, `web/index.html`, `web/package.json`, `web/vite.config.ts` or
   `web/tsconfig.json` is newer than `dist/index.html`. `--build` forces this,
   `--no-build` skips it and fails if the bundle is absent.
4. `exec`s `drama-llama serve --host … --port …`, keeping the worker
   in-process. Unrecognized arguments are forwarded to `serve`.

## Dev mode

`--dev` runs `npm run dev` in `web/` (Vite on `:5173`, proxying `/api` to
`:8770`) alongside the FastAPI server. If an API is already running on the
port, it is reused and only Vite starts. The EXIT/INT/TERM trap kills the Vite
child when the script stops.

`--open` polls `/api/health` and then calls `xdg-open`, so the browser is not
opened before uvicorn is accepting requests.

## Environment

`DRAMA_LLAMA_BIN`, `DRAMA_LLAMA_HOST` and `DRAMA_LLAMA_PORT` override the
defaults; the legacy `DRAMA_STUDIO_*` names still work. `--host` / `--port` do
the same and are normalized before the health probe. Chatterbox and the Suno
bridge are separate services and are not started by this script.

## Related

- [[entities/drama-llama-app]]
- [[entities/job-worker]]
