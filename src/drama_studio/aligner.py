from __future__ import annotations

import importlib
import sys
from pathlib import Path

import numpy as np

from .audio import write_mono
from .models import AlignedLine, AlignmentResult, Line

VENDOR_DIR = Path(__file__).resolve().parents[2] / "vendor" / "breeze_stems"

# Auto-cut allowance. Whisper reports where the *words* are, and a word's
# consonant decay sits past its reported end: cutting on the boundary as-is
# leaves 0.4-1.0 s of the phrase out, which reads as every line being clipped.
# The allowance is spent on measured signal, not on dead air — the envelope walk
# below stops as soon as the take goes quiet, so padding a pauseless exchange
# costs nothing and padding a 2 s beat does not add two seconds of nothing.
PAD_LEAD_S = 0.15
PAD_TAIL_S = 0.60
HOP_S = 0.02  # envelope resolution: 20 ms
_FLOOR_RATIO = 0.04  # share of the take's loudest hop judged to be speech
# A ratio alone is not enough: on a silent take the peak is 0, so 0 >= 0 would
# call digital silence "voiced" and let the cut run to its limit.
_FLOOR_FLOOR = 1e-5
_HYSTERESIS_HOPS = 3  # a dip this short is still the same phrase


def speech_floor(env: np.ndarray) -> float:
    """Energy above which a hop counts as part of a phrase."""
    return max(float(env.max(initial=0.0)) * _FLOOR_RATIO, _FLOOR_FLOOR)


class WhisperAligner:
    """CPU-only word alignment shared by Breeze and Chatterbox stems.

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
        cursor = 0.0
        PAUSE = 0.5
        env = envelope(samples, sample_rate)
        floor = speech_floor(env)
        duration_s = samples.size / float(sample_rate)
        for index, (line, (start, end), conf) in enumerate(zip(lines, cuts, confs)):
            # Never swallow the next line's attack, and never run past the take.
            nxt = float(cuts[index + 1][0]) if index + 1 < len(cuts) else duration_s
            previous_end = aligned[-1].end_s if aligned else 0.0
            lead_limit = max(previous_end, float(start) - PAD_LEAD_S)
            tail_limit = max(
                float(end),
                min(float(end) + PAD_TAIL_S, nxt - PAD_LEAD_S, duration_s),
            )
            head_s, tail_s = padded_window(
                env,
                float(start),
                float(end),
                lead_limit=lead_limit,
                tail_limit=tail_limit,
                floor=floor,
            )
            segment = samples[int(head_s * sample_rate) : int(tail_s * sample_rate)]
            write_mono(out_dir / f"{_safe(line.id)}.wav", segment, sample_rate)
            dur = max(0.0, tail_s - head_s)
            pos = round(cursor, 3)
            aligned.append(
                AlignedLine(
                    id=line.id,
                    line_id=line.id,
                    start_s=head_s,
                    end_s=tail_s,
                    position_s=pos,
                    confidence=round(float(conf), 3),
                    fallback=float(conf) < 0.8,
                )
            )
            cursor = pos + dur + PAUSE
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


def envelope(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """Short-time RMS in HOP_S blocks — the shape the cut is allowed to follow."""
    hop = max(1, int(HOP_S * sample_rate))
    usable = samples.size // hop * hop
    if usable <= 0:
        return np.zeros(0, dtype=np.float32)
    frames = samples[:usable].reshape(-1, hop)
    return np.sqrt((frames.astype(np.float32) ** 2).mean(axis=1))


def _walk_forward(env: np.ndarray, index: int, limit: int, floor: float) -> int:
    """Last hop before the phrase goes quiet, searching up to `limit`.

    A short dip is still the same phrase — a plosive between two syllables drops
    below the floor routinely — so the walk only stops after a run of quiet
    hops. It never returns less than `index`.
    """
    best = index
    quiet = 0
    while index < limit:
        if env[index] >= floor:
            quiet = 0
            best = index
        else:
            quiet += 1
            if quiet > _HYSTERESIS_HOPS:
                break
        index += 1
    return best


def _walk_backward(env: np.ndarray, index: int, floor_limit: int, floor: float) -> int:
    """Mirror of `_walk_forward`, for the breath before the first word."""
    best = index
    quiet = 0
    while index > floor_limit:
        if env[index] >= floor:
            quiet = 0
            best = index
        else:
            quiet += 1
            if quiet > _HYSTERESIS_HOPS:
                break
        index -= 1
    return best


def padded_window(
    env: np.ndarray,
    start_s: float,
    end_s: float,
    *,
    lead_limit: float,
    tail_limit: float,
    floor: float,
) -> tuple[float, float]:
    """Cut bounds for one line: the aligned words plus whatever still sounds.

    `lead_limit` and `tail_limit` are hard bounds in seconds — the previous cut's
    end and the next cut's start (minus its own lead), or the take's edges. The
    result always contains `[start_s, end_s]`: the allowance can grow a cut,
    never shrink one.
    """
    if env.size == 0:
        return start_s, end_s
    hops = env.size

    def to_hop(value: float) -> int:
        return int(min(hops - 1, max(0.0, value / HOP_S)))

    head = to_hop(start_s)
    tail = to_hop(end_s)
    head = _walk_backward(env, head, to_hop(lead_limit), floor)
    tail = _walk_forward(env, tail, to_hop(tail_limit), floor) + 1
    head_s = max(lead_limit, min(start_s, head * HOP_S))
    tail_s = min(tail_limit, max(end_s, min(tail, hops) * HOP_S))
    return round(head_s, 3), round(tail_s, 3)
