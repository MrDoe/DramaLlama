---
title: Multitrack Cut board
tags: [cut, multitrack, timeline, webaudio, waveform, takes]
sourceRefs:
  - web/src/components/MultiTrackEditor.tsx
  - web/src/audio/timeline.ts
  - web/src/audio/syncEngine.ts
  - src/drama_studio/mixer.py
  - src/drama_studio/api.py
lastReviewed: 2026-10-31
---

# Multitrack Cut board

The Cut step is one shared timeline over all voice stems, not a card per stem.
Every line region sits at the position it will occupy in the final mix.

## Placement is source-script order

`computeTimeline()` in `web/src/audio/timeline.ts` mirrors
`mixer.py::build_mix`:

- lines iterate in script order (scenes, then lines)
- `pause_ms` after every line, `scene_gap_ms` on a scene change
- clip duration = the line's alignment cut length (`end_s - start_s`)
- `TAIL_S = 2.0` s is appended

There are no server-side stem offsets. A stem take is a continuous
performance, but its lines are placed individually, so a take's dead air or
internal pauses never push a region out of script order. Saved alignments seed
the initial layout; pending edits feed the same function, so trimming updates
the layout exactly like the next mix rebuild will.

## Editing

- **Trim** either edge → changes the cut window and the slot length; every
  later line shifts by the same amount, matching the mix.
- **Body drag** → slip edit: the local cut window moves inside the take while
  the region keeps its script-derived slot. Length and all other clips stay
  fixed.
- **Numeric start/end** → keyboard-accessible equivalent of the trims, in
  local take seconds.
- **Track dropdown** → moves one region to another take of the same stem and
  scales its window by the duration ratio. It lists every take, marks the
  stem's default, and is a dirty edit (saved with the stem, not immediately).
- **Save / Save all** → `PATCH .../stems/{id}/alignment` with `rebuild_mix=true`
  and a `take_id` per cut; only dirty stems are saved.

## Playback

`SyncEngine` (`web/src/audio/syncEngine.ts`) decodes one `AudioBuffer` per
**take** and one `GainNode` per **stem** for mute/solo/volume.
`play(fromGlobal, segments)` creates a `BufferSource` **per line segment**,
resolved through `ScheduleSegment.takeId`, scheduled at its global slot and
started mid-window when the playhead is inside it. Regions entering after the
playhead are scheduled on the same `AudioContext` clock, so tracks stay in
sync. Region audition passes a single segment plus `onlyStemId`.

Decoding is lazy: the board loads each stem's selected take plus only the takes
referenced by its cuts, so historical generations cost nothing until a region
picks them. See [[concepts/take-aware-cuts]] for the data model and render
rules.

## Layout details

- One ruler, one canvas per stem, shared view and rose `#fb7185` 1px playhead.
- Wheel pans when zoomed; Ctrl/Meta+wheel zooms at the cursor; plain wheel-up
  at fit zooms in. Icon buttons cover zoom, pan, selection zoom and fit.
- Waveform rows resize to the natural height of the left track card. Measure
  from the card's child content, not its box: grid stretch makes the card
  follow the canvas height and a box measurement feeds back.

## Related

- [[concepts/take-aware-cuts]]
- [[concepts/script-to-stem-pipeline]]
- [[concepts/sfx-library-and-mix]]
