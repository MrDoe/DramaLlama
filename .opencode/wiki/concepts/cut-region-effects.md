---
title: Per-region effects on the Cut board
tags: [cut, effects, dsp, audio, multitrack]
sourceRefs:
  - src/drama_studio/audio.py
  - src/drama_studio/models.py
  - src/drama_studio/mixer.py
  - src/drama_studio/api.py
  - web/src/audio/syncEngine.ts
  - web/src/audio/timeline.ts
  - web/src/components/MultiTrackEditor.tsx
  - web/src/pages/ProjectPage.tsx
  - web/src/types.ts
lastReviewed: 2026-11-04
---

# Per-region effects on the Cut board

Each aligned line (cut region) carries its own effect chain, edited in the
Cut board inspector and applied at mix time. All defaults are neutral so old
projects render unchanged. Implemented Suno-Studio-style: select a clip, then
drag it to move the section or use the inspector sliders.

## Fields (`AlignedLine` / `CutUpdate`)

| Field | Range | Meaning |
|---|---|---|
| `gain_db` | -48..+12 | Per-region gain |
| `fade_in_s` | 0..10 s | Linear ramp up from 0 |
| `fade_out_s` | 0..10 s | Linear ramp down to 0 |
| `speed` | 0.5..2.0 | Playback rate (resampled) |
| `pitch_cents` | -1200..+1200 | Pitch shift (resample pair) |
| `reverb_mix` | 0..1 | Wet amount (convolver, noise IR) |
| `dereverb` | bool | STFT spectral-subtraction dereverb |

## DSP (`apply_region_effects` in `audio.py`)

Order of operations:

1. **Dereverb** — STFT spectral subtraction: estimate the room as a smoothed
   long-term magnitude curve, over-subtract with a floor, reconstruct with
   original phase. Needs scipy; no-op on stdlib-only builds.
2. **Reverb** — exponential-decay noise impulse response convolver, wet/dry
   blend by `reverb_mix`.
3. **Gain** — `10^(gain_db/20)`.
4. **Fades** — linear ramps at head/tail.
5. **Speed** — resample by `speed` (changes duration).
6. **Pitch** — librosa phase-vocoder `pitch_shift` (`n_steps = cents/100`),
   so duration is preserved while the pitch moves. A resample pair would
   cancel itself out; that was a real bug (pitch was a no-op) caught by a
   frequency regression test. Minimal installs without the `tts` extra leave
   the clip unpitched instead of failing the mix.

Speed runs before pitch so pitch correction does not undo the duration change.
The mixer divides clip duration by `speed` for placement/total length; the
timeline does the same in `effectDuration()`.

## Preview vs mix

- The mixer applies effects when rebuilding the mix (`build_mix`).
- `SyncEngine` previews them on the WebAudio clock: speed via
  `playbackRate`, gain/fades/reverb in the node graph. Pitch-only preview is
  approximate (a playback-rate tweak), and dereverb has no live WebAudio
  equivalent — the rendered mix is the source of truth.
- `playRegion()` (inspector Play button) passes the same effect fields as the
  main transport, so auditioning a selected region reflects its chain.

## Persisting

Effects travel with `CutUpdate` through `PATCH .../alignment` and
`PATCH .../alignments` (batch), and both endpoints copy every field into
`AlignedLine` (a bug fixed after the first implementation dropped them).
The `align-script` endpoint re-sorts positions without touching effects.

Save semantics:

- A full cut body replaces the segment; omitted effect fields fall back to
  neutral defaults, so clients must send the fields they want to keep.
- Omitted `position_s` is **not** written as 0. Both PATCH endpoints keep
  `None` and then run `layout_script_order(overwrite_positions=False)` so a
  legacy/partial save keeps global script order instead of stacking stems at
  0. Covered by `test_alignment_omitted_position_follows_script_order`.
- `ProjectPage`'s `saveCuts`/`saveAllCuts` now rethrow after showing the
  error; `MultiTrackEditor` keeps the stem dirty on failure. Previously the
  `void` wrappers cleared the dirty state before the PATCH finished, so
  `Save all (0)` appeared while the mix was still rebuilding.

## Verification notes

Live check on a scratch copy (`wiki-cut-fx-test-1a2b3c`) in Firefox:

- Align-to-script produced strictly increasing positions across all three
  stems (0, 25.93, 39.65, …), fixing the legacy per-stem overlap at 0.
- LMB drag moved `s1-02` from 25.93 s to 47.1 s and a second drag to 68.27 s;
  Save-all roundtrip took ~5.8 s and ended with `mix_stale: false`.
- Inspector set volume +6 dB, fade 0.3/0.4 s, speed 150 %, pitch -300 ¢,
  reverb 40 %, dereverb on; all seven values persisted and the mix rebuilt in
  ~6.3 s.
- Inspector Play changed to Stop with no error (trusted click).
- Local gemma4 (`gemma-4-12b-uncensored`, llama.cpp :8080) correctly heard
  the added reverb and the quieter/faded clip when comparing rendered
  excerpts; the speed A/B answer was wrong, but the processed duration
  (8.0 s → 4.44 s) is verified numerically.
- Pitch shift is verified numerically: a 220 Hz sine at +700 ¢ renders as
  329.5 Hz with unchanged length (`test_region_effects_pitch_shifts_frequency_and_preserves_duration`).

## Related

- [[concepts/cut-board-multitrack]]
- [[concepts/cut-cues-lanes]]
- [[concepts/take-aware-cuts]]
- [[entities/cuts-module]]
