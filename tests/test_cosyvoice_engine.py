"""CosyVoice engine behaviour with the runtime and Whisper faked out."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from drama_studio.engines import cosyvoice as engine_module
from drama_studio.engines.base import EngineNotAvailable, UnsupportedOperation
from drama_studio.engines.cosyvoice import (
    PAUSE_S,
    CosyVoiceEngine,
    Heard,
    spoken_text,
)
from drama_studio.engines.manager import ENGINES
from drama_studio.models import ReferenceRequest, RenderSegment, StemRenderRequest

RATE = 24000


def _tone(seconds: float = 1.0, level: float = 0.2) -> np.ndarray:
    t = np.arange(int(RATE * seconds)) / RATE
    return (level * np.sin(2 * np.pi * 180 * t)).astype(np.float32)


class Studio:
    """Fake runtime and ear: records calls, says back whatever the script wants."""

    def __init__(self, monkeypatch) -> None:
        self.calls: list[dict] = []
        self.voices: list[tuple[Path, str]] = []
        self.heard: list[str] | None = None  # None: hears exactly the text
        self.clarity: list[float] = []  # per listen; empty means perfectly clear
        self.silent_calls: set[int] = set()
        self._last = ""
        monkeypatch.setattr(engine_module.runtime_module, "resolve_runtime", lambda: object())
        monkeypatch.setattr(engine_module.runtime_module, "prepare_voice", self.prepare)
        monkeypatch.setattr(engine_module.runtime_module, "synthesize", self.synthesize)

    def prepare(self, runtime, clip, transcript, cache_dir, *, cancelled=None):
        self.voices.append((clip, transcript))
        return Path("voice.gguf")

    def synthesize(self, runtime, voice, text, output, *, seed, instruction=None, cancelled=None):
        index = len(self.calls)
        self.calls.append({"text": text, "seed": seed, "instruction": instruction})
        self._last = text
        level = 0.0 if index in self.silent_calls else 0.2
        sf.write(output, _tone(level=level), RATE)

    def listen(self, samples, sample_rate, language):
        text = self._last if self.heard is None else self.heard.pop(0)
        return Heard(text, self.clarity.pop(0) if self.clarity else 1.0)

    def engine(self) -> CosyVoiceEngine:
        return CosyVoiceEngine(listen=self.listen)


@pytest.fixture
def studio(monkeypatch) -> Studio:
    return Studio(monkeypatch)


def _request(tmp_path: Path, **overrides) -> StemRenderRequest:
    ref = tmp_path / "refs" / "anna.wav"
    ref.parent.mkdir(exist_ok=True)
    sf.write(ref, _tone(7.0), RATE)
    values = {
        "project_id": "p",
        "stem_id": "s1-anna",
        "speaker": "ANNA",
        "text": "Kommen Sie herein. ... Wann kommt der Bus?",
        "instruction": "ignored long stem instruction",
        "output_dir": str(tmp_path),
        "ref_audio_path": "refs/anna.wav",
        "ref_text": "Guten Tag.",
        "language": "de",
        "seed": 52,
        "candidates": 1,
    }
    return StemRenderRequest(**(values | overrides))


def test_engine_is_registered_and_covers_german_and_english():
    assert ENGINES["cosyvoice"] is CosyVoiceEngine
    assert CosyVoiceEngine.info.languages == frozenset({"de", "en"})
    assert CosyVoiceEngine.info.voice_mode == "reference"


def test_design_is_unsupported_and_starts_nothing(monkeypatch):
    def explode():
        raise AssertionError("the runtime must not be resolved")

    monkeypatch.setattr(engine_module.runtime_module, "resolve_runtime", explode)
    request = ReferenceRequest(
        character_id="anna", text="Hallo.", instruction="ruhig", output_path="x.wav"
    )
    with pytest.raises(UnsupportedOperation, match="sample"):
        CosyVoiceEngine().design_reference(request)


@pytest.mark.parametrize("missing", [{"ref_audio_path": None}, {"ref_text": ""}])
def test_a_reference_clip_and_transcript_are_required(studio, tmp_path, missing):
    with pytest.raises(EngineNotAvailable, match="reference"):
        studio.engine().render_stem(_request(tmp_path, **missing))


def test_a_reference_over_thirty_seconds_is_refused(studio, tmp_path):
    request = _request(tmp_path)
    sf.write(tmp_path / "refs" / "anna.wav", _tone(31.0), RATE)
    with pytest.raises(EngineNotAvailable, match="30"):
        studio.engine().render_stem(request)


def test_plain_stem_is_one_call_with_one_take_per_candidate(studio, tmp_path):
    result = studio.engine().render_stem(_request(tmp_path, candidates=2))

    assert [c["instruction"] for c in studio.calls] == [None, None]
    assert [c["seed"] for c in studio.calls] == [52, 53]
    assert [t.id for t in result.takes] == [
        "cosyvoice-s1-anna-52-0",
        "cosyvoice-s1-anna-53-1",
    ]
    assert all((tmp_path / t.file).is_file() and t.engine == "cosyvoice" for t in result.takes)
    assert [t.selected for t in result.takes].count(True) == 1
    assert [t.warnings for t in result.takes] == [[], []]
    assert studio.voices[0][1] == "Guten Tag."


def test_segments_keep_their_delivery_and_are_joined_with_a_pause(studio, tmp_path):
    request = _request(
        tmp_path,
        segments=[
            RenderSegment(text="Kommen Sie herein.", instruction="soft please"),
            RenderSegment(text="Wann kommt der Bus?"),
        ],
    )

    result = studio.engine().render_stem(request)

    assert [(c["text"], c["instruction"]) for c in studio.calls] == [
        ("Kommen Sie herein.", "soft please"),
        ("Wann kommt der Bus?", None),
    ]
    assert result.takes[0].duration_s == pytest.approx(2.0 + PAUSE_S, abs=0.01)


def test_a_misspoken_piece_is_retried_with_a_new_seed(studio, tmp_path):
    studio.heard = ["Untertitelung BR", "Wann kommt der Bus?"]

    result = studio.engine().render_stem(_request(tmp_path, text="Wann kommt der Bus?"))

    assert [c["seed"] for c in studio.calls] == [52, 1052]
    assert result.takes[0].warnings == []


def test_the_last_attempt_drops_the_direction_and_says_so(studio, tmp_path):
    studio.heard = ["Quatsch", "Quatsch", "Wann kommt der Bus?"]
    request = _request(
        tmp_path,
        text="Wann kommt der Bus?",
        segments=[RenderSegment(text="Wann kommt der Bus?", instruction="angry please")],
    )

    result = studio.engine().render_stem(request)

    assert [c["instruction"] for c in studio.calls] == ["angry please", "angry please", None]
    assert any("without its direction" in w for w in result.takes[0].warnings)


def test_the_best_attempt_is_kept_and_flagged_when_none_passes(studio, tmp_path):
    studio.heard = ["Nein", "Wann kommt die", "Quatsch"]

    result = studio.engine().render_stem(_request(tmp_path, text="Wann kommt der Bus?"))

    assert len(studio.calls) == 3
    assert len(result.takes[0].warnings) == 1
    assert "Wann kommt der Bus?" in result.takes[0].warnings[0]
    assert result.takes[0].selected


def test_the_selected_take_is_the_one_that_says_the_line(studio, tmp_path):
    studio.heard = ["Quatsch", "Quatsch", "Quatsch", "Wann kommt der Bus?"]

    result = studio.engine().render_stem(
        _request(tmp_path, text="Wann kommt der Bus?", candidates=2)
    )

    assert result.selected_take_id == result.takes[1].id


def test_only_silence_is_an_error(studio, tmp_path):
    studio.silent_calls = {0, 1, 2}
    with pytest.raises(EngineNotAvailable, match="silence"):
        studio.engine().render_stem(_request(tmp_path))


def test_rendering_continues_with_a_warning_when_whisper_is_unavailable(studio, tmp_path):
    def deaf(samples, sample_rate, language):
        raise RuntimeError("faster-whisper large-v3 was not found")

    result = CosyVoiceEngine(listen=deaf).render_stem(_request(tmp_path))

    assert len(studio.calls) == 1
    assert any("not checked" in w for w in result.takes[0].warnings)


def test_cancel_stops_before_the_next_piece(studio, tmp_path):
    request = _request(
        tmp_path,
        segments=[RenderSegment(text="Eins."), RenderSegment(text="Zwei.")],
    )
    with pytest.raises(RuntimeError, match="cancelled"):
        studio.engine().render_stem(request, cancelled=lambda: len(studio.calls) >= 1)
    assert len(studio.calls) == 1


def test_script_tags_become_tags_the_model_knows():
    assert spoken_text("**Nein!** <laugh> Doch. <sigh>  [breath] <wave> [door slams] Ja.") == (
        "Nein! [laughter] Doch. [sigh] [breath] Ja."
    )


def test_the_runtime_is_resolved_again_for_every_stem(studio, monkeypatch, tmp_path):
    resolved: list[int] = []
    monkeypatch.setattr(
        engine_module.runtime_module, "resolve_runtime", lambda: resolved.append(1) or object()
    )
    engine = studio.engine()
    engine.load()  # what EngineManager.get does

    engine.render_stem(_request(tmp_path))
    assert not engine.loaded
    engine.render_stem(_request(tmp_path))

    # Ollama can update underneath a long-running worker.
    assert len(resolved) == 2


def test_among_passing_takes_the_clearest_one_is_selected(studio, tmp_path):
    studio.clarity = [0.97, 0.74, 0.96, 0.95]  # take 1 mumbles its second piece
    request = _request(
        tmp_path,
        candidates=2,
        segments=[RenderSegment(text="Eins."), RenderSegment(text="Zwei.")],
    )

    result = studio.engine().render_stem(request)

    assert result.selected_take_id == result.takes[1].id


class TwoLibraries:
    """Ollama's GGML produces garbage; the downloaded copy may or may not exist."""

    def __init__(self, studio: Studio, monkeypatch, *, fallback: bool) -> None:
        self.rejected: list[str] = []
        self.used: list[str] = []
        self.current = "ollama"
        self.fallback = fallback
        module = engine_module.runtime_module
        monkeypatch.setattr(module, "resolve_runtime", self.resolve)
        monkeypatch.setattr(module, "reject_ggml", lambda r: self.rejected.append(r.ggml_source))
        original = studio.synthesize

        def synthesize(runtime, voice, text, output, **kwargs):
            self.used.append(runtime.ggml_source)
            original(runtime, voice, text, output, **kwargs)

        monkeypatch.setattr(module, "synthesize", synthesize)

    def resolve(self, exclude=frozenset()):
        source = "pinned" if exclude else "ollama"
        if exclude and not self.fallback:
            raise EngineNotAvailable("no other GGML")
        return SimpleNamespace(ggml_source=source, ggml_fingerprint=source)


LONG = "Der Zug nach Hamburg hat heute Verspätung."


def test_broken_ollama_libraries_are_replaced_and_remembered(studio, monkeypatch, tmp_path):
    libraries = TwoLibraries(studio, monkeypatch, fallback=True)
    studio.heard = ["xq", "xq", "xq", LONG]

    result = studio.engine().render_stem(_request(tmp_path, text=LONG))

    assert libraries.used == ["ollama"] * 3 + ["pinned"]
    assert libraries.rejected == ["ollama"]
    assert any("Ollama" in warning for warning in result.takes[0].warnings)


def test_silence_from_ollama_also_triggers_the_fallback(studio, monkeypatch, tmp_path):
    libraries = TwoLibraries(studio, monkeypatch, fallback=True)
    studio.silent_calls = {0, 1, 2}

    studio.engine().render_stem(_request(tmp_path, text=LONG))

    assert libraries.rejected == ["ollama"]


def test_ollama_is_not_blamed_when_the_other_copy_fails_too(studio, monkeypatch, tmp_path):
    libraries = TwoLibraries(studio, monkeypatch, fallback=True)
    studio.heard = ["xq"] * 6

    result = studio.engine().render_stem(_request(tmp_path, text=LONG))

    assert libraries.rejected == []
    assert any("misspoken" in warning for warning in result.takes[0].warnings)


def test_without_another_copy_the_warning_says_how_to_get_one(studio, monkeypatch, tmp_path):
    TwoLibraries(studio, monkeypatch, fallback=False)
    studio.heard = ["xq"] * 3

    result = studio.engine().render_stem(_request(tmp_path, text=LONG))

    assert any("installer" in warning for warning in result.takes[0].warnings)


def test_a_short_mumbled_line_does_not_blame_the_libraries(studio, monkeypatch, tmp_path):
    libraries = TwoLibraries(studio, monkeypatch, fallback=True)
    studio.heard = ["xq"] * 3

    studio.engine().render_stem(_request(tmp_path, text="Nein."))

    assert libraries.used == ["ollama"] * 3 and libraries.rejected == []
