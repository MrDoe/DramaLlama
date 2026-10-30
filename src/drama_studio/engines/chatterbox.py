from __future__ import annotations

import io
import os
import re
import subprocess
import time
from pathlib import Path

import httpx
import numpy as np
import soundfile as sf

from ..audio import sha256_text, write_mono
from ..config import CHATTERBOX_URL


def _container_name() -> str:
    # Same lookup as config._env (DRAMA_LLAMA_ wins, legacy DRAMA_STUDIO_
    # falls back) without touching config.py. Empty string disables docker
    # management: the engine then only health-checks an external server.
    return (
        os.environ.get("DRAMA_LLAMA_CHATTERBOX_CONTAINER")
        or os.environ.get("DRAMA_STUDIO_CHATTERBOX_CONTAINER", "chatterbox-tts-api")
    )


#: How long load() waits for a freshly started container to serve /health.
START_TIMEOUT_S = 180.0
from ..models import (
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


class ChatterboxEngine(BaseEngine):
    """Chatterbox Multilingual 0.5B over its local OpenAI-style HTTP server.

    The server owns the model; this adapter only sends text plus an optional
    voice sample and writes the returned WAV. Chatterbox clones a reference but
    has no voice-design mode, so `ref_audio_path` is optional and falls back to
    the server's configured default voice.
    """

    info = EngineInfo(
        name="chatterbox",
        languages=frozenset({"de", "en"}),
        sample_rate=24000,
        supports_reference_clone=True,
        voice_mode="reference",
        description="Chatterbox Multilingual 0.5B: voice-sample cloning, exaggeration/cfg per take.",
    )

    def __init__(self, container: str | None = None) -> None:
        super().__init__()
        self.base_url = CHATTERBOX_URL.rstrip("/")
        self.container = _container_name() if container is None else container

    def available(self) -> bool:
        # Status probe only: do not start the container here.
        return self._healthy()

    def load(self) -> None:
        if self._loaded:
            return
        self._ensure_server()
        try:
            response = httpx.get(f"{self.base_url}/health", timeout=5)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise EngineNotAvailable(
                f"Chatterbox server not reachable at {self.base_url}: {exc}"
            ) from exc
        if not payload.get("model_loaded"):
            raise EngineNotAvailable("Chatterbox server is up but the model is not loaded")
        self._loaded = True

    def _ensure_server(self) -> None:
        """Start the container when the server is down, then wait for /health."""
        if not self.container or self._healthy():
            return
        try:
            proc = subprocess.run(
                ["docker", "start", self.container],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except Exception:
            return  # no docker binary: assume an externally managed server
        if proc.returncode != 0:
            return  # no such container: same assumption, load() decides
        deadline = time.monotonic() + START_TIMEOUT_S
        while time.monotonic() < deadline:
            if self._healthy():
                return
            time.sleep(2)
        # Still down: fall through so load() raises the user-facing error.

    def _healthy(self) -> bool:
        try:
            response = httpx.get(f"{self.base_url}/health", timeout=5)
            response.raise_for_status()
            return bool(response.json().get("model_loaded"))
        except Exception:
            return False

    def unload(self) -> None:
        # Drama Llama owns the container lifecycle: stop it so its ~4.4 GB of
        # VRAM is released for Breeze/LLM work. load() restarts it on demand.
        # Best-effort: without docker/a container this just flips the flag.
        if self.container:
            try:
                subprocess.run(
                    ["docker", "stop", self.container],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
            except Exception:
                pass
        self._loaded = False

    def design_reference(self, request: ReferenceRequest) -> ReferenceResult:
        # Never needs the server: release a container that an eager
        # EngineManager.get() may have just started, then fail fast.
        self.unload()
        raise UnsupportedOperation(
            "Chatterbox clones an existing voice sample; upload a reference clip instead"
        )

    def render_stem(
        self,
        request: StemRenderRequest,
        *,
        progress: ProgressCallback | None = None,
        cancelled: CancelCheck | None = None,
    ) -> StemRenderResult:
        self.load()
        try:
            return self._render_inner(
                request, progress=progress, cancelled=cancelled
            )
        finally:
            # Per-job lifecycle: the container holds ~4.4 GB of VRAM, so stop
            # it when the render is done. The next render restarts it.
            self.unload()

    def _render_inner(
        self,
        request: StemRenderRequest,
        *,
        progress: ProgressCallback | None = None,
        cancelled: CancelCheck | None = None,
    ) -> StemRenderResult:
        output_dir = Path(request.output_dir)
        take_dir = output_dir / "audio" / "takes"
        ref_path = None
        if request.ref_audio_path:
            ref_path = Path(request.ref_audio_path)
            if not ref_path.is_absolute():
                ref_path = output_dir / ref_path
            if not ref_path.exists():
                ref_path = None

        ref_vec = None
        if ref_path is not None:
            ref_audio, ref_sr = _read_mono(ref_path)
            ref_vec = _mfcc(ref_audio, ref_sr)

        takes: list[Take] = []
        for index in range(request.candidates):
            if cancelled and cancelled():
                raise RuntimeError("cancelled")
            if progress:
                progress(index / request.candidates, f"Chatterbox take {index + 1}/{request.candidates}")
            audio, sample_rate = self._speak(request, ref_path)
            seed = request.seed + index
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
                    id=f"chatterbox-{request.stem_id}-{seed}-{index}",
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

        best = min(
            takes,
            key=lambda take: (
                (take.mfcc_sim or 0.0) < 0.98,
                -(take.mfcc_sim or 0.0),
            ),
        )
        best.selected = True
        if progress:
            progress(1.0, "Chatterbox stem complete")
        return StemRenderResult(
            stem_id=request.stem_id,
            engine=self.info.name,
            sample_rate=takes[0].sample_rate,
            takes=takes,
            selected_take_id=best.id,
            text_sha256=sha256_text(request.text),
        )

    def _speak(self, request: StemRenderRequest, ref_path: Path | None) -> tuple[np.ndarray, int]:
        exaggeration = max(0.25, min(2.0, request.cfg_scale / 5.0))
        data = {
            "input": request.text,
            "response_format": "wav",
            "exaggeration": str(round(exaggeration, 3)),
            "cfg_weight": "0.5",
            "temperature": "0.8",
        }
        try:
            if ref_path is not None:
                with ref_path.open("rb") as handle:
                    response = httpx.post(
                        f"{self.base_url}/v1/audio/speech/upload",
                        data=data,
                        files={
                            "voice_file": (
                                ref_path.name,
                                handle,
                                "audio/wav",
                            )
                        },
                        timeout=300,
                    )
            else:
                response = httpx.post(
                    f"{self.base_url}/v1/audio/speech",
                    json={
                        "input": request.text,
                        "response_format": "wav",
                        "exaggeration": exaggeration,
                        "cfg_weight": 0.5,
                        "temperature": 0.8,
                    },
                    timeout=300,
                )
            response.raise_for_status()
            audio, sample_rate = sf.read(
                io.BytesIO(response.content), always_2d=True, dtype="float32"
            )
            return audio.mean(axis=1), int(sample_rate)
        except Exception as exc:
            raise EngineNotAvailable(f"Chatterbox generation failed: {exc}") from exc


def _safe(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", value).strip("_") or "stem"


def _read_mono(path: Path) -> tuple[np.ndarray, int]:
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
