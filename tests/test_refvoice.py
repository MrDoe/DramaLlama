from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

from drama_studio import refvoice
from drama_studio.audio import write_mono
from drama_studio.transcriber import TranscriptResult, TranscriptWord


def _tone(seconds: float, frequency: float = 150.0, sr: int = 24000) -> np.ndarray:
    t = np.arange(int(seconds * sr), dtype=np.float32) / sr
    return (0.2 * np.sin(2 * np.pi * frequency * t)).astype(np.float32)


def _clip_with_silence() -> np.ndarray:
    sr = 24000
    silence_a = np.zeros(int(0.5 * sr), dtype=np.float32)
    speech = _tone(8.0)
    silence_b = np.zeros(int(3.5 * sr), dtype=np.float32)
    return np.concatenate([silence_a, speech, silence_b])


def _words(count: int = 10, step: float = 0.8, start: float = 0.5) -> list[TranscriptWord]:
    return [
        TranscriptWord(
            word=f"word{index}",
            start=start + index * step,
            end=start + (index + 1) * step,
            probability=0.9,
        )
        for index in range(count)
    ]


class FakeTranscriber:
    def __init__(self, transcript: TranscriptResult | None = None) -> None:
        self.transcript = transcript or TranscriptResult(
            words=_words(),
            text="hello world this is a reference clip",
            confidence=0.9,
            no_speech_prob=0.05,
        )
        self.calls = 0

    def transcribe(self, samples, sample_rate, *, word_timestamps: bool = True):
        self.calls += 1
        return self.transcript


def test_select_window_prefers_target_duration():
    start, end = refvoice.select_window(_words(10), 12.0)
    duration = end - start
    assert 6.5 <= duration <= 10.5
    assert 7.0 <= duration <= 9.0


def test_select_window_keeps_internal_gap_when_it_is_the_only_fit():
    words = [
        TranscriptWord("a", 0.0, 2.0, 0.9),
        TranscriptWord("b", 8.0, 10.0, 0.9),
    ]
    start, end = refvoice.select_window(words, 10.2)
    assert start == 0.0
    assert end >= 10.0


def test_select_window_rejects_clip_without_enough_speech():
    with pytest.raises(refvoice.NoUsableWindowError):
        refvoice.select_window(_words(2, step=1.0), 2.0)


def test_select_window_splits_long_word_runs():
    words = _words(20, step=1.0, start=0.0)
    start, end = refvoice.select_window(words, 20.0)
    assert 6.5 <= end - start <= 10.5


def test_measure_f0_returns_median_and_range():
    median, span = refvoice.measure_f0(_tone(2.0), 24000)
    assert median is not None
    assert 130.0 <= median <= 170.0
    assert span is not None
    assert span[0] <= median <= span[1]


def test_prepare_reference_writes_pcm16_24k(tmp_path, monkeypatch):
    source = tmp_path / "sample.wav"
    write_mono(source, _clip_with_silence(), 24000)
    destination = tmp_path / "out" / "ref.wav"

    def fake_decode(input_path, output_path, **kwargs):
        shutil.copyfile(input_path, output_path)
        return Path(output_path)

    monkeypatch.setattr(refvoice, "decode_to_24k", fake_decode)
    fake = FakeTranscriber()
    prepared = refvoice.prepare_reference(
        source,
        destination,
        language="en",
        transcriber=fake,
    )

    import soundfile as sf

    info = sf.info(str(destination))
    assert info.samplerate == 24000
    assert info.channels == 1
    assert info.subtype == "PCM_16"
    assert 6.0 <= prepared.duration_s <= 11.0
    assert prepared.ref_text
    assert prepared.transcript_confidence == pytest.approx(0.9)
    assert prepared.window_start_s >= 0.0
    assert prepared.window_end_s > prepared.window_start_s
    assert fake.calls == 2


def test_prepare_reference_rejects_short_input(tmp_path):
    source = tmp_path / "short.wav"
    write_mono(source, _tone(3.0), 24000)
    with pytest.raises(refvoice.ReferencePreparationError):
        refvoice.prepare_reference(
            source,
            tmp_path / "out.wav",
            language="en",
            transcriber=FakeTranscriber(),
        )


def test_prepare_reference_cuts_long_input(tmp_path, monkeypatch):
    source = tmp_path / "long.wav"
    write_mono(source, np.concatenate([_clip_with_silence() for _ in range(6)]), 24000)
    destination = tmp_path / "out" / "ref.wav"

    def fake_decode(input_path, output_path, **kwargs):
        shutil.copyfile(input_path, output_path)
        return Path(output_path)

    monkeypatch.setattr(refvoice, "decode_to_24k", fake_decode)
    prepared = refvoice.prepare_reference(
        source,
        destination,
        language="en",
        transcriber=FakeTranscriber(),
    )
    assert 6.0 <= prepared.duration_s <= 11.0
    assert prepared.window_start_s < prepared.window_end_s
    assert destination.exists()
    assert any("source clip is" in warning for warning in prepared.warnings)


def test_prepare_reference_rejects_over_two_minutes(tmp_path, monkeypatch):
    source = tmp_path / "toolong.wav"
    write_mono(source, _tone(1.0), 24000)
    monkeypatch.setattr(refvoice, "probe_duration", lambda path: 121.0)
    with pytest.raises(refvoice.ReferencePreparationError, match="2 minutes"):
        refvoice.prepare_reference(
            source,
            tmp_path / "out.wav",
            language="en",
            transcriber=FakeTranscriber(),
        )


def test_prepare_reference_rejects_no_speech(tmp_path, monkeypatch):
    source = tmp_path / "sample.wav"
    write_mono(source, _clip_with_silence(), 24000)

    def fake_decode(input_path, output_path, **kwargs):
        shutil.copyfile(input_path, output_path)
        return Path(output_path)

    monkeypatch.setattr(refvoice, "decode_to_24k", fake_decode)
    empty = FakeTranscriber(
        TranscriptResult(words=[], text="", confidence=None, no_speech_prob=0.9)
    )
    with pytest.raises(refvoice.NoSpeechError):
        refvoice.prepare_reference(
            source,
            tmp_path / "out.wav",
            language="en",
            transcriber=empty,
        )
