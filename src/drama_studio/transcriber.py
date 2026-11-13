from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field
from typing import Any

import numpy as np

ASR_GLOB = os.path.expanduser(
    "~/.cache/huggingface/hub/models--Systran--faster-whisper-large-v3/snapshots/*"
)

# Whisper large-v3 is expensive to load. The worker is single-threaded, so a
# process-wide cache is safe and keeps repeated reference jobs responsive.
_MODEL_CACHE: dict[tuple[str, str, str], Any] = {}


@dataclass(frozen=True)
class TranscriptWord:
    word: str
    start: float
    end: float
    probability: float | None = None


@dataclass
class TranscriptResult:
    words: list[TranscriptWord] = field(default_factory=list)
    text: str = ""
    confidence: float | None = None
    no_speech_prob: float | None = None


def find_asr_path() -> str:
    """Resolve the pinned faster-whisper large-v3 snapshot.

    This mirrors ``vendor/breeze_stems.find_asr`` but avoids importing that
    module, which pulls in torch and the Breeze runtime at import time.
    """
    paths = sorted(glob.glob(ASR_GLOB))
    if not paths:
        raise RuntimeError(
            "faster-whisper large-v3 was not found in the Hugging Face cache; "
            "run the installer or download the pinned model first"
        )
    return paths[0]


class WhisperTranscriber:
    """CPU-only faster-whisper transcription helper.

    Reference generation must never put Whisper on the GPU: Breeze and Whisper
    do not fit together on a 16 GB card, and reference preparation does not
    need a TTS model at all.
    """

    def __init__(
        self,
        *,
        language: str = "en",
        device: str = "cpu",
        compute_type: str = "int8",
        model_path: str | None = None,
    ) -> None:
        self.language = language
        self.device = device
        self.compute_type = compute_type
        self.model_path = model_path
        self._model: Any = None

    def load(self) -> None:
        if self._model is not None:
            return
        path = self.model_path or find_asr_path()
        key = (path, self.device, self.compute_type)
        if key not in _MODEL_CACHE:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:  # pragma: no cover - optional extra
                raise RuntimeError(
                    "faster-whisper is not installed; install the TTS extras "
                    "before generating reference voices"
                ) from exc
            _MODEL_CACHE[key] = WhisperModel(
                path, device=self.device, compute_type=self.compute_type
            )
        self._model = _MODEL_CACHE[key]

    def transcribe(
        self,
        samples: np.ndarray,
        sample_rate: int,
        *,
        word_timestamps: bool = True,
    ) -> TranscriptResult:
        self.load()
        data = np.asarray(samples, dtype=np.float32)
        if data.ndim > 1:
            data = data.mean(axis=1)
        if sample_rate != 16000:  # Whisper's native rate.
            import librosa

            data = librosa.resample(
                data, orig_sr=int(sample_rate), target_sr=16000
            ).astype(np.float32)

        segments, _info = self._model.transcribe(
            data,
            language=self.language,
            beam_size=5,
            word_timestamps=word_timestamps,
            vad_filter=False,
            condition_on_previous_text=False,
        )

        words: list[TranscriptWord] = []
        segment_texts: list[str] = []
        no_speech: list[float] = []
        for segment in segments:
            text = (segment.text or "").strip()
            if text:
                segment_texts.append(text)
            probability = getattr(segment, "no_speech_prob", None)
            if probability is not None:
                no_speech.append(float(probability))
            seg_words = getattr(segment, "words", None) if word_timestamps else None
            if seg_words:
                for word in seg_words:
                    token = (word.word or "").strip()
                    if not token:
                        continue
                    words.append(
                        TranscriptWord(
                            word=token,
                            start=float(word.start),
                            end=float(word.end),
                            probability=(
                                float(word.probability)
                                if getattr(word, "probability", None) is not None
                                else None
                            ),
                        )
                    )
            elif text:
                # Defensive fallback for mocks/older faster-whisper builds.
                words.append(
                    TranscriptWord(
                        word=text,
                        start=float(segment.start),
                        end=float(segment.end),
                    )
                )

        probabilities = [
            word.probability for word in words if word.probability is not None
        ]
        confidence = (
            float(sum(probabilities) / len(probabilities))
            if probabilities
            else None
        )
        text = " ".join(segment_texts).strip()
        if not text:
            text = " ".join(word.word for word in words).strip()
        return TranscriptResult(
            words=words,
            text=text,
            confidence=confidence,
            no_speech_prob=max(no_speech) if no_speech else None,
        )
