---
title: Script-to-stem pipeline
tags: [parser, llm, stems, alignment, whisper]
sourceRefs:
  - src/drama_studio/parser.py
  - src/drama_studio/llm.py
  - src/drama_studio/stems.py
  - src/drama_studio/aligner.py
lastReviewed: 2026-10-29
---

# Script-to-stem pipeline

## Parse

`parse_script()` is the safe first pass:

- scene headings (`INT./EXT.`, `SCENE n`, `SZENE n`, Markdown headings)
- `NAME:` and `**NAME** *(direction)*` speakers
- Markdown cast tables; short names resolve to full cast names when unambiguous
- `[SFX]` / `[AMBIENCE]` / `[PAUSE]` stage directions
- Markdown emphasis is stripped from spoken text, but `<sigh>` tags are kept
- coverage = dialogue lines / (dialogue + unparsed); metadata/appendix excluded

The optional LLM path asks Ollama for JSON, then validates that every line is a
verbatim normalized substring of the source. Below 85% coverage it raises
`LLMParseError`; the API falls back to the deterministic result and records a
warning.

## Stems

`build_stems()` groups lines by `(scene, speaker)`, joins with `" ... "`, and
generates a scene-level instruction from the character description, scene
heading and per-line beats. Existing takes survive a rebuild only when the stem
text and instruction are unchanged.

## Alignment

A stem is one continuous performance. `WhisperAligner` (CPU int8,
large-v3) produces word timestamps; the vendored `align_cuts()` sequence-matches
script words to ASR words and returns line cut points plus per-line confidence.
Line clips are written to `lines/<line_id>.wav`.

## Related

- [[entities/breeze-engine]]
- [[entities/chatterbox-engine]]
- [[sources/kepler-stem-pipeline]]
