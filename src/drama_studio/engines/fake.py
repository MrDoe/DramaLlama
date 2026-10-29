from __future__ import annotations

import hashlib
import re
from pathlib import Path

import numpy as np

from ..audio import sha256_text, write_mono
from ..models import (
    ReferenceRequest,
    ReferenceResult,
    StemRenderRequest,
    StemRenderResult,
    Take,
)
from .base import BaseEngine, CancelCheck, EngineInfo, ProgressCallback


def _slug(value: str, limit: int = 48) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()
    return text[:limit] or "stem"


def _voice_frequency(speaker: str) -> float:
    digest = hashlib.sha256(speaker.encode("utf-8")).digest()
    # Stable per-speaker pitch in a plausible dialogue range.
    return 95.0 + (int.from_bytes(digest[:2], "big") % 110)


def _synthesize(text: str, frequency: float, seed: int, sample_rate: int) -> np.ndarray:
    words = max(1, len(text.split()))
    duration = max(0.8, words / 2.6)
    frames = int(duration * sample_rate)
    t = np.arange(frames, dtype=np.float32) / sample_rate
    rng = np.random.default_rng(seed)

    envelope = np.full(frames, 0.55, dtype=np.float32)
    for index in range(words):
        start = int(index / words * frames)
        end = max(start + 1, int((index + 1) / words * frames))
        window = np.hanning(max(2, end - start)).astype(np.float32)
        envelope[start:end] *= window[: end - start]

    signal = (
        0.55 * np.sin(2 * np.pi * frequency * t)
        + 0.20 * np.sin(2 * np.pi * frequency * 2 * t)
        + 0.08 * np.sin(2 * np.pi * frequency * 3 * t)
    )
    signal *= 0.75 + 0.25 * np.sin(2 * np.pi * 3.1 * t)
    signal += rng.normal(0, 0.004, frames)
    return np.clip(signal * envelope * 0.45, -1.0, 1.0).astype(np.float32)


class FakeEngine(BaseEngine):
    """Deterministic, GPU-free engine used by tests and UI development."""

    info = EngineInfo(
        name="fake",
        languages=frozenset({"en", "de"}),
        sample_rate=24000,
        supports_reference_clone=True,
        voice_mode="reference",
        description="Sine/noise test engine. No model, no GPU.",
    )

    def design_reference(self, request: ReferenceRequest) -> ReferenceResult:
        self.load()
        frequency = _voice_frequency(request.character_id)
        audio = _synthesize(request.text, frequency, request.seed, self.info.sample_rate)
        path = write_mono(request.output_path, audio, self.info.sample_rate)
        return ReferenceResult(
            file=str(path),
            duration_s=len(audio) / self.info.sample_rate,
            sample_rate=self.info.sample_rate,
            f0_hz=frequency,
        )

    def render_stem(
        self,
        request: StemRenderRequest,
        *,
        progress: ProgressCallback | None = None,
        cancelled: CancelCheck | None = None,
    ) -> StemRenderResult:
        self.load()
        if cancelled and cancelled():
            raise RuntimeError("cancelled")

        output_dir = Path(request.output_dir)
        take_dir = output_dir / "audio" / "takes"
        base = _voice_frequency(request.speaker)
        takes: list[Take] = []

        for index in range(request.candidates):
            if cancelled and cancelled():
                raise RuntimeError("cancelled")
            if progress:
                progress(index / request.candidates, f"fake take {index + 1}/{request.candidates}")

            # Slight detune makes take selection meaningful in tests.
            offset = (index - (request.candidates - 1) / 2) * 0.01
            frequency = base * (1.0 + offset)
            audio = _synthesize(
                request.text,
                frequency,
                request.seed + index,
                self.info.sample_rate,
            )
            filename = (
                f"{_slug(request.stem_id)}_{request.seed}_{index}"
                f"_{sha256_text(request.text)[:8]}.wav"
            )
            path = write_mono(take_dir / filename, audio, self.info.sample_rate)
            takes.append(
                Take(
                    id=f"{request.stem_id}-{request.seed}-{index}",
                    file=str(path.relative_to(output_dir)),
                    duration_s=len(audio) / self.info.sample_rate,
                    sample_rate=self.info.sample_rate,
                    seed=request.seed + index,
                    f0_hz=round(frequency, 2),
                    mfcc_sim=round(1.0 - abs(index - 0) * 0.01, 4),
                    selected=False,
                )
            )

        best = max(takes, key=lambda take: take.mfcc_sim or 0.0)
        best.selected = True
        if progress:
            progress(1.0, "fake stem complete")
        return StemRenderResult(
            stem_id=request.stem_id,
            engine=self.info.name,
            sample_rate=self.info.sample_rate,
            takes=takes,
            selected_take_id=best.id,
            text_sha256=sha256_text(request.text),
        )
