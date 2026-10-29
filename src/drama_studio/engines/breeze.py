from __future__ import annotations

import gc
import importlib
import re
import sys
from pathlib import Path

import numpy as np

from ..audio import sha256_text, write_mono
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
)

BREEZE_REPO = Path.home() / "code" / "breeze-tts"
BREEZE_MODEL = Path.home() / "models" / "Breeze-TTS-2"
VENDOR_DIR = Path(__file__).resolve().parents[3] / "vendor" / "breeze_stems"


class BreezeEngine(BaseEngine):
    """Breeze TTS 2: reference design, then one continuous stem per scene."""

    info = EngineInfo(
        name="breeze",
        languages=frozenset({"en"}),
        sample_rate=24000,
        supports_reference_clone=True,
        voice_mode="reference",
        description="Breeze TTS 2: reference design, then scene-stem cloning.",
    )

    def __init__(self) -> None:
        super().__init__()
        self._torch = None
        self._runtime = None
        self._tokenizer = None
        self._model = None
        self._audio_tokenizer = None
        self._synth = None
        self._convert_tags = None

    # ------------------------------------------------------------------ load

    def load(self) -> None:
        if self._loaded:
            return
        if not BREEZE_REPO.exists():
            raise EngineNotAvailable(f"Breeze repo not found at {BREEZE_REPO}")
        if not BREEZE_MODEL.exists():
            raise EngineNotAvailable(f"Breeze model not found at {BREEZE_MODEL}")
        if not (VENDOR_DIR / "breeze_stems.py").exists():
            raise EngineNotAvailable(f"vendored stem module missing at {VENDOR_DIR}")

        for path in (str(VENDOR_DIR), str(BREEZE_REPO)):
            if path not in sys.path:
                sys.path.insert(0, path)

        try:
            import torch

            runtime_mod = importlib.import_module("breeze_infer.runtime")
            streaming = importlib.import_module("models.fast_streaming")
            breeze_drama = importlib.import_module("breeze_drama")

            device = runtime_mod.resolve_device()
            tokenizer, model, audio_tokenizer = runtime_mod.load_runtime(
                BREEZE_MODEL, device=device, attn_implementation="eager"
            )
            runtime_mod.update_generation_config_for_breeze(model)
            runtime = streaming.FastBreezeStreamingRuntime(
                model,
                audio_tokenizer,
                streaming.FastStreamingConfig(
                    max_new_tokens=2000,
                    max_seq_len=2048,
                    fast_all=False,
                    repetition_penalty=1.1,
                ),
                tokenizer=tokenizer,
            )
        except Exception as exc:  # pragma: no cover - hardware path
            self.unload()
            raise EngineNotAvailable(f"could not load Breeze: {exc}") from exc

        self._torch = torch
        self._runtime = runtime
        self._tokenizer = tokenizer
        self._model = model
        self._audio_tokenizer = audio_tokenizer
        self._synth = breeze_drama.synth
        self._convert_tags = breeze_drama.convert_tags
        self._loaded = True

    def unload(self) -> None:
        self._runtime = None
        self._tokenizer = None
        self._model = None
        self._audio_tokenizer = None
        self._synth = None
        self._convert_tags = None
        self._loaded = False
        gc.collect()
        if self._torch is not None and self._torch.cuda.is_available():  # pragma: no cover
            self._torch.cuda.empty_cache()

    # -------------------------------------------------------------- reference

    def design_reference(self, request: ReferenceRequest) -> ReferenceResult:
        self.load()
        assert self._synth is not None
        req = {
            "id": f"ref-{request.character_id}",
            "text": self._spoken(request.text),
            "instruction": request.instruction,
        }
        audio, sample_rate = self._synth(
            self._runtime,
            self._tokenizer,
            self._model,
            self._audio_tokenizer,
            req,
            request.cfg_scale,
            request.seed,
        )
        path = write_mono(request.output_path, audio, sample_rate)
        f0 = self._median_f0(audio, sample_rate)
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
        assert self._synth is not None
        if not request.ref_audio_path or not request.ref_text:
            raise EngineNotAvailable("Breeze stem rendering requires a reference clip and transcript")

        ref_path = Path(request.ref_audio_path)
        if not ref_path.is_absolute():
            ref_path = Path(request.output_dir) / ref_path
        if not ref_path.exists():
            raise EngineNotAvailable(f"reference clip not found: {ref_path}")

        ref_audio, ref_sr = self._read_mono(ref_path)
        ref_f0 = self._median_f0(ref_audio, ref_sr)
        ref_vec = self._mfcc(ref_audio, ref_sr)

        output_dir = Path(request.output_dir)
        take_dir = output_dir / "audio" / "takes"
        takes: list[Take] = []
        spoken = self._spoken(request.text)

        for index in range(request.candidates):
            if cancelled and cancelled():
                raise RuntimeError("cancelled")
            if progress:
                progress(index / request.candidates, f"Breeze take {index + 1}/{request.candidates}")
            req = {
                "id": f"{request.stem_id}-{request.seed}-{index}",
                "text": spoken,
                "instruction": request.instruction,
                "ref_audio_path": str(ref_path),
                "ref_text": request.ref_text,
            }
            seed = request.seed + index
            audio, sample_rate = self._synth(
                self._runtime,
                self._tokenizer,
                self._model,
                self._audio_tokenizer,
                req,
                request.cfg_scale,
                seed,
            )
            path = write_mono(
                take_dir / f"{self._safe(request.stem_id)}_{seed}_{index}.wav",
                audio,
                sample_rate,
            )
            f0 = self._median_f0(audio, sample_rate)
            sim = self._cosine(self._mfcc(audio, sample_rate), ref_vec)
            takes.append(
                Take(
                    id=f"{request.stem_id}-{seed}-{index}",
                    file=str(path.relative_to(output_dir)),
                    duration_s=len(audio) / sample_rate,
                    sample_rate=sample_rate,
                    seed=seed,
                    f0_hz=round(f0, 1) if f0 else None,
                    mfcc_sim=round(sim, 4) if sim is not None else None,
                    selected=False,
                )
            )

        best = self._choose(takes, ref_f0)
        best.selected = True
        if progress:
            progress(1.0, "Breeze stem complete")
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
        language: str = "en",
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

    # ---------------------------------------------------------------- helpers

    def _spoken(self, text: str) -> str:
        assert self._convert_tags is not None
        text = self._convert_tags(text)
        text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
        text = re.sub(r"__(.+?)__", r"\1", text)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _safe(value: str) -> str:
        return re.sub(r"[^a-zA-Z0-9._-]+", "_", value).strip("_") or "stem"

    @staticmethod
    def _read_mono(path: Path) -> tuple[np.ndarray, int]:
        import soundfile as sf

        data, sample_rate = sf.read(str(path), always_2d=True, dtype="float32")
        return data.mean(axis=1), int(sample_rate)

    @staticmethod
    def _median_f0(audio: np.ndarray, sample_rate: int) -> float:
        import librosa

        f0, _, _ = librosa.pyin(audio, fmin=60, fmax=300, sr=sample_rate, frame_length=2048)
        values = f0[~np.isnan(f0)]
        return float(np.median(values)) if len(values) else 0.0

    @staticmethod
    def _mfcc(audio: np.ndarray, sample_rate: int) -> np.ndarray:
        import librosa

        return librosa.feature.mfcc(y=audio, sr=sample_rate, n_mfcc=20).mean(axis=1)

    @staticmethod
    def _cosine(a: np.ndarray, b: np.ndarray) -> float | None:
        denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
        if denominator <= 1e-9:
            return None
        return float(np.dot(a, b) / denominator)

    @staticmethod
    def _choose(takes: list[Take], ref_f0: float) -> Take:
        return min(
            takes,
            key=lambda take: (
                (take.mfcc_sim or 0.0) < 0.98,
                abs((take.f0_hz or 0.0) - ref_f0),
            ),
        )
