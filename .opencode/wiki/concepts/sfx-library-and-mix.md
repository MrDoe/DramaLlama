---
title: SFX library and mix
tags: [sfx, sound-design, mixing, loudness, cues]
sourceRefs:
  - src/drama_studio/sfx.py
  - src/drama_studio/mixer.py
  - src/drama_studio/api.py
lastReviewed: 2026-10-30
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

`Cue` is line-anchored: `anchor_line_id`, `offset_s`, `kind`
(`bed` / `one_shot` / `music`), `gain_db`, fades, loop, duck and filter
(`telephone` / `radio`).

## Mix chain

1. Assemble line clips in script order with `pause_ms` inside a scene and
   `scene_gap_ms` between scenes.
2. High-pass dialogue at 80 Hz.
3. Place beds/one-shots; loop beds to the end of the anchored scene.
4. Compute a speech envelope and duck beds by 4.5 dB only while speech is
   present.
5. Telephone/radio filter uses a 300–3400 Hz band-pass with soft saturation.
6. Peak-normalize, then ffmpeg `loudnorm` to −16 LUFS (podcast) or −23 LUFS
   (broadcast), 48 kHz stereo. Export WAV + 320 kbps MP3.

## Related

- [[entities/drama-llama-app]]
- [[concepts/script-to-stem-pipeline]]
