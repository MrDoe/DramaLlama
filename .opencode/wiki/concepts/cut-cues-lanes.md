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
lastReviewed: 2026-11-04
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
