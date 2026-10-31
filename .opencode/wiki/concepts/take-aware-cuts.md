---
title: Take-aware cuts
tags: [cut, takes, alignment, suno, multitrack, audio]
sourceRefs:
  - src/drama_studio/models.py
  - src/drama_studio/api.py
  - src/drama_studio/handlers.py
  - src/drama_studio/engines/suno.py
  - src/drama_studio/cuts.py
  - web/src/components/MultiTrackEditor.tsx
  - web/src/audio/syncEngine.ts
  - web/src/audio/timeline.ts
lastReviewed: 2026-10-31
---

# Take-aware cuts

Before this change a stem had exactly one cut sheet, and every line clip
implicitly came from the stem's *selected* take. Re-rendering a Suno stem
replaced its takes, so the cut sheet either broke or had to be thrown away.
Now each cut names the take it was measured on, and the Cut board is a montage
of takes within one stem row.

## Data model

The choice lives on the existing cut sheet — no new store, table or file
format.

```python
class AlignedLine(BaseModel):
    line_id: str
    take_id: str | None = None   # None = the stem's selected take
    start_s: float
    end_s: float
```

- `project.alignments[stem.id]` is still the single cut sheet per stem.
- `None` is the legacy/compat value: the cut follows whichever take is
  selected. New saves always stamp a concrete `take_id`.
- `CutUpdate.take_id` in `PATCH .../stems/{id}/alignment` accepts it; omitting
  it (or sending `null`) means "use the selected take".
- The frontend mirrors the field in `Cut` / `AlignedLine` / `ScheduleSegment`.

## Saving a cut sheet

`update_alignment()` in `api.py` now resolves audio per cut, not per stem.
It shares `cuts.safe_filename` / `cuts.validate_cut` with the batch endpoint:

1. Ownership + duplicates first: unknown `line_id` or duplicate entries are
   `400` before any audio is touched.
2. Build `takes_by_id` once and resolve each cut's take (`null` → selected).
   Unknown IDs are `404 take ... not found on stem`; missing files are `404
   take {id} audio missing`.
3. Decode and cache each referenced take once (`read_mono`); its real decoded
   duration, not the stored metadata, is the validation bound.
4. Validate every cut in a first pass, pre-slice all segments into memory, then
   write all `lines/<line_id>.wav` files (atomic batches — a bad window cannot
   half-write).
5. Mark the sheet `aligner="manual"`, set `project.mix_stale=True`, and rebuild
   the mix when requested (which clears `mix_stale`).

`PATCH /api/projects/{id}/alignments` applies the same rules to several stems
and rebuilds the mix once. The Cut board's Save-all uses it so N dirty stems
cost one mix rebuild instead of N.

`mixer.py` is untouched: it still reads `lines/<line_id>.wav`. Take choice is
therefore purely a slicing concern — the mix never needs to know about takes.
`MixBoard` shows a `mix stale` badge while `project.mix_stale` is true.

## Render rules in `handlers.render_stem`

### 1. Freeze legacy cuts first

`_freeze_legacy_cuts(alignment, old_selected)` runs **before** any selection
changes. It stamps the previously selected take ID on every `take_id is None`
line. Without this, a legacy cut would silently follow the *new* selected take
and read its window from the wrong audio.

### 2. Suno appends, it does not replace

- `SunoEngine.render_stem` allocates a unique generation prefix:
  take IDs `suno-{stem_id}-{generation}-{index}`, WAV files
  `{slug}-{generation}-{index}.wav` with
  `slug = slugify(f"suno-{stem_id}")[:40]`.
- Every take with a `clip_id` dedupes on
  `(engine, clip_id, vocal_isolated)`; only the matching old copy is removed.
  Takes without a `clip_id` never participate.
- All surviving takes are deselected, then the newest result take is selected.
- Historical takes and their audio stay on the stem for A/B comparison.

### 3. Preserve non-empty cut sheets

After a render (Suno or not), an existing cut sheet with lines is kept as-is.
Estimated/Whisper alignment runs only when the stem has no cuts yet. A Suno
re-render therefore leaves every region's take and trim untouched — including
manual `aligner="manual"` sheets.

### 4. Remap cuts whose take disappeared

`_remap_removed_cuts(alignment, removed, new_take, directory)` handles both
Suno dedupe and non-Suno same-engine replacement:

- scale `start_s` / `end_s` by `new_duration / old_duration`
- clamp to the new take and stamp the new take ID
- rewrite only the affected `lines/<line>.wav` clips from the new audio
- if the removed duration is ~0, keep the ratio at 1.0

Non-Suno engines keep their old semantics (same-engine takes are replaced and
the new take becomes selected); the difference is that cuts now survive via
remapping instead of being invalidated.

## `select_take` is now guarded

`PATCH .../stems/{id}/take` used to always queue `stem.realign`, which replaces
the whole cut sheet. It now:

- freezes `None` cuts to the previous selection before changing it (via
  `cuts.freeze_legacy_cuts`);
- queues `stem.realign` only when the sheet is empty **or** every cut still
  points at the old selected take and `aligner != "manual"`;
- otherwise returns `job: null` and keeps the current sheet.

`realign_stem` additionally refuses to clobber a `manual` sheet that appeared
after the job was queued (queued-realign race): it returns the existing sheet
instead of overwriting newer manual trims.

The frontend treats the job as nullable (`Job | null`) and only tracks a real
job.

## Import, extract and explicit realign

- `import_suno_take`: same `(engine, clip_id, vocal_isolated)` dedupe, freezes
  legacy cuts, remaps cuts that pointed at the replaced copy, and only seeds
  estimated timing when no cut sheet exists.
- `extract_stem`: freezes legacy cuts, appends the vocal take, and re-aligns
  only when every cut is `None` or belonged to the source take. A mixed sheet is
  preserved untouched.
- `realign_stem` / estimated alignment stamp `selected.id` on every produced
  line, so newly created sheets are take-aware from the start.

## Frontend

`MultiTrackEditor.tsx` + `SyncEngine`:

- `SyncEngine` keys `AudioBuffer`s by **take ID**; gains stay keyed by **stem
  ID** so mute/solo/volume still act per character. Buffers are LRU-capped
  (`maxTracks=12`, `lastUsed` timestamps, `prune(keep)`); the board prunes to
  `neededTakes` after each render so historical generations do not leak memory.
  Peaks are computed from channel 0 (takes are mono wavs).
- `ScheduleSegment` carries `takeId`; playback resolves the buffer per segment.
- Takes decode lazily: each stem's selected take plus every take referenced by
  a cut (`neededTakes`). Older generations stay undecoded until a clip picks
  them.
- Region labels show `T#` when a stem has more than one take; the track card
  shows `N takes`.
- The selection panel has a **Track** dropdown listing *all* stem takes (not
  just Suno), labelled `T# · engine [vocal] · duration [· default]`.
- Switching a region's track scales its window by
  `newDuration / oldDuration` (metadata fallback before decode), clamps it,
  and avoids a zero-length window. It marks the stem dirty; it is **not**
  auto-saved.
- Save sends `take_id`; validation checks each cut against its take's duration.
  Save-all uses `PATCH /alignments` (one mix rebuild).
- `takeDurationOf()` falls back to `take.duration_s` so a switch computes
  correctly even while the new take is still decoding.
- Explicit but missing `take_id`s no longer fall back to the selected take:
  `takeByIdFor` returns `null`, the region is flagged invalid, and a banner
  names the dead take ids. `beforeunload` guards unsaved dirty stems.

## Gotchas

- Freeze legacy cuts before changing selection, in every code path that
  changes `selected`. `None` is dynamic by definition.
- The dedupe key includes `vocal_isolated`: a full mix and a vocals-only
  generation of the same clip are distinct tracks.
- A Suno re-render preserves the sheet even when it adds no usable new cut
  timing; use **Realign** explicitly if you want fresh Whisper cuts.
- Track switching is a dirty edit. Reloading the page discards it until
  **Save all** runs.
- The old take's audio file is not deleted on dedupe/replacement; clips
  remapped away from it are rewritten, but the file may linger for other cuts.

## Live verification (2026-10-31)

Scratch project with a 12 s take A and 14 s take B, one manual sheet:

- switching region 2 to take B scaled `3.0–5.0` → `3.5–5.83`
- Save persisted `take_id=take-b`
- saved `lines/s1-02.wav` measured 659.87 Hz (take B is 660 Hz) and 2.33 s
- reload preserved the choice; audition stopped at the slot boundary

## Related

- [[concepts/cut-board-multitrack]]
- [[entities/suno-engine]]
- [[concepts/script-to-stem-pipeline]]
- [[concepts/engine-selection]]
