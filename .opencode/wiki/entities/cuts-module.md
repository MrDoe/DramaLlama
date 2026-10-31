---
title: Cuts module
tags: [cuts, domain, alignment, takes]
sourceRefs:
  - src/drama_studio/cuts.py
  - src/drama_studio/handlers.py
  - src/drama_studio/api.py
lastReviewed: 2026-10-31
---

# Cuts module (`src/drama_studio/cuts.py`)

Pure domain logic for take-aware cuts, shared by handlers and the API. No job
queue, no store, no numpy import at module load.

- `safe_filename(value)` — single regex for `lines/<line>.wav` names.
- `freeze_legacy_cuts(alignment, fallback)` — stamp `take_id=None` cuts with
  the previously selected take before the selection changes.
- `scale_window(start, end, old_dur, new_dur)` — duration-ratio scaling,
  clamped and rounded to ms.
- `validate_cut(line_id, take, start, end, duration)` — raises `ValueError`
  with `cut for {line} out of range for take {id} (0..{dur}s)`.
- `remap_removed_cuts(alignment, removed, new_take, directory)` — re-point
  cuts whose take disappeared, rewrite affected line clips. Raises
  `FileNotFoundError` when the replacement audio is missing (callers map to
  `PermanentJobError` / `404`), instead of silently leaving dead take ids.
- `normalize_alignment(alignment, takes)` — fill legacy `None` with the
  selected (or first) take on load. Called in `ProjectStore.get`.
- `pick_selected_take(takes, selected_take_id)` — honor
  `StemRenderResult.selected_take_id`, then flagged take, then first. Fixes
  the old Suno path that always picked `result.takes[0]`.

## Related

- [[concepts/take-aware-cuts]]
- [[concepts/engine-selection]]
- [[concepts/cut-board-multitrack]]
