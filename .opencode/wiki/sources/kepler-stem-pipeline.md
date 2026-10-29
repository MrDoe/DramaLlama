---
title: Kepler stem pipeline source
tags: [source, breeze, alignment, vendored]
sourceRefs:
  - vendor/breeze_stems/VENDORED_FROM.md
  - vendor/breeze_stems/breeze_stems.py
  - vendor/breeze_stems/breeze_drama.py
lastReviewed: 2026-10-29
---

# Kepler stem pipeline source

The sibling `~/tts_test` session (Kepler) built and validated the
stem-per-character + Whisper aligner. Drama Studio vendors it read-only.

## Pins

- `breeze_stems.py` SHA-256
  `0c44f6f62961c9d8af855c9e915b3b9cbffae3fe5235531ff56f318e0e068dd3`
- `breeze_drama.py` SHA-256
  `b3780524654d744150ae0d46d3cb538dea28f41d30fa5936baa780cbcc83c3d2`
- Breeze repo commit `6eba901d1845353802652c708a4740fdfc5baa75`
- Breeze model revision `3e28c5151381a722f1d8661b4118c298caa77aa4`

## Contract

`align_cuts(stem, sr, lines, asr, language="en") -> (cuts, confidences)`.
`norm_words(text, fold_umlauts=False)` is Unicode-safe; German folds umlauts on
both script and ASR words.

## Policy

- Never edit `vendor/`; send fixes upstream and re-vendor with new hashes.
- `breeze_drama.py` is used for `synth`, `convert_tags` and `slug`; its
  hardcoded `REFS` are not used by Drama Studio.
- The validated render was 27 lines, 89.38 s dialogue, mean WER 0.087.

## Related

- [[entities/breeze-engine]]
- [[concepts/script-to-stem-pipeline]]
