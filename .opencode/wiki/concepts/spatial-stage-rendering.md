---
title: Spatial stage rendering
tags: [spatial, staging, ambisonic, roomsim, saf, mixer, stage]
sourceRefs:
  - src/drama_studio/spatial.py
  - src/drama_studio/spatial/spatial_shim.c
  - src/drama_studio/spatial/CMakeLists.txt
  - src/drama_studio/mixer.py
  - src/drama_studio/models.py
  - web/src/components/StageBoard.tsx
  - scripts/build_spatial.py
  - scripts/spike_spatial.py
lastReviewed: 2026-11-13
---

# Spatial stage rendering

Drama Llama's Stage step places a scene's cast in a shoebox room and renders the
dialogue through a real image-source acoustic model. This page covers why the
integration is shaped the way it is, and the traps in it.

## The plug-in is not the integration

[SPARTA](../../SPARTA) AmbiRoomSim is a JUCE VST2/VST3/LV2 plug-in under **GPLv3**,
and it is realtime-only — the standalone build has play/stop and no file export
anywhere in `StudioShell`. There is therefore no way to invoke the plug-in to
render a mix, and linking its code would copy-left the app.

What is reusable is the algorithm underneath it. `ambi_roomsim` lives in the
Spatial Audio Framework examples and is **ISC**, as is the `saf_reverb` module
that implements `ims_shoebox`. The C API is small and flat:

```c
ambi_roomsim_create/init/process/destroy
ambi_roomsim_setSourceX|Y|Z(h, index, metres)   // 1..16 sources
ambi_roomsim_setReceiverX|Y|Z                   // 1..16 SH receivers
ambi_roomsim_setRoomDimX|Y|Z, setWallAbsCoeff(h, xyz, posneg, v)
ambi_roomsim_setOutputOrder(h, SH_ORDER_THIRD)  // (order+1)^2 channels
```

So Drama Llama links **SAF only** through its own shim
(`src/drama_studio/spatial/spatial_shim.c`) and shares nothing but a coordinate
convention with the GPLv3 plug-in. See [[concepts/spatial-coordinate-convention]].

The DSP chain, per frame: `ims_shoebox_updateSource` (dirty-flags a moved
source) → `computeEchograms` (only for dirty source/receiver pairs) →
`applyEchogramTD` (sparse reads from a circular buffer, crossfaded when the
echogram changed).

## Where it sits in the mix

`build_mix` in [[entities/drama-llama-app]]'s `mixer.py` used to sum every line
clip into one mono buffer and let ffmpeg duplicate it to stereo. Now:

```
line clips (24k mono) --fx--> per-(scene,speaker) 48k mono
                                    |
                        ambi_roomsim, 128-sample frames
                                    |
                        scene bed (16ch, ACN/N3D)
                                    |
             summed into one ambisonic timeline at scene offsets
                                    |
                  decoded ONCE, SAD + max-rE, virtual speakers ±30°
                                    |
        stereo dialogue + centre dialogue + SFX bed --> loudnorm
                                    └--> master_ambisonic.wav (opt-in)
```

Two decisions matter here.

**Decode once, not per scene.** Rooms differ per scene, but scenes do not
overlap in time, so the scene beds sum cleanly into one timeline and a single
decode is equivalent — and it yields a continuous Ambisonic master for free.

**Staged speakers leave the mono sum.** A line whose scene *and* speaker are
staged goes to a per-scene track; everything else (unstaged scenes, speakers in
a staged scene with no actor slot) stays in the centre `dialogue` buffer. That
is why `MixResult.spatial` exists and why an unstaged scene in a staged project
is still audible rather than silently dropped.

See [[concepts/gpu-serialization]] — staging deliberately does **not** join it.
It is CPU-only at ~0.2 s per audio second, so it never touches the GPU queue
discipline that Breeze, Chatterbox and Ollama are under.

## Degrading gracefully

The hard rule: a missing or failing renderer must never lose a line.

- `spatial.available()` False → the Stage page says so, `build_mix` mixes dry
  and adds a warning.
- Render raises mid-flight → `build_mix` catches `SpatialUnavailable`, folds the
  staged tracks back into the centre and mixes dry.
- `stage is None`, or enabled with no staged actors → the code path is exactly
  what it was before staging existed. Every pre-staging project must mix
  byte-identically; [[tests/test_mixer.py]] guards that.

The legacy 24 kHz mono path is untouched, including the `master_24k.wav`
filename. Spatial projects write `master_pre.wav` instead, at 48 kHz.

## What the room sim cannot do

- **Tail ≤ 0.68 s at 48 kHz.** `IMS_CIRC_BUFFER_LENGTH` is 4·8192 samples of
  source history per band. Intimate rooms are right; a cathedral will not ring.
  Note `ambi_roomsim.c`'s `maxTime_s = -0.05f` is a *sentinel* selecting
  reflection-order mode, not a 50 ms limit — do not "fix" it.
- **Shoebox only.** Parallel walls, no doorways, no L-shaped rooms.
- **Order is fixed at construction.** Changing it rebuilds all image-source
  state, so the shim takes it at `dl_create`.
- **16 sources per scene** (`ROOM_SIM_MAX_NUM_SOURCES`).
- **Bed and SFX are not spatialised** yet; they stay centred. See
  [[concepts/sfx-library-and-mix]].

Motion is re-evaluated on a `MOTION_GRID_S` = 100 ms grid because every position
change recomputes that source's echograms. Benchmarks
(`scripts/bench_spatial.py`):

| order | sources | refl | move | s wall per s audio |
|---|---|---|---|---|
| 1 | 6 | 2 | never | 0.07 |
| 3 | 6 | 2 | never | 0.12 |
| 3 | 6 | 2 | every block | 0.22 |
| 3 | 12 | 2 | 500 ms | 0.24 |
| 3 | 6 | 4 | 500 ms | 0.21 |

## Per-source dry blend

`StageActor.direct` (1 = in the room, 0 = dry and centred) exists for narration.
Because the room sim is linear, a scene where every actor is `direct = 1`
renders in one pass; any partial blend diverts that actor's dry share to a
centred mono track and scales their wet contribution, per block. `all_wet()`
detects the cheap case so the default costs nothing.

## Build

`scripts/build_spatial.py` configures `src/drama_studio/spatial` against a SAF
checkout (auto-detected at `../SPARTA/SDKs/Spatial_Audio_Framework`, overridable
with `--saf` or `DRAMA_LLAMA_SAF_DIR`). It adds only `framework/` and
`examples/`, with `saf_example_list` narrowed to `ambi_roomsim`.
`spatial.py` then probes `DRAMA_LLAMA_SPATIAL_LIB`, `build/spatial/`, an in-tree
build, the venv's `lib/drama-llama/`, and the per-user installer prefix.

Not yet done: prebuilt binaries in CI and `installer/pins.json`, so staging is a
build-it-yourself feature. That is the main packaging gap.

## Verification

`scripts/spike_spatial.py` asserts the physics directly and is the fastest way
to tell a DSP regression from a wiring bug: 3.43 m onset at sample 480 of 48000,
front source balance 0.000, left +0.862, right −0.862, and a left source
recentred to 0.000 by facing +90. `tests/test_spatial.py` mirrors those as
unit tests and skips itself when the library is absent.

## Related

- [[concepts/spatial-coordinate-convention]] — the axes, and the y-flip trap.
- [[concepts/cut-region-effects]] — per-region reverb, which staging replaces
  rather than stacks with.
- [[concepts/script-to-stem-pipeline]] — where the clips come from.
