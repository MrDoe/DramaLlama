from __future__ import annotations

import io
import re
import time
from pathlib import Path

import httpx
import numpy as np
import soundfile as sf

from ..audio import sha256_text, write_mono
from ..config import BREEZE_DE_URL
from ..models import (
    AlignmentResult,
    Line,
    ReferenceRequest,
    ReferenceResult,
    StemRenderRequest,
    StemRenderResult,
    Take,
)
from .base import (
    BaseEngine,
    CancelCheck,
    EngineInfo,
    EngineNotAvailable,
    ProgressCallback,
    UnsupportedOperation,
)


def _safe(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", value).strip("_") or "stem"


def _read_mono(path: Path) -> tuple[np.ndarray, int]:
    import soundfile as sf

    data, sample_rate = sf.read(str(path), always_2d=True, dtype="float32")
    return data.mean(axis=1), int(sample_rate)


def _median_f0(audio: np.ndarray, sample_rate: int) -> float:
    import librosa

    f0, _, _ = librosa.pyin(audio, fmin=60, fmax=300, sr=sample_rate, frame_length=2048)
    values = f0[~np.isnan(f0)]
    return float(np.median(values)) if len(values) else 0.0


def _mfcc(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    import librosa

    return librosa.feature.mfcc(y=audio, sr=sample_rate, n_mfcc=20).mean(axis=1)


def _cosine(a: np.ndarray, b: np.ndarray) -> float | None:
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denominator <= 1e-9:
        return None
    return float(np.dot(a, b) / denominator)


class BreezeDeEngine(BaseEngine):
    """German Breeze via the Breeze-TTS-2.cpp sidecar (Articulatio-DE finetune).

    The sidecar owns the model and the GPU; this adapter only sends text plus
    an optional reference clip and writes the returned PCM/WAV. Voice design is
    supported (instruction only, no reference).
    """

    info = EngineInfo(
        name="breeze-de",
        languages=frozenset({"de"}),
        sample_rate=24000,
        supports_reference_clone=True,
        voice_mode="reference",
        description="Breeze-TTS-2.cpp (Articulatio-DE): German voice clone/design/direction.",
    )

    def __init__(self) -> None:
        super().__init__()
        self.base_url = BREEZE_DE_URL.rstrip("/")

    def load(self) -> None:
        if self._loaded:
            return
        try:
            response = httpx.get(f"{self.base_url}/health", timeout=5)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise EngineNotAvailable(
                f"Breeze-DE sidecar not reachable at {self.base_url}: {exc}"
            ) from exc
        if payload.get("status") != "ok":
            raise EngineNotAvailable(
                f"Breeze-DE sidecar unhealthy at {self.base_url}: {payload!r}"
            )
        self._loaded = True

    def unload(self) -> None:
        # Model lives in the external sidecar process.
        self._loaded = False

    # ---------------------------------------------------------------- helpers

    def _spoken(self, text: str) -> str:
        text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
        text = re.sub(r"__(.+?)__", r"\1", text)
        return re.sub(r"\s+", " ", text).strip()

    def _post(self, data: dict, ref_path: Path | None, timeout: float = 300) -> bytes:
        files = None
        if ref_path is not None:
            files = {"ref_audio": (ref_path.name, ref_path.read_bytes(), "audio/wav")}
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                response = httpx.post(
                    f"{self.base_url}/v1/audio/speech",
                    data=data,
                    files=files,
                    timeout=timeout,
                )
                if response.status_code == 409:
                    time.sleep(1.0 + attempt)
                    continue
                response.raise_for_status()
                return response.content
            except Exception as exc:
                last_exc = exc
                time.sleep(1.0 + attempt)
        if last_exc is not None:
            raise EngineNotAvailable(f"Breeze-DE generation failed: {last_exc}") from last_exc
        raise EngineNotAvailable("Breeze-DE generation failed: busy")

    def _pcm_to_audio(self, content: bytes, content_type: str | None) -> tuple[np.ndarray, int]:
        content_type = (content_type or "").lower()
        if "wav" in content_type or content[:4] == b"RIFF":
            audio, sample_rate = sf.read(io.BytesIO(content), always_2d=True, dtype="float32")
            return audio.mean(axis=1), int(sample_rate)
        # headerless s16le PCM at 24 kHz
        samples = np.frombuffer(content, dtype="<i2").astype(np.float32) / 32768.0
        return samples, 24000

    # -------------------------------------------------------------- reference

    def design_reference(self, request: ReferenceRequest) -> ReferenceResult:
        self.load()
        spoken = self._spoken(request.text)
        data = {
            "text": spoken,
            "instruction": request.instruction or "Speak clearly and naturally.",
            "cfg_scale": str(request.cfg_scale),
            "seed": str(request.seed),
        }
        content = self._post(data, None)
        # Need content type; re-fetch not possible; assume wav/pcm. _post dropped headers.
        # Fall back to pcm detection inside _pcm_to_audio.
        audio, sample_rate = self._pcm_to_audio(content, None)
        path = write_mono(request.output_path, audio, sample_rate)
        f0 = _median_f0(audio, sample_rate)
        return ReferenceResult(
            file=str(path),
            duration_s=len(audio) / sample_rate,
            sample_rate=sample_rate,
            f0_hz=f0,
        )

    # ----------------------------------------------------------------- render

    def render_stem(
        self,
        request: StemRenderRequest,
        *,
        progress: ProgressCallback | None = None,
        cancelled: CancelCheck | None = None,
    ) -> StemRenderResult:
        self.load()
        output_dir = Path(request.output_dir)
        take_dir = output_dir / "audio" / "takes"

        ref_path = None
        if request.ref_audio_path:
            candidate = Path(request.ref_audio_path)
            if not candidate.is_absolute():
                candidate = output_dir / candidate
            if candidate.exists():
                ref_path = candidate

        ref_vec = None
        if ref_path is not None:
            ref_audio, ref_sr = _read_mono(ref_path)
            ref_vec = _mfcc(ref_audio, ref_sr)

        takes: list[Take] = []
        spoken = self._spoken(request.text)
        for index in range(request.candidates):
            if cancelled and cancelled():
                raise RuntimeError("cancelled")
            if progress:
                progress(index / request.candidates, f"Breeze-DE take {index + 1}/{request.candidates}")
            seed = request.seed + index
            data = {
                "text": spoken,
                "instruction": request.instruction or "Speak clearly and naturally.",
                "cfg_scale": str(request.cfg_scale),
                "seed": str(seed),
            }
            if request.ref_text:
                data["ref_text"] = request.ref_text
            content = self._post(data, ref_path)
            audio, sample_rate = self._pcm_to_audio(content, None)
            path = write_mono(
                take_dir / f"{_safe(request.stem_id)}_{seed}_{index}.wav",
                audio,
                sample_rate,
            )
            f0 = _median_f0(audio, sample_rate)
            sim = None
            if ref_vec is not None:
                sim = _cosine(_mfcc(audio, sample_rate), ref_vec)
            takes.append(
                Take(
                    id=f"breeze-de-{request.stem_id}-{seed}-{index}",
                    file=str(path.relative_to(output_dir)),
                    engine=self.info.name,
                    duration_s=len(audio) / sample_rate,
                    sample_rate=sample_rate,
                    seed=seed,
                    f0_hz=round(f0, 1) if f0 else None,
                    mfcc_sim=round(sim, 4) if sim is not None else None,
                    selected=False,
                )
            )

        if not takes:
            raise EngineNotAvailable("Breeze-DE produced no takes")
        best = min(
            takes,
            key=lambda take: (
                (take.mfcc_sim or 0.0) < 0.98,
                -(take.mfcc_sim or 0.0),
            ),
        )
        best.selected = True
        if progress:
            progress(1.0, "Breeze-DE stem complete")
        return StemRenderResult(
            stem_id=request.stem_id,
            engine=self.info.name,
            sample_rate=takes[0].sample_rate,
            takes=takes,
            selected_take_id=best.id,
            text_sha256=sha256_text(spoken),
        )

    # ------------------------------------------------------------------ align

    def align_stem(
        self,
        stem_path: str | Path,
        lines: list[Line],
        output_dir: str | Path,
        *,
        stem_id: str | None = None,
        language: str = "de",
    ) -> AlignmentResult:
        from ..aligner import WhisperAligner

        aligner = WhisperAligner(language=language)
        return aligner.align(
            stem_path,
            lines,
            output_dir,
            stem_id=stem_id or Path(stem_path).stem,
            language=language,
        )
