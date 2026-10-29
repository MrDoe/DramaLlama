# Vendored Breeze stem module

Pinned copy of the stem renderer and Whisper aligner from the sibling
`~/tts_test` session (Kepler). Do not edit these files; send fixes upstream and
re-vendor with new hashes.

| Field | Value |
|---|---|
| Source path | `~/tts_test/breeze_stems.py`, `~/tts_test/breeze_drama.py` |
| Vendored on | 2026-10-29 |
| `breeze_stems.py` SHA-256 | `0c44f6f62961c9d8af855c9e915b3b9cbffae3fe5235531ff56f318e0e068dd3` |
| `breeze_drama.py` SHA-256 | `b3780524654d744150ae0d46d3cb538dea28f41d30fa5936baa780cbcc83c3d2` |
| Breeze repo | `~/code/breeze-tts` |
| Breeze repo commit | `6eba901d1845353802652c708a4740fdfc5baa75` |
| Breeze model revision | `3e28c5151381a722f1d8661b4118c298caa77aa4` |
| License | Apache 2.0 inference code; Breeze weights/outputs research / non-commercial |

## Validation

Kepler's full render: 27 lines, 89.38 s dialogue, 94.38 s with SFX. Mean WER
0.087, median 0.000; per character Mara 0.011, Mother 0.000, Elias 0.184
(deep 85 Hz voice is ASR-hard on short lines; cuts were checked individually).

## Contract

- `align_cuts(stem, sr, lines, asr, language="en") -> (cuts, confidences)`
- `norm_words(text, fold_umlauts=False)` is Unicode-safe; German folds
  ä→ae, ö→oe, ü→ue, ß→ss on both script and ASR words.
- `align_conf` is returned by the current `align_cuts`; the validated manifest
  predates the confidence edit. The aligner logic is otherwise unchanged.
- Breeze generation remains English/Chinese; German audio is aligned from the
  Qwen3 engine with the same cut logic.

## Local adapter policy

- `src/drama_studio/engines/breeze.py` imports `synth`, `align_cuts`,
  `convert_tags`, `find_asr` from this directory.
- Never import `vendor/` from the web process; only the GPU worker does.
- App-specific instructions, refs and stem grouping live in the adapter, not
  here.
