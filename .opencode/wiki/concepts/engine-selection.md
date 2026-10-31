---
title: Engine selection
tags: [engine, providers, takes, ab-testing]
sourceRefs:
  - src/drama_studio/models.py
  - src/drama_studio/engines/manager.py
  - src/drama_studio/handlers.py
  - src/drama_studio/api.py
  - web/src/pages/ProjectPage.tsx
lastReviewed: 2026-10-31
---

# Engine selection (project-level only)

Per-stem engine overrides were removed. Engine choice is project-level:

- **Project default** — `Project.default_engine` (set on create, editable via
  `PUT /api/projects/{id}` with an engine name or `"auto"`). The project header
  exposes it as a picker. `"auto"` resolves to `breeze` for English and
  `chatterbox` for German.
- **No per-stem override** — `Stem.engine` and `Character.engine` no longer
  exist. Old `project.json` files that still contain them load fine
  (pydantic ignores the legacy keys); new saves never write them.
- `PATCH /stems/{id}` and `POST /stems/{id}/render` reject any `engine` field
  with `400 per-stem engine selection was removed; set Project.default_engine
  instead`.

| Engine | Languages | Voice source |
|---|---|---|
| `breeze` | en | reference design |
| `breeze-de` | de | reference design (Articulatio-DE sidecar) |
| `chatterbox` | de, en | uploaded sample or server default |
| `suno` | de, en | preset / Suno persona, no local reference |
| `fake` | de, en | none, test only |

## Resolution

Render and reference-design resolve as `project.default_engine → language
default`. `Take.engine` remains as provenance (which provider rendered this
take) and drives same-engine replacement, but it is never a selection.

## Takes and A/B

A render replaces only takes from the resolved project engine; other engines'
takes stay visible for A/B comparison. `StemRenderResult.selected_take_id` is
honored via `cuts.pick_selected_take` (explicit id → flagged take → first).

## Related

- [[entities/breeze-engine]]
- [[entities/chatterbox-engine]]
- [[entities/suno-engine]]
- [[concepts/take-aware-cuts]]
- [[entities/cuts-module]]
