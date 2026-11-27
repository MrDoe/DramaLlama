---
title: SFX library and mix
tags: [sfx, sound-design, mixing, loudness, cues]
sourceRefs:
  - src/drama_studio/sfx.py
  - src/drama_studio/mixer.py
  - src/drama_studio/api.py
  - src/drama_studio/models.py
  - web/src/audio/cues.ts
lastReviewed: 2026-11-21
---

# SFX library and mix

## Library

`SfxLibrary` walks `~/Sounds` and `data/sfx`, indexes metadata into
`data/sfx_index.json`, and never copies source files. First scan of 322 files
(14 GB) takes about 26 s; later scans reuse the cache.

Personal folders `juli`, `miller`, `Projects`, `Snippets` are excluded by
default. Search is filename/tag based; playback streams through
`/api/sfx/{id}/audio`.

## Cues

`Cue` carries optional `anchor_line_id` and `position_s`, plus `offset_s`,
`kind` (`bed` / `one_shot` / `music`), `gain_db`, fades, loop, duck and filter
(`telephone` / `radio`). A cue is **freeform** when `position_s` is set — it
starts there and runs to its `end_s` or the end of the play — and **anchored**
when `anchor_line_id` is set, starting at the line's slot plus `offset_s`.
One-shots belong on a line; music and beds belong on the timeline. See
[[concepts/cut-cues-lanes]] for placement and the drag behaviour that keeps a
score from silently vanishing.

## Mix chain

1. Assemble line clips in script order with `pause_ms` inside a scene and
   `scene_gap_ms` between scenes.
2. High-pass dialogue at 80 Hz.
3. Place cues at their timeline slots or line anchors; freeform beds/music run
   to the end of the play, anchored ones to the end of the anchor's scene.
4. Compute a speech envelope and duck beds by 4.5 dB only while speech is
   present.
5. Telephone/radio filter uses a 300–3400 Hz band-pass with soft saturation.
6. Optional master reverb (see below).
7. Peak-normalize, then ffmpeg `loudnorm` to −16 LUFS (podcast) or −23 LUFS
   (broadcast), 48 kHz stereo. Export WAV + 320 kbps MP3.

## Master reverb

`Project.settings.master_reverb` is `{enabled, ir_path, wet}` and defaults to
off. The Mix step uploads an impulse response to `<project>/irs/` through
`POST /api/projects/{id}/ir/upload`; the IR is resampled if needed, truncated
to `TAIL_S = 2.0` s (the slack the mix already carries after the last line),
normalised by RMS energy, and convolved with the finished stereo mix before
peak/loudnorm. `wet` (0–1) blends wet and dry. Failures degrade to the dry mix
with a warning — reverb must never break a render, and the disabled path is
byte-identical to the pre-reverb mix. This exists because the room sim's
32768-sample history gives only 0.68 s at 48 kHz, so a cathedral tail has to
be a post-mix effect. See [[concepts/spatial-stage-rendering]].

## Ambisonic export and SFX

The Ambisonic master is written from per-scene beds (see
[[concepts/spatial-stage-rendering]]). The SFX bed has no spherical-harmonic
representation, so it used to be silent there. The export now folds the mono
effects bed into the W (omni) channel of a **copy** of the bed at
`SFX_AMBISONIC_GAIN = 0.8`; the stereo decode still uses the untouched bed plus
its own effects sum. Unstaged scenes and unstaged dialogue remain silent in the
Ambisonic file, which is inherent, not a bug.

## Related

- [[entities/drama-llama-app]]
- [[concepts/script-to-stem-pipeline]]
- [[concepts/project-json-concurrency]] — why a track the worker just downloaded
  can answer "sfx not found" from the API's own copy of the index
