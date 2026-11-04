from __future__ import annotations

import hashlib
import warnings
from pathlib import Path

import numpy as np
import soundfile as sf


def write_mono(path: str | Path, samples: np.ndarray, sample_rate: int) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.asarray(samples, dtype=np.float32)
    if data.ndim > 1:
        data = data.mean(axis=1)
    sf.write(str(path), np.clip(data, -1.0, 1.0), sample_rate, subtype="PCM_24")
    return path


def read_mono(path: str | Path) -> tuple[np.ndarray, int]:
    data, sample_rate = sf.read(str(path), always_2d=True, dtype="float32")
    return data.mean(axis=1), int(sample_rate)


def duration_s(path: str | Path) -> float:
    info = sf.info(str(path))
    return float(info.frames) / float(info.samplerate)


def slice_mono(
    samples: np.ndarray, sample_rate: int, start_s: float, end_s: float
) -> np.ndarray:
    """Return the [start_s, end_s) window of a mono buffer.

    Clamps to the buffer; callers that need validation should check the
    duration first so a silently truncated cut cannot slip through.
    """
    start = max(0, int(round(start_s * sample_rate)))
    end = min(len(samples), int(round(end_s * sample_rate)))
    if end <= start:
        return np.zeros(0, dtype=np.float32)
    return np.asarray(samples[start:end], dtype=np.float32)


def peak_normalize(samples: np.ndarray, peak: float = 0.9) -> np.ndarray:
    data = np.asarray(samples, dtype=np.float32)
    current = float(np.max(np.abs(data))) if data.size else 0.0
    if current <= 1e-9:
        return data
    return data * (peak / current)


def rms(samples: np.ndarray) -> float:
    data = np.asarray(samples, dtype=np.float64)
    return float(np.sqrt(np.mean(data**2))) if data.size else 0.0


def _resample(samples: np.ndarray, ratio: float) -> np.ndarray:
    """Linear-interp resample by ``ratio`` (output length = len / ratio).

    ``ratio > 1`` shortens (faster), ``ratio < 1`` lengthens (slower).
    """
    if ratio <= 0:
        return np.asarray(samples, dtype=np.float32)
    n = len(samples)
    if n == 0:
        return np.zeros(0, dtype=np.float32)
    out_len = max(1, int(round(n / ratio)))
    positions = np.linspace(0, n - 1, out_len, dtype=np.float64)
    lo = np.floor(positions).astype(np.int64)
    hi = np.minimum(lo + 1, n - 1)
    frac = positions - lo
    data = np.asarray(samples, dtype=np.float64)
    return (data[lo] * (1.0 - frac) + data[hi] * frac).astype(np.float32)


def _dereverb_wiener(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """Spectral-subtraction-ish dereverb (offline, STFT domain).

    Estimate the room response as an exponentially decaying smooth curve
    fitted to the long-term magnitude spectrum; subtract the smoothed tail
    then gate low-energy bins. Keeps speech intelligible for 0.35–1.2 s IR
    decays; not a watermark-grade removal, but audibly drier.
    """
    data = np.asarray(samples, dtype=np.float64)
    n = len(data)
    if n < 2048:
        return data.astype(np.float32)
    from scipy.signal import get_window, stft, istft

    nperseg = 1024
    win = get_window("hann", nperseg)
    freqs, times, Zxx = stft(data, fs=max(1, sample_rate), window=win, nperseg=nperseg, noverlap=nperseg // 2, padded=True, boundary="zeros")
    mag = np.abs(Zxx)
    # Smooth magnitude over time: reverb tails are persistent, speech isn't.
    # Dilate with a median-ish moving max via rolling mean+max blend.
    smooth_win = max(1, int(0.45 * (sample_rate / (nperseg // 2)) / 2))
    smooth_win = min(smooth_win, max(1, mag.shape[1]))
    kernel = np.ones(smooth_win) / smooth_win
    smooth = np.apply_along_axis(lambda row: np.convolve(row, kernel, mode="same"), 1, mag)
    # Over-subtraction with floor so speech doesn't water-gate to silence.
    alpha = 1.2
    floor = 0.12
    mag_clean = np.maximum(mag - alpha * (smooth - mag * 0.2), mag * floor)
    mag_clean = np.maximum(mag_clean, np.abs(Zxx) * 0.05)
    phase = np.angle(Zxx)
    Z_clean = mag_clean * np.exp(1j * phase)
    _f, _t, restored = istft(Z_clean, fs=max(1, sample_rate), window=win, nperseg=nperseg, noverlap=nperseg // 2, input_onesided=True)
    if len(restored) >= n:
        restored = restored[:n]
    else:
        restored = np.pad(restored, (0, n - len(restored)))
    return restored.astype(np.float32)


def apply_region_effects(
    samples: np.ndarray,
    sample_rate: int,
    *,
    gain_db: float = 0.0,
    fade_in_s: float = 0.0,
    fade_out_s: float = 0.0,
    speed: float = 1.0,
    pitch_cents: int = 0,
    reverb_mix: float = 0.0,
    dereverb: bool = False,
) -> np.ndarray:
    """Render the Cut board's per-region effects onto a mono clip.

    Order: dereverb → reverb → gain → fades → speed (resample) → pitch
    (phase vocoder, so a speed change keeps its perceived pitch and a pitch
    change keeps its duration).

    ``dereverb`` needs scipy; import guarded so the mixer still runs on
    stdlib-only builds (dereverb becomes a no-op there).
    """
    data = np.asarray(samples, dtype=np.float32)
    if data.size == 0:
        return data
    sr = max(1, sample_rate)

    if dereverb:
        try:
            data = _dereverb_wiener(data, sr)
        except Exception:
            pass  # scipy missing or tiny clip — keep original

    # Reverb: exponential-decay noise IR convolver, wet/dry by reverb_mix.
    if reverb_mix > 0:
        wet = max(0.0, min(1.0, float(reverb_mix)))
        ir_len = max(64, int(round(sr * 0.35)))
        rng = np.random.default_rng(7)
        ir = rng.standard_normal(ir_len).astype(np.float32)
        decay = np.exp(-np.linspace(0.0, 6.0, ir_len, dtype=np.float32))
        ir = (ir * decay).astype(np.float32)
        nrm = float(np.sqrt(np.sum(ir**2))) or 1.0
        ir = ir / nrm
        conv = np.convolve(data, ir, mode="same").astype(np.float32)
        data = ((1.0 - wet) * data + wet * conv).astype(np.float32)

    if gain_db:
        data = data * float(10 ** (gain_db / 20.0))

    total = len(data)
    if fade_in_s > 0:
        n = min(total, int(round(fade_in_s * sr)))
        if n > 0:
            ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
            data[:n] = data[:n] * ramp
    if fade_out_s > 0:
        n = min(total, int(round(fade_out_s * sr)))
        if n > 0:
            ramp = np.linspace(1.0, 0.0, n, dtype=np.float32)
            data[-n:] = data[-n:] * ramp

    # Speed (affects duration) — do before pitch correction so pitch won't
    # undo the duration change.
    if speed and speed != 1.0:
        data = _resample(data, float(speed))

    # Pitch: phase-vocoder shift preserves duration. A resample pair would
    # cancel itself out (the two ratios invert), so use librosa when present;
    # minimal installs without the tts extra leave the clip unpitched.
    if pitch_cents:
        cents = max(-1200, min(1200, int(pitch_cents)))
        try:
            import librosa

            with warnings.catch_warnings():
                # librosa 1.0 emits deprecation/numba-cast warnings per call.
                warnings.simplefilter("ignore", FutureWarning)
                warnings.simplefilter("ignore", RuntimeWarning)
                data = librosa.effects.pitch_shift(
                    np.asarray(data, dtype=np.float32),
                    sr=sr,
                    n_steps=cents / 100.0,
                ).astype(np.float32)
        except Exception:
            pass  # librosa missing or clip too short for the STFT

    return data.astype(np.float32)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
