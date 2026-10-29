from __future__ import annotations

import gc
import os
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
from .base import (
    BaseEngine,
    CancelCheck,
    EngineInfo,
    EngineNotAvailable,
    ProgressCallback,
    UnsupportedOperation,
)

QWEN_MODEL = Path.home() / "models" / "Qwen3-TTS-12Hz-1.7B-CustomVoice"
QWEN_DEVICE = os.environ.get("DRAMA_STUDIO_QWEN_DEVICE", "cuda:0")
QWEN_PRESETS = ("Vivian", "Serena", "Ryan", "Aiden")


class Qwen3Engine(BaseEngine):
    """German engine: Qwen3-TTS CustomVoice presets, no reference cloning."""

    info = EngineInfo(
        name="qwen3",
        languages=frozenset({"de"}),
        sample_rate=24000,
        supports_reference_clone=False,
        voice_mode="preset",
        description="Qwen3-TTS 1.7B CustomVoice: preset speaker + instruction.",
    )

    def __init__(self) -> None:
        super().__init__()
        self._torch = None
        self._model = None

    def load(self) -> None:
        if self._loaded:
            return
        if not QWEN_MODEL.exists():
            raise EngineNotAvailable(f"Qwen3 model not found at {QWEN_MODEL}")
        try:
            import torch
            from qwen_tts import Qwen3TTSModel

            self._model = Qwen3TTSModel.from_pretrained(
                str(QWEN_MODEL),
                device_map=QWEN_DEVICE,
                dtype=torch.bfloat16,
                attn_implementation="sdpa",
            )
            self._torch = torch
        except Exception as exc:  # pragma: no cover - hardware path
            self.unload()
            raise EngineNotAvailable(f"could not load Qwen3: {exc}") from exc
        self._loaded = True

    def unload(self) -> None:
        self._model = None
        self._loaded = False
        gc.collect()
        if self._torch is not None and self._torch.cuda.is_available():  # pragma: no cover
            self._torch.cuda.empty_cache()

    def design_reference(self, request: ReferenceRequest) -> ReferenceResult:
        raise UnsupportedOperation(
            "Qwen3 CustomVoice uses preset speakers; reference cloning needs the Base model"
        )

    def render_stem(
        self,
        request: StemRenderRequest,
        *,
        progress: ProgressCallback | None = None,
        cancelled: CancelCheck | None = None,
    ) -> StemRenderResult:
        self.load()
        preset = (request.voice_preset or "Vivian").strip().title()
        if preset not in QWEN_PRESETS:
            raise EngineNotAvailable(
                f"unknown Qwen3 preset {preset!r}; choose from {', '.join(QWEN_PRESETS)}"
            )
        language = "German" if request.language.startswith("de") else "English"
        output_dir = Path(request.output_dir)
        take_dir = output_dir / "audio" / "takes"
        takes: list[Take] = []

        for index in range(request.candidates):
            if cancelled and cancelled():
                raise RuntimeError("cancelled")
            if progress:
                progress(index / request.candidates, f"Qwen3 take {index + 1}/{request.candidates}")
            wavs, sample_rate = self._model.generate_custom_voice(
                text=request.text,
                language=language,
                speaker=preset,
                instruct=request.instruction,
                do_sample=True,
                temperature=0.9,
                top_p=0.9,
                top_k=50,
                repetition_penalty=1.05,
            )
            audio = np.asarray(wavs[0], dtype=np.float32)
            seed = request.seed + index
            path = write_mono(
                take_dir / f"{_safe(request.stem_id)}_{seed}_{index}.wav",
                audio,
                int(sample_rate),
            )
            f0 = _median_f0(audio, int(sample_rate))
            takes.append(
                Take(
                    id=f"{request.stem_id}-{seed}-{index}",
                    file=str(path.relative_to(output_dir)),
                    duration_s=len(audio) / float(sample_rate),
                    sample_rate=int(sample_rate),
                    seed=seed,
                    f0_hz=round(f0, 1) if f0 else None,
                    mfcc_sim=None,
                    selected=False,
                )
            )

        takes[0].selected = True
        if progress:
            progress(1.0, "Qwen3 stem complete")
        return StemRenderResult(
            stem_id=request.stem_id,
            engine=self.info.name,
            sample_rate=takes[0].sample_rate,
            takes=takes,
            selected_take_id=takes[0].id,
            text_sha256=sha256_text(request.text),
        )


def _safe(value: str) -> str:
    import re

    return re.sub(r"[^a-zA-Z0-9._-]+", "_", value).strip("_") or "stem"


def _median_f0(audio: np.ndarray, sample_rate: int) -> float:
    import librosa

    f0, _, _ = librosa.pyin(audio, fmin=60, fmax=300, sr=sample_rate, frame_length=2048)
    values = f0[~np.isnan(f0)]
    return float(np.median(values)) if len(values) else 0.0
