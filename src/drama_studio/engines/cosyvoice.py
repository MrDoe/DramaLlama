from __future__ import annotations

import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .. import config
from ..audio import duration_s, sha256_text, write_mono
from ..models import (
    ReferenceRequest,
    ReferenceResult,
    RenderSegment,
    StemRenderRequest,
    StemRenderResult,
    Take,
)
from ..speech_check import PASS_SCORE, fold_speech, is_silent, match_score
from . import cosyvoice_runtime as runtime_module
from .base import (
    BaseEngine,
    CancelCheck,
    EngineInfo,
    EngineNotAvailable,
    ProgressCallback,
    UnsupportedOperation,
)
from .breeze_de import _cosine, _median_f0, _mfcc, _read_mono, _safe

#: Silence between separately synthesized pieces of one stem.
PAUSE_S = 0.35
#: The speech tokenizer rejects longer reference clips.
MAX_REFERENCE_S = 30.0
#: Seed distance between retries; far from the per-candidate seeds.
RETRY_SEED_STEP = 1000
#: A line this long that never scores above the second value is not a slip of
#: the model but a sign that the GGML libraries produce garbage.
BROKEN_MIN_LETTERS = 16
BROKEN_SCORE = 0.35

#: Tags the CosyVoice 3 tokenizer knows.
_MODEL_TAGS = frozenset(
    {"breath", "quick_breath", "laughter", "cough", "sigh", "noise", "mn", "hissing"}
)
_SCRIPT_TAGS = {
    "laugh": "laughter",
    "laughs": "laughter",
    "laughter": "laughter",
    "chuckle": "laughter",
    "sigh": "sigh",
    "sighs": "sigh",
    "breath": "breath",
    "inhale": "breath",
    "gasp": "quick_breath",
    "cough": "cough",
}



@dataclass(frozen=True)
class Heard:
    """What the speech check made of one piece."""

    text: str
    #: Probability of the least certain word; a mumbled word shows up here even
    #: when the transcript still comes out right.
    clarity: float


Listen = Callable[[np.ndarray, int, str], Heard]


def spoken_text(text: str) -> str:
    """Script text as the model should receive it.

    ``<laugh>``-style script tags become the model's ``[laughter]`` form; any
    other bracketed note is removed because the model would read it aloud.
    """
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(
        r"<\s*([a-zA-Z_]+)\s*/?>",
        lambda m: f"[{_SCRIPT_TAGS[m[1].lower()]}]" if m[1].lower() in _SCRIPT_TAGS else " ",
        text,
    )
    text = re.sub(
        r"\[([^\]]*)\]",
        lambda m: m[0] if m[1] in _MODEL_TAGS else " ",
        text,
    )
    return re.sub(r"\s+", " ", text).strip()


def _whisper_listen(samples: np.ndarray, sample_rate: int, language: str) -> Heard:
    from ..transcriber import WhisperTranscriber

    transcriber = WhisperTranscriber(language=language, device="cpu", compute_type="int8")
    result = transcriber.transcribe(samples, sample_rate, word_timestamps=True)
    clarity = min(
        (word.probability for word in result.words if word.probability is not None),
        default=0.0,
    )
    return Heard(result.text, clarity)


@dataclass
class _Piece:
    audio: np.ndarray
    sample_rate: int
    instruction: str | None
    silent: bool
    score: float = 0.0
    clarity: float = 0.0

    @property
    def rank(self) -> tuple[bool, float, float]:
        return (not self.silent, self.score, self.clarity)


class CosyVoiceEngine(BaseEngine):
    """CosyVoice 3 through ``cosyvoice-cli``: voice cloning with delivery steering.

    Every piece is a separate short-lived process, so no VRAM is held between
    pieces. The model sometimes garbles or drops a line, so each piece is
    transcribed on the CPU and retried with a new seed when it does not match.
    """

    info = EngineInfo(
        name="cosyvoice",
        languages=frozenset({"de", "en"}),
        sample_rate=24000,
        supports_reference_clone=True,
        voice_mode="reference",
        description="CosyVoice 3: voice cloning from a sample, steered by line directions.",
    )

    def __init__(self, listen: Listen | None = None) -> None:
        super().__init__()
        self._listen = listen or _whisper_listen
        self._runtime: runtime_module.CosyRuntime | None = None

    def available(self) -> bool:
        return runtime_module.installed()

    def load(self) -> None:
        if self._loaded:
            return
        self._runtime = runtime_module.resolve_runtime()
        self._loaded = True

    def unload(self) -> None:
        self._runtime = None
        self._loaded = False

    def design_reference(self, request: ReferenceRequest) -> ReferenceResult:
        raise UnsupportedOperation(
            "CosyVoice clones an existing voice; generate one from a sample in the "
            "Voices step"
        )

    def render_stem(
        self,
        request: StemRenderRequest,
        *,
        progress: ProgressCallback | None = None,
        cancelled: CancelCheck | None = None,
    ) -> StemRenderResult:
        try:
            return self._render(request, progress=progress, cancelled=cancelled)
        finally:
            # Nothing stays resident anyway; dropping the runtime makes the next
            # job probe again, in case an installed library changed meanwhile.
            self.unload()

    def _render(
        self,
        request: StemRenderRequest,
        *,
        progress: ProgressCallback | None,
        cancelled: CancelCheck | None,
    ) -> StemRenderResult:
        reference = self._reference(request)
        self.load()
        assert self._runtime is not None
        output_dir = Path(request.output_dir)
        take_dir = output_dir / "audio" / "takes"
        voice = runtime_module.prepare_voice(
            self._runtime,
            reference,
            " ".join((request.ref_text or "").split()),
            config.DATA_DIR / "cosyvoice" / "voices",
            cancelled=cancelled,
        )
        ref_audio, ref_rate = _read_mono(reference)
        ref_vec = _mfcc(ref_audio, ref_rate)

        segments = request.segments or [RenderSegment(text=request.text)]
        takes: list[Take] = []
        weakest: list[tuple[float, float]] = []  # (match, clarity) per take
        total = request.candidates * len(segments)
        with tempfile.TemporaryDirectory(prefix="cosyvoice-") as scratch:
            for index in range(request.candidates):
                seed = request.seed + index
                warnings: list[str] = []
                pieces: list[_Piece] = []
                for number, segment in enumerate(segments):
                    if cancelled and cancelled():
                        raise RuntimeError("cancelled")
                    if progress:
                        progress(
                            (index * len(segments) + number) / total,
                            f"CosyVoice take {index + 1}/{request.candidates}, "
                            f"piece {number + 1}/{len(segments)}",
                        )
                    pieces.append(
                        self._piece(
                            voice,
                            segment,
                            seed,
                            request.language,
                            Path(scratch) / "piece.wav",
                            warnings,
                            cancelled,
                        )
                    )
                sample_rate = pieces[0].sample_rate
                pause = np.zeros(int(PAUSE_S * sample_rate), dtype=np.float32)
                joined = [pieces[0].audio]
                for piece in pieces[1:]:
                    joined.extend((pause, piece.audio))
                audio = np.concatenate(joined)
                path = write_mono(
                    take_dir / f"{_safe(request.stem_id)}_{seed}_{index}.wav",
                    audio,
                    sample_rate,
                )
                f0 = _median_f0(audio, sample_rate)
                similarity = _cosine(_mfcc(audio, sample_rate), ref_vec)
                takes.append(
                    Take(
                        id=f"cosyvoice-{request.stem_id}-{seed}-{index}",
                        file=str(path.relative_to(output_dir)),
                        engine=self.info.name,
                        duration_s=len(audio) / sample_rate,
                        sample_rate=sample_rate,
                        seed=seed,
                        f0_hz=round(f0, 1) if f0 else None,
                        mfcc_sim=round(similarity, 4) if similarity is not None else None,
                        warnings=list(dict.fromkeys(warnings)),
                    )
                )
                weakest.append(
                    (
                        min(min(piece.score, PASS_SCORE) for piece in pieces),
                        min(piece.clarity for piece in pieces),
                    )
                )

        # The take whose weakest piece is best: first whether it says the line,
        # then how clearly; voice similarity breaks remaining ties.
        best = max(
            range(len(takes)),
            key=lambda i: (*weakest[i], takes[i].mfcc_sim or 0.0),
        )
        takes[best].selected = True
        if progress:
            progress(1.0, "CosyVoice stem complete")
        return StemRenderResult(
            stem_id=request.stem_id,
            engine=self.info.name,
            sample_rate=takes[0].sample_rate,
            takes=takes,
            selected_take_id=takes[best].id,
            text_sha256=sha256_text(request.text),
        )

    def _reference(self, request: StemRenderRequest) -> Path:
        if not request.ref_audio_path or not (request.ref_text or "").strip():
            raise EngineNotAvailable(
                "CosyVoice needs a reference clip and its transcript"
            )
        path = Path(request.ref_audio_path)
        if not path.is_absolute():
            path = Path(request.output_dir) / path
        if not path.is_file():
            raise EngineNotAvailable(f"reference clip not found: {path}")
        seconds = duration_s(path)
        if seconds > MAX_REFERENCE_S:
            raise EngineNotAvailable(
                f"reference clip is {seconds:.0f} s long; CosyVoice accepts at most "
                f"{MAX_REFERENCE_S:.0f} s. Generate a voice from it in the Voices step."
            )
        return path

    def _piece(
        self,
        voice: Path,
        segment: RenderSegment,
        seed: int,
        language: str,
        scratch: Path,
        warnings: list[str],
        cancelled: CancelCheck | None,
    ) -> _Piece:
        quoted = segment.text if len(segment.text) <= 60 else f"{segment.text[:57]}…"
        arguments = (voice, segment, seed, language, scratch, warnings, cancelled)
        best = self._attempts(*arguments)
        if self._libraries_suspected(segment, best):
            best = self._try_other_libraries(best, arguments, warnings)
        if best.silent:
            raise EngineNotAvailable(
                f"CosyVoice produced only silence for “{quoted}” "
                f"({self._runtime_source()} GGML). {' '.join(warnings)}".strip()
            )
        if segment.instruction and best.instruction is None:
            warnings.append(f"“{quoted}” was rendered without its direction.")
        if best.score < PASS_SCORE:
            warnings.append(
                f"“{quoted}” may be misspoken (match {best.score:.0%}); listen to it."
            )
        return best

    def _attempts(
        self,
        voice: Path,
        segment: RenderSegment,
        seed: int,
        language: str,
        scratch: Path,
        warnings: list[str],
        cancelled: CancelCheck | None,
    ) -> _Piece:
        """Up to three attempts; the last one ignores the direction."""
        assert self._runtime is not None
        text = spoken_text(segment.text)
        instructions = (segment.instruction, segment.instruction, None)
        best: _Piece | None = None
        for attempt, instruction in enumerate(instructions):
            runtime_module.synthesize(
                self._runtime,
                voice,
                text,
                scratch,
                seed=seed + attempt * RETRY_SEED_STEP,
                instruction=instruction,
                cancelled=cancelled,
            )
            audio, sample_rate = _read_mono(scratch)
            piece = _Piece(audio, sample_rate, instruction, is_silent(audio, sample_rate))
            if not piece.silent:
                try:
                    heard = self._listen(audio, sample_rate, language)
                    piece.score = match_score(segment.text, heard.text)
                    piece.clarity = heard.clarity
                except RuntimeError as exc:
                    warnings.append(f"Rendered speech was not checked: {exc}")
                    piece.score = PASS_SCORE
            if best is None or piece.rank > best.rank:
                best = piece
            if piece.score >= PASS_SCORE:
                break
        assert best is not None
        return best

    def _libraries_suspected(self, segment: RenderSegment, best: _Piece) -> bool:
        # Only Ollama's copy changes behind our back.
        return (
            self._runtime_source() == "ollama"
            and len(fold_speech(segment.text)) >= BROKEN_MIN_LETTERS
            and (best.silent or best.score < BROKEN_SCORE)
        )

    def _try_other_libraries(
        self, best: _Piece, arguments: tuple, warnings: list[str]
    ) -> _Piece:
        """Render the piece again with another GGML copy to see which is at fault."""
        suspect = self._runtime
        assert suspect is not None
        try:
            other = runtime_module.resolve_runtime(
                exclude=frozenset({suspect.ggml_fingerprint})
            )
        except EngineNotAvailable:
            warnings.append(
                "Ollama's GGML libraries may be producing broken audio. Re-run the "
                "installer with the CosyVoice component to download a second copy."
            )
            return best
        self._runtime = other
        retry = self._attempts(*arguments)
        if retry.silent or retry.score < PASS_SCORE:
            # The other copy is no better, so the libraries are not to blame.
            self._runtime = suspect
            return max(best, retry, key=lambda piece: piece.rank)
        runtime_module.reject_ggml(suspect)
        warnings.append(
            "Ollama's GGML libraries produced broken audio; switched to the "
            f"{other.ggml_source} copy and will not use that Ollama build again."
        )
        return retry

    def _runtime_source(self) -> str:
        return getattr(self._runtime, "ggml_source", "unknown")
