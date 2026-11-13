from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np

from .audio import peak_normalize, read_mono, slice_mono, write_mono
from .transcriber import TranscriptResult, TranscriptWord, WhisperTranscriber

SR = 24000
MIN_INPUT_S = 6.0
# Longer uploads are accepted and automatically reduced to a 6-11 s reference
# window. Two minutes keeps the CPU transcription bounded while covering most
# interview/recording excerpts.
MAX_INPUT_S = 120.0
# Above this, surface a "long clip" advisory in the UI and on the voice.
LONG_INPUT_S = 60.0
MIN_REF_S = 6.0
MAX_REF_S = 11.0
TARGET_REF_S = 8.0
# Leave room for the silence trim: selecting exactly 6.0 s can trim below the
# Breeze minimum, so the first pass prefers a small safety margin.
SELECT_MIN_S = 6.5
SELECT_MAX_S = 10.5


class ReferencePreparationError(Exception):
    """Bad input or an unusable clip; the job fails permanently."""


class NoUsableWindowError(ReferencePreparationError):
    pass


class ReferenceTooShortError(ReferencePreparationError):
    pass


class NoSpeechError(ReferencePreparationError):
    pass


class ReferenceCancelled(Exception):
    pass


@dataclass
class PreparedReference:
    file: str
    duration_s: float
    sample_rate: int
    f0_hz: float | None
    f0_range_hz: tuple[float, float] | None
    ref_text: str
    transcript_confidence: float | None
    window_start_s: float
    window_end_s: float
    warnings: list[str]


def _report(
    progress: Callable[[float, str], None] | None,
    value: float,
    message: str,
) -> None:
    if progress is not None:
        progress(value, message)


def _check_cancel(cancelled: Callable[[], bool] | None) -> None:
    if cancelled is not None and cancelled():
        raise ReferenceCancelled("reference generation cancelled")


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def probe_duration(path: str | Path) -> float:
    """Return audio duration in seconds using soundfile or ffprobe."""
    source = Path(path)
    try:
        import soundfile as sf

        info = sf.info(str(source))
        if info.frames and info.samplerate:
            return float(info.frames) / float(info.samplerate)
    except Exception:
        pass
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(source),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return float(result.stdout.strip())
    except FileNotFoundError as exc:
        raise ReferencePreparationError(
            "ffprobe is not available; install ffmpeg to read audio durations"
        ) from exc
    except (subprocess.CalledProcessError, ValueError) as exc:
        raise ReferencePreparationError(
            f"could not read the duration of {source.name}: {exc}"
        ) from exc


def decode_to_24k(
    input_path: str | Path,
    output_path: str | Path,
    *,
    timeout_s: float = 120,
) -> Path:
    """Decode any ffmpeg-readable file to 24 kHz mono PCM-16 WAV."""
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(input_path),
                "-vn",
                "-map",
                "0:a:0",
                "-ac",
                "1",
                "-ar",
                str(SR),
                "-c:a",
                "pcm_s16le",
                str(destination),
            ],
            check=True,
            capture_output=True,
            timeout=timeout_s,
        )
    except FileNotFoundError as exc:
        raise ReferencePreparationError(
            "ffmpeg is not available; install it before generating a reference"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ReferencePreparationError(
            "ffmpeg timed out while decoding the uploaded clip"
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise ReferencePreparationError(
            "ffmpeg could not decode the uploaded clip"
            + (f": {detail.splitlines()[-1]}" if detail else "")
        ) from exc
    return destination


def _speech_seconds(
    words: Sequence[TranscriptWord], start: float, end: float, index_range: range
) -> float:
    total = 0.0
    for index in index_range:
        word = words[index]
        overlap = min(end, word.end) - max(start, word.start)
        if overlap > 0:
            total += overlap
    return total


def select_window(
    words: Sequence[TranscriptWord],
    total_duration: float,
    *,
    target_s: float = TARGET_REF_S,
    min_s: float = MIN_REF_S,
    max_s: float = MAX_REF_S,
) -> tuple[float, float]:
    """Pick a 6-11 s span of contiguous words for Breeze cloning.

    The first pass uses a 6.5-10.5 s margin so the silence trim cannot take the
    result below the 6 s minimum. If no candidate fits, a second pass allows the
    full 6-11 s range. There is deliberately no silence-padding fallback.
    """
    usable = [word for word in words if word.end > word.start]
    if not usable:
        raise NoSpeechError("the clip contains no recognisable speech")
    usable.sort(key=lambda word: (word.start, word.end))

    def best_in_bounds(lower: float, upper: float) -> tuple[float, float] | None:
        best: tuple[float, float] | None = None
        best_key: tuple[float, float, float, float, float] | None = None
        count = len(usable)
        for first in range(count):
            if usable[first].start >= total_duration:
                break
            for last in range(first, count):
                start = max(0.0, usable[first].start - 0.10)
                end = min(total_duration, usable[last].end + 0.10)
                duration = end - start
                if duration < lower:
                    continue
                if duration > upper:
                    break
                speech = _speech_seconds(
                    usable, start, end, range(first, last + 1)
                )
                voiced_ratio = speech / duration if duration > 0 else 0.0
                gap = max(0.0, duration - speech)
                key = (
                    0.0 if 7.0 <= duration <= 9.0 else 1.0,
                    abs(duration - target_s),
                    -voiced_ratio,
                    gap,
                    start,
                )
                if best_key is None or key < best_key:
                    best_key = key
                    best = (start, end)
        return best

    selected = best_in_bounds(SELECT_MIN_S, SELECT_MAX_S)
    if selected is None:
        selected = best_in_bounds(min_s, max_s)
    if selected is None:
        longest = max(
            (word.end - word.start for word in usable), default=0.0
        )
        raise NoUsableWindowError(
            f"no continuous {min_s:.0f}-{max_s:.0f} s speech window was found "
            f"(longest utterance is {longest:.1f} s); upload a longer clean clip"
        )
    return selected


def trim_and_normalize(
    samples: np.ndarray,
    sample_rate: int,
    start_s: float,
    end_s: float,
) -> np.ndarray:
    """Slice a window, trim leading/trailing silence and peak-normalise it."""
    clip = slice_mono(samples, sample_rate, start_s, end_s)
    if clip.size == 0:
        raise ReferenceTooShortError("the selected window contains no audio")

    import librosa

    trimmed, _ = librosa.effects.trim(
        clip, top_db=30, frame_length=2048, hop_length=512
    )
    if len(trimmed) < 0.5 * sample_rate:
        trimmed = clip

    duration = len(trimmed) / sample_rate
    if duration < MIN_REF_S:
        raise ReferenceTooShortError(
            f"the selected clip is {duration:.1f} s after trimming; Breeze "
            f"needs at least {MIN_REF_S:.0f} s"
        )
    if duration > MAX_REF_S:
        raise ReferenceTooShortError(
            f"the selected clip is {duration:.1f} s after trimming; Breeze "
            f"accepts at most {MAX_REF_S:.0f} s"
        )
    return peak_normalize(trimmed, peak=0.95).astype(np.float32)


def measure_f0(
    samples: np.ndarray, sample_rate: int
) -> tuple[float | None, tuple[float, float] | None]:
    """Return median F0 and the p10/p90 range over voiced frames.

    ``yin`` is the primary estimator because it still returns a value on
    noisy/clipped dialogue where ``pyin``'s voiced-probability gate can reject
    every frame. Fall back to ``pyin`` only if yin fails.
    """
    import librosa

    data = np.asarray(samples, dtype=np.float32)
    if data.ndim > 1:
        data = data.mean(axis=1)
    try:
        f0 = librosa.yin(
            data,
            fmin=60,
            fmax=300,
            sr=sample_rate,
            frame_length=2048,
        )
    except Exception:
        f0 = np.array([], dtype=float)
    values = f0[~np.isnan(f0)] if len(f0) else f0
    if len(values) == 0:
        try:
            f0, _voiced, _ = librosa.pyin(
                data,
                fmin=60,
                fmax=300,
                sr=sample_rate,
                frame_length=2048,
            )
        except Exception:
            return None, None
        values = f0[~np.isnan(f0)]
    if len(values) == 0:
        return None, None
    median = float(np.median(values))
    low = float(np.percentile(values, 10))
    high = float(np.percentile(values, 90))
    return median, (low, high)


def _validate_transcript(
    transcript: TranscriptResult, *, label: str
) -> None:
    if not transcript.words or not transcript.text.strip():
        raise NoSpeechError(f"{label} contains no recognisable speech")
    if (
        transcript.no_speech_prob is not None
        and transcript.no_speech_prob >= 0.6
        and (transcript.confidence or 0.0) < 0.5
    ):
        raise NoSpeechError(
            f"{label} looks like music/noise rather than speech "
            f"(no-speech probability {transcript.no_speech_prob:.2f})"
        )


def prepare_reference(
    input_path: str | Path,
    output_path: str | Path,
    *,
    language: Literal["en", "de"],
    progress: Callable[[float, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    transcriber: WhisperTranscriber | None = None,
) -> PreparedReference:
    """Build a Breeze-ready reference WAV from an uploaded speech clip.

    This is intentionally CPU-only: no TTS model is loaded and no GPU memory is
    touched. The final transcript is produced from the trimmed clip, not from
    the original segmentation, so it matches the audio Breeze will clone.
    """
    source = Path(input_path)
    destination = Path(output_path)
    if not source.is_file():
        raise ReferencePreparationError(f"uploaded clip not found: {source.name}")

    total_duration = probe_duration(source)
    if total_duration < MIN_INPUT_S:
        raise ReferencePreparationError(
            f"the uploaded clip is {total_duration:.1f} s; upload a "
            f"single-speaker sample of at least {MIN_INPUT_S:.0f} s"
        )
    if total_duration > MAX_INPUT_S:
        raise ReferencePreparationError(
            f"the uploaded clip is {total_duration / 60:.1f} minutes; "
            "the automatic cutter accepts at most 2 minutes"
        )

    _check_cancel(cancelled)
    destination.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    decoded = destination.parent / f".decode-{token}.wav"
    final_tmp = destination.parent / f".ref-{token}.wav"

    try:
        _report(progress, 0.05, "decoding audio")
        decode_to_24k(source, decoded)
        samples, sample_rate = read_mono(decoded)
        if sample_rate != SR:
            raise ReferencePreparationError(
                f"internal decode error: expected {SR} Hz, got {sample_rate} Hz"
            )

        _check_cancel(cancelled)
        _report(progress, 0.20, "transcribing clip")
        worker = transcriber or WhisperTranscriber(language=language)
        full = worker.transcribe(samples, sample_rate, word_timestamps=True)
        _validate_transcript(full, label="the uploaded clip")

        _check_cancel(cancelled)
        _report(progress, 0.40, "selecting best speech window")
        start_s, end_s = select_window(full.words, total_duration)

        _check_cancel(cancelled)
        _report(progress, 0.55, "trimming and normalising")
        raw_clip = slice_mono(samples, sample_rate, start_s, end_s)
        source_peak = (
            float(np.max(np.abs(raw_clip))) if raw_clip.size else 0.0
        )
        clip = trim_and_normalize(samples, sample_rate, start_s, end_s)
        write_mono(final_tmp, clip, SR, subtype="PCM_16")

        _check_cancel(cancelled)
        _report(progress, 0.70, "checking final transcript")
        final_samples, final_sr = read_mono(final_tmp)
        final = worker.transcribe(final_samples, final_sr, word_timestamps=True)
        _validate_transcript(final, label="the trimmed reference")

        _report(progress, 0.85, "measuring pitch")
        f0, f0_range = measure_f0(final_samples, final_sr)
        duration = len(final_samples) / final_sr

        warnings: list[str] = []
        if final.confidence is not None and final.confidence < 0.80:
            warnings.append(
                "Whisper confidence is low; listen and edit the transcript "
                "before using this voice."
            )
        if final.no_speech_prob is not None and final.no_speech_prob >= 0.5:
            warnings.append(
                "Whisper thinks part of the clip may not be speech; a cleaner "
                "upload is recommended."
            )
        if f0 is not None and (f0 < 80.0 or f0 > 260.0):
            warnings.append(
                f"Measured F0 is {f0:.0f} Hz, outside the usual 80-260 Hz "
                "range; cloning may sound different from the source."
            )
        if source_peak >= 0.999:
            warnings.append(
                "The source clip may be clipped; a cleaner upload is recommended."
            )
        if duration < 7.0:
            warnings.append(
                f"The selected window is only {duration:.1f} s; a longer clean "
                "passage may clone better."
            )
        if total_duration > LONG_INPUT_S:
            warnings.append(
                f"The source clip is {total_duration:.0f} s long; only the best "
                "6-11 s window was kept. Long clips take longer to transcribe."
            )

        _check_cancel(cancelled)
        _report(progress, 0.95, "writing reference")
        os.replace(final_tmp, destination)
        return PreparedReference(
            file=str(destination),
            duration_s=duration,
            sample_rate=final_sr,
            f0_hz=f0,
            f0_range_hz=f0_range,
            ref_text=final.text,
            transcript_confidence=final.confidence,
            window_start_s=start_s,
            window_end_s=end_s,
            warnings=warnings,
        )
    finally:
        _safe_unlink(decoded)
        _safe_unlink(final_tmp)
