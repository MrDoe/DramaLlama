---
title: Drama Studio app
tags: [app, fastapi, react, projects, storage]
sourceRefs:
  - src/drama_studio/api.py
  - src/drama_studio/store.py
  - web/src/App.tsx
  - web/src/pages/ProjectPage.tsx
lastReviewed: 2026-10-29
---

# Drama Studio app

Local single-user web app at `http://127.0.0.1:8770`. FastAPI serves the API
and the built React bundle from `web/dist`.

## Project lifecycle

1. `POST /api/projects` stores a project under `data/projects/<slug>-<hex>/`.
2. `PUT /api/projects/{id}` saves raw text and reparses deterministically.
3. `POST /api/projects/{id}/parse` can use the LLM with deterministic fallback.
4. `POST /api/projects/{id}/stems` groups lines by scene + speaker.
5. `POST /api/projects/{id}/characters/{speaker}/reference` designs a Breeze
   reference (or stores a Qwen3 preset).
6. `POST /api/projects/{id}/stems/{stem_id}/render` queues a GPU render +
   CPU alignment.
7. `POST /api/projects/{id}/cues` anchors SFX to lines.
8. `POST /api/projects/{id}/mix` assembles and masters the final audio.

Project content is JSON (`project.json`) plus `refs/`, `audio/takes/`,
`lines/`, and `mix/`. SQLite (`data/jobs.sqlite3`) is only the job queue.

## UI

Five production steps in `ProjectPage`: Script, Stems, Render, Sound, Mix.
The visual system is a dark studio console (deep indigo, amber accent,
Archivo/Space Grotesk) with a three-pane script/coverage/cast view and
panel-based later stages.

## Related

- [[concepts/script-to-stem-pipeline]]
- [[entities/job-worker]]
- [[concepts/sfx-library-and-mix]]
