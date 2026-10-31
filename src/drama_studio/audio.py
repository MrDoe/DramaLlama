from __future__ import annotations

import hashlib
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


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
