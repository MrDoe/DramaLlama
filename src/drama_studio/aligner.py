from __future__ import annotations

import importlib
import sys
from pathlib import Path

import numpy as np

from .audio import write_mono
from .models import AlignedLine, AlignmentResult, Line

VENDOR_DIR = Path(__file__).resolve().parents[2] / "vendor" / "breeze_stems"


class WhisperAligner:
    """CPU-only word alignment shared by Breeze and Qwen3 stems.

    Whisper large-v3 on CPU int8 is deliberate: Breeze and Whisper cannot be
    resident on a 16 GB GPU at the same time.
    """

    def __init__(
        self,
        *,
        language: str = "en",
        device: str = "cpu",
        compute_type: str = "int8",
    ) -> None:
        self.language = language
        self.device = device
        self.compute_type = compute_type
        self._align_cuts = None
        self._find_asr = None
        self._asr = None

    def load(self) -> None:
        if self._asr is not None:
            return
        vendor = str(VENDOR_DIR)
        if vendor not in sys.path:
            sys.path.insert(0, vendor)
        breeze_stems = importlib.import_module("breeze_stems")
        from faster_whisper import WhisperModel

        self._align_cuts = breeze_stems.align_cuts
        self._find_asr = breeze_stems.find_asr
        self._asr = WhisperModel(
            self._find_asr(), device=self.device, compute_type=self.compute_type
        )

    def align(
        self,
        stem_path: str | Path,
        lines: list[Line],
        output_dir: str | Path,
        *,
        stem_id: str | None = None,
        language: str | None = None,
    ) -> AlignmentResult:
        self.load()
        assert self._align_cuts is not None
        language = language or self.language
        samples, sample_rate = self._read_mono(Path(stem_path))
        groups = [{"text": line.text} for line in lines]
        cuts, confs = self._align_cuts(
            samples, sample_rate, groups, self._asr, language=language
        )

        out_dir = Path(output_dir) / "lines"
        aligned: list[AlignedLine] = []
        for line, (start, end), conf in zip(lines, cuts, confs):
            segment = samples[int(start * sample_rate) : int(end * sample_rate)]
            write_mono(out_dir / f"{_safe(line.id)}.wav", segment, sample_rate)
            aligned.append(
                AlignedLine(
                    line_id=line.id,
                    start_s=round(float(start), 3),
                    end_s=round(float(end), 3),
                    confidence=round(float(conf), 3),
                    fallback=float(conf) < 0.8,
                )
            )
        return AlignmentResult(
            stem_id=stem_id or Path(stem_path).stem,
            lines=aligned,
            aligner="whisper-large-v3",
        )

    @staticmethod
    def _read_mono(path: Path) -> tuple[np.ndarray, int]:
        import soundfile as sf

        data, sample_rate = sf.read(str(path), always_2d=True, dtype="float32")
        return data.mean(axis=1), int(sample_rate)


def _safe(value: str) -> str:
    import re

    return re.sub(r"[^a-zA-Z0-9._-]+", "_", value).strip("_") or "line"
