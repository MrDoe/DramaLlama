---
title: Engine selection
tags: [engine, providers, takes, ab-testing]
sourceRefs:
  - src/drama_studio/engines/manager.py
  - src/drama_studio/handlers.py
  - web/src/pages/ProjectPage.tsx
lastReviewed: 2026-10-30
---

# Engine selection

Engine choice is two-layered:

1. **Project default** — `Project.default_engine` (set on create, editable via
   `PUT /api/projects/{id}` with an engine name or `"auto"`). The project header
   exposes it as a picker. Engines are not tied to language: Chatterbox and Suno
   are multilingual, Breeze is English, Breeze-DE is German.
2. **Per-stem override** — every stem has an optional `engine` field. The Render
   card shows a dropdown of engines compatible with the project language, and
   `PATCH /stems/{id}` persists the choice.

| Engine | Languages | Voice source |
|---|---|---|
| `breeze` | en | reference design |
| `breeze-de` | de | reference design (Articulatio-DE sidecar) |
| `chatterbox` | de, en | uploaded sample or server default |
| `suno` | de, en | preset / Suno persona, no local reference |
| `fake` | de, en | none, test only |

`"auto"` resolves to `breeze` for English and `chatterbox` for German.

## Inheritance rules

- Characters parsed before this change carried the language default in
  `Character.engine`; `ProjectStore.get` treats that legacy value as `None`, so
  it inherits the project default instead of pinning Breeze/Chatterbox.
- Changing the project default rewrites stems whose `engine` is `None` or equal
  to the previous default. Explicit per-stem choices (a one-off Suno render,
  `fake`) stay put.
- The render endpoint resolves the engine as
  `body.engine → stem.engine → project.default_engine → language default`, and
  only `breeze`/`breeze-de` require a reference file. Suno projects no longer
  409 with "design a reference voice first".

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
- [[entities/suno-engine]]
- [[concepts/script-to-stem-pipeline]]
