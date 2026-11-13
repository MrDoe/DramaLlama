---
title: Cut board cue lanes & region effects
tags: [cut, cues, sfx, music, multitrack, webaudio, effects, fade, speed, pitch, reverb, dereverb]
sourceRefs:
  - web/src/audio/cues.ts
  - web/src/audio/syncEngine.ts
  - web/src/audio/timeline.ts
  - web/src/components/MultiTrackEditor.tsx
  - web/src/pages/ProjectPage.tsx
  - src/drama_studio/api.py
  - src/drama_studio/audio.py
  - src/drama_studio/mixer.py
  - src/drama_studio/models.py
  - src/drama_studio/cuts.py
lastReviewed: 2026-11-13
---

# Cut board cue lanes

Music and SFX cues are editable on the shared Cut timeline, not just in the
Sound step. Two extra lanes (`Music`, `SFX`) render every cue at its mix
position; selecting a clip opens an inspector (anchor, offset, gain, kind,
loop/duck, audition, two-click delete); `Save all` persists cue edits
together with cuts.

## Placement mirrors the mix

`computeCueSpans()` in `web/src/audio/cues.ts` mirrors
`mixer.py::build_mix` cue placement: anchor = the anchor line's global slot
start + `offset_s`; one-shots run for their source length; beds/music run to
the end of the anchored scene (loop repeats the source, non-loop stops at the
shorter of source rest and scene rest). Everything clips to the dialogue
timeline. Unknown/missing sources use a 1.5 s placeholder width.

## Per-region effects (Suno Studio-style)

Every cut region carries seven effects, neutral by default so old saves render
unchanged (gain, fade in/out, speed, pitch, reverb, dereverb). Full field
table, DSP chain order, preview-vs-mix and persistence details live in
[[concepts/cut-region-effects]].

## Script-order alignment

`cuts.layout_script_order()` walks the script interleaving speakers
(Viktoria 1, Andreas 1, Grandmother 1, Viktoria 2, …) with `pause_ms` /
scene gaps, writing `position_s`. `POST …/alignments/align-script` exposes
it; mixer falls back to it when `position_s` is `None`. LMB body-drag moves a
selected section (`position_s`); edge drag trims; `Alt`+drag slips the window.

## Music and beds live on the timeline

A cue has two placement modes, and which one it is in is visible in the
inspector:

| | set | start | extent |
|---|---|---|---|
| **freeform** | `position_s` | that number | `end_s`, or the end of the play |
| **anchored** | `anchor_line_id` | the line's slot + `offset_s` | clipped to the anchor's scene |

`Cue.anchor_line_id` is optional and `position_s: null` / `anchor_line_id: null`
switch between the two, so a cue can be detached and re-anchored.

Anchoring is right for a footstep: it happens *at* a word and should follow that
word when the dialogue reflows. It is wrong for a score. Clipping music to the
scene its anchor line belongs to meant a theme could not score more than one
scene, and dragging it out of that scene made the mixer measure a non-positive
length and skip it — reported as a successful mix with no warning. The Cut board
dropped the span too, so the clip simply vanished on the next reload. That is the
bug behind "music is not movable".

Dragging now splits by kind: **music and beds write `position_s` and drop their
anchor**, so a drag places them anywhere and they run to the end; **one-shots keep
dragging by `offset_s`** so they stay glued to their line. The Sound step and the
board's insert bar create new music cues freeform at the playhead, never anchored.

A cue that cannot play is drawn as a 3-second dashed stub — pinned to the right
edge if it is past the end of the play, otherwise at its own start — so it stays
possible to drag back, and the mix says `cue X is not in the mix: it starts at
101.3s, past
the end of the scene it is anchored to (23.2s)`. A silent, invisible cue is the
failure mode; both now speak up.

## Playback

`SyncEngine` gained per-clip `gain` (linear amplitude from `gain_db`) and
`loop` (buffer loop until `localEnd`) on `ScheduleSegment`, plus
`maxTracks 12→24` since cue buffers share the LRU cache under `sfx:<id>`.
Cue rows use their own `GainNode`s (`cue:music` / `cue:sfx`) so mute/solo
semantics stay per stem. Transport plays dialogue + cues together;
per-cue audition plays one span with `onlyStemId` on its lane.

## Saving

- Dragging a cue horizontally stages `offset_s` locally (`cueDirty`); the
  inspector edits anchor/kind/gain/loop/duck the same way.
- `Save all` PATCHes each dirty cue via `api.updateCue` (sequential, then one
  project refresh) and clears only the saved ids. The toolbar counts cuts +
  cues and shows the `mix stale` badge.
- `PATCH /api/projects/{id}/cues/{cue_id}` (`CueUpdate`, all fields optional)
  sets `mix_stale`; anchor lines must exist. The `sfx_id` is only re-validated
  when it *changed* — re-saving a full cue body must not 404 just because its
  file is not indexed on this machine (demo SFX missing from `~/Sounds`).
  Covered by `test_cue_patch_keeps_missing_sfx_id`.
- Delete is two-click (arm → confirm) and refreshes the project.
- `ProjectPage`'s save handlers now return their promises (no `void`
  wrapper), so the toolbar keeps showing `Saving…` and dirty flags until the
  PATCH and mix rebuild finish. Failures keep the edits dirty for a retry.

## Verification notes

- `click_by_uid` can silently miss the right-side `Save all` button in a
  narrow viewport (no error, handler never fires). For non-audio actions use
  `evaluate_script` `btn.click()` and confirm via API; reserve `click_by_uid`
  for trusted audio gestures.
- Live check on `was-wir-begraben-haben-7a3f9c` (11 cues, takes missing):
  lanes render, selection/inspector/dirty-count/stale-badge/save roundtrip and
  delete-arm all observed; scratch values reverted afterwards.

## Related

- [[concepts/cut-board-multitrack]]
- [[concepts/take-aware-cuts]]
- [[concepts/sfx-library-and-mix]]
- [[entities/cuts-module]]
