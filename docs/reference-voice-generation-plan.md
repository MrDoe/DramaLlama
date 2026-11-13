# Plan: Reference Voice Generation in Drama Llama

## Goal

Let a user turn an uploaded audio clip (or a pasted text description) into a
usable **Breeze reference voice** without leaving the app, instead of manually
transcribing, trimming and normalising clips in an external script. The output
must be a 6–11 s, 24 kHz mono WAV plus a verbatim transcript that Breeze can
clone from with WER ≈ 0.

## Scope

- **In scope:** Breeze (English) and Breeze-DE reference generation from an
  uploaded audio sample; reuse the existing text-based `design_reference` path.
- **Out of scope (v1):** Chatterbox (it already clones directly from an
  uploaded sample), Suno (preset voices), and automatic voice *design* from a
  free-form description beyond the existing `design_reference` flow.

## User-facing flow

1. On a character card, three actions:
   - **Design from description** (existing): text + instruction → Breeze designs a ref.
   - **Generate from sample** (new): upload 6–30 s of speech → app transcribes,
     trims, normalises and stores a `RefVoice`.
   - **Upload raw sample** (existing): stores the file as-is (Chatterbox path).
2. After generation, the new ref appears in the character's reference list with
   name, duration, F0 and a play button for the preview.
3. A warning is shown when the measured F0 is out of the intended register or
   the transcript confidence is low, so the user can regenerate.

## Data model changes

`RefVoice` already carries `ref_text`, `ref_instruction`, `f0_hz`. Add:

- `source: Literal["designed", "generated", "uploaded"] = "uploaded"`
- `duration_s: float | None`
- `transcript_confidence: float | None` (mean Whisper word probability)
- `created_at` (already on takes; optional here for sorting)

No migration needed: new fields have defaults.

## Backend job and engine API

Add a new job type `reference.generate` (mirroring `reference.design`) and an
engine method:

```python
def generate_reference(self, request: GenerateReferenceRequest) -> ReferenceResult
```

`GenerateReferenceRequest`:

```python
class GenerateReferenceRequest(BaseModel):
    character_id: str
    input_path: str          # uploaded clip (any ffmpeg-decodable format)
    output_path: str
    name: str | None = None
    target_duration_s: float = 8.0   # 6-11 window
    language: Literal["en", "de"] = "en"
```

Handler `generate_reference(job, ctx)` mirrors `design_reference`:

1. Load project/character.
2. Resolve engine; require Breeze or Breeze-DE.
3. `_require_gpu`.
4. Convert input to 24 kHz mono via ffmpeg (already done on upload; re-do to
   be safe).
5. Call `engine.generate_reference(request)` inside progress callbacks.
6. Transcribe the trimmed clip with Whisper on CPU (`faster-whisper` large-v3,
   int8) to obtain `ref_text` and confidence.
7. Store `RefVoice` with `source="generated"`, `ref_text`, `f0_hz`, duration.
8. Save project.

Job registration in `handlers.JOB_HANDLERS` as `"reference.generate"`.

## Audio processing pipeline (engine side)

Implement in `BreezeEngine` (and `BreezeDeEngine` where applicable):

1. **Decode** input to 24 kHz mono float (`_read_mono`, already present).
2. **VAD + segmentation** with Whisper word timestamps (`vad_filter=False` to
   avoid dropping edge words; we saw VAD drop leading words).
3. **Window selection**: enumerate consecutive Whisper segments, pick the
   combination with duration in `[6.0, 11.0]` s that maximises voiced ratio and
   minimises internal gaps; prefer 7–9 s. Fall back to the single longest
   segment padded with silence only if nothing fits (should be rare).
4. **Trim** leading/trailing silence (`librosa.effects.trim`, top_db=30).
5. **Peak-normalise** to 0.95.
6. **Transcribe** the final trimmed clip with Whisper (beam_size=5,
   `vad_filter=False`, `condition_on_previous_text=False`) to get the verbatim
   `ref_text`. Use this transcript, not the original segmentation text, so it
   matches the audio byte-for-byte.
7. **Validate**:
   - duration in [6, 11] after trim;
   - Whisper mean word probability ≥ 0.8 (warn otherwise);
   - F0 in a sane range for the engine (Breeze: 80–260 Hz; warn outside);
   - no clipping (peak ≤ 0.99).
   Return warnings in `ReferenceResult` (add `warnings: list[str]`).
8. **Write** WAV (PCM_16, 24 kHz) via `write_mono`.

Whisper runs on CPU while Breeze holds the GPU; do not load Breeze and Whisper
on GPU at the same time. The handler should call Whisper *before* `engine.load()`
or after `engine.unload()`. Simplest: do transcription in the handler using a
small helper, then pass `ref_text` into `generate_reference`.

## API endpoints

Add alongside the existing upload endpoints:

- `POST /api/projects/{id}/characters/{speaker}/reference/generate`
  - multipart: `file`, optional `name`, optional `language`.
  - Returns 202 with a job id (`reference.generate`).
- Optional synchronous preview endpoint for the frontend waveform (not required
  for v1).

Reuse the existing upload temp-file + ffmpeg conversion logic; store the
generated ref under `refs/<speaker>-<id>.wav`.

## Frontend changes

- Character card: add **Generate from sample** button next to **Design** and
  **Upload**.
- Modal: file picker, optional name, language (inherited from project),
  progress bar (job polling like other jobs), then preview player + F0/duration
  + warnings.
- Show transcript in an editable text area so the user can correct Whisper
  errors before saving (important: Breeze needs an exact match).
- On save, optionally allow re-generating if the user edits the transcript
  (re-run only the trim/normalise step? For v1, re-run full pipeline or just
  accept edited text).

## Tests

- Unit tests for the window-selection and trim/normalise helpers (pure
  functions, no GPU).
- `test_reference_generate_job` with the fake engine stubbed to return a canned
  `ReferenceResult` (like `test_render_voices`).
- API test: upload a short WAV, job enqueued, project updated with new
  `RefVoice` and `source="generated"`.
- No GPU required in CI; mark real-model smoke tests as opt-in.

## Migration and compatibility

- Existing projects keep working: `source` defaults to `"uploaded"`/`"designed"`.
- `design_reference` unchanged; `generate_reference` is additive.
- The frontend falls back to the current upload-only UI if the endpoint is
  missing (older backend).

## Risks and mitigations

- **Whisper accuracy on noisy clips**: show transcript for editing; warn on low
  confidence; allow manual override.
- **GPU memory**: keep Whisper on CPU; never overlap with Breeze load.
- **Clip too short/long**: reject < 6 s after trim with a clear error; for
  > 11 s, auto-select a sub-window and tell the user which seconds were used.
- **Octave errors in F0** (seen with shouted audio): use `librosa.yin` and
  report a range, not a single value; treat F0 as advisory only.
- **Licensing**: Breeze weights are research/non-commercial; the generated ref
  inherits this. Surface in UI.

## Milestones

1. **M1 – helpers + tests** (1–2 days): pure functions for decode, window
   selection, trim, normalise, transcribe; unit tests.
2. **M2 – engine method** (1 day): `BreezeEngine.generate_reference` + model
   changes.
3. **M3 – job + API** (1 day): `reference.generate` handler, endpoint, job
   registration.
4. **M4 – frontend** (2 days): modal, progress, preview, transcript edit.
5. **M5 – Breeze-DE + polish** (1–2 days): port to sidecar, warnings, docs.

## Reuse from tts_test

The implementation should reuse the proven logic in
`~/tts_test/make_breeze_voices.py`:
- ffmpeg → 24 kHz mono decode;
- Whisper word-timestamp window selection (consecutive segments, 6–11 s);
- `librosa.effects.trim` + peak normalise;
- transcribe the *final* clip for `ref_text` (not the original segmentation).
- F0 via `librosa.yin`.
