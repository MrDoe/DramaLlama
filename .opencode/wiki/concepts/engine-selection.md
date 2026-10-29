---
title: Engine selection
tags: [engine, providers, takes, ab-testing]
sourceRefs:
  - src/drama_studio/engines/manager.py
  - src/drama_studio/handlers.py
  - web/src/pages/ProjectPage.tsx
lastReviewed: 2026-10-29
---

# Engine selection

Every stem has an optional `engine` field. The Render card shows a dropdown of
engines compatible with the project language, and `PATCH /stems/{id}` persists
the choice.

| Language | Engines |
|---|---|
| English | `breeze`, `chatterbox`, `fake` |
| German | `chatterbox`, `fake` |

## Takes and A/B

`Take.engine` records the provider. A render replaces only takes from the same
engine; other engines' takes stay visible as labelled pills. Selecting a take
queues `stem.realign`, which re-runs Whisper against the chosen file and
updates the line clips.

`EngineManager` unloads the previous local engine before loading another
(Breeze ↔ Chatterbox local model; Chatterbox is external). Chatterbox and Suno
live outside the worker process.

## Related

- [[entities/breeze-engine]]
- [[entities/chatterbox-engine]]
- [[concepts/script-to-stem-pipeline]]
