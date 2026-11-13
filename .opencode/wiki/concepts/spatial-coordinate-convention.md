---
title: Spatial coordinate convention
tags: [spatial, coordinates, ambisonic, ambiroomsim, sparta]
sourceRefs:
  - src/drama_studio/spatial/spatial_shim.c
  - src/drama_studio/spatial.py
  - src/drama_studio/models.py
  - web/src/components/StageBoard.tsx
lastReviewed: 2026-11-13
---

# Spatial coordinate convention

Three coordinate systems are in play, and they do not agree. Getting this wrong
does not crash anything — it silently pans the cast backwards or mirrors the
stage, which is why it is written down.

## Stage space (what is stored)

Used by `SceneStage`, `project.json`, `stage.json` and the Stage UI. Chosen to
match SPARTA's studio so a scene can round-trip.

```
x  width   0 .. W     +x is the listener's LEFT at the default facing
y  depth   0 .. D     the FRONT WALL is at y = 0, back wall at y = D
z  height  0 .. H
facing     azimuth, degrees: 0 faces the front wall (-y), +90 faces +x,
           ±180 faces the back wall (+y)
```

So the unit vector a facing azimuth `az` points along is `(sin az, -cos az, 0)`.
Heights are stored but only editable numerically — the Stage UI is top-down.

## SAF's Ambisonic space (what the decoder assumes)

```
+x = front, +y = left, +z = up
```

This is the SOFA/SAF convention and is *not* the stage convention. A listener
facing stage-azimuth `az` must therefore see the field rotated by `90 - az`:

| facing | rotation |
|---|---|
| 0 (front wall) | +90° |
| +90 (+x) | 0° |
| ±180 (back wall) | −90° |

`dl_build_yaw_matrix` builds that `Rz(90 - az)` and feeds it to
`getSHrotMtxReal`, mirroring what the plug-in's receiver-facing rotation does.
Rotate the *field*, not the source positions: rotating positions leaves the room
walls behind and gets the reflections wrong.

## Inside `ims_shoebox`

`ims_shoebox_computeEchograms` flips y itself before computing image sources:

```c
rec2.y = sc->room_dims[1] - sc->recs[rec_idx].pos.y;
src2.y = sc->room_dims[1] - sc->srcs[src_idx].pos.y;
```

**Never pre-flip a position.** Doing it twice puts every actor at the wrong depth
and reads as a room that sounds fine but has the cast behind the wall. Positions
go to `ambi_roomsim_setSourceX|Y|Z` in stage space, unchanged.

## Channel order and normalisation

The shim always encodes **ACN / N3D** internally: that is what SAF works in, and
what `getLoudspeakerDecoderMtx` expects. `dl_to_sn3d` (`convertHOANormConvention`)
converts to **ACN / SN3D** — AmbiX — only when writing
`mix/master_ambisonic.wav`, so the file is importable by the usual Ambisonic
tooling. Never let the stereo decode path see an SN3D buffer; the omni and the
dipoles would be scaled inconsistently.

## Checking it

`scripts/spike_spatial.py` is the executable version of this page. It asserts
that a source toward the front wall decodes centred (balance ≈ 0), that `+x` leans left and `-x` right, and that facing the listener to `+90` recentres the
`+x` source. Any change to the rotation or the flip must keep those true.
`tests/test_spatial.py` runs the same checks as unit tests.

## Related

- [[concepts/spatial-stage-rendering]] — the pipeline this feeds.
- [[entities/drama-llama-app]] — where the stage is stored.
