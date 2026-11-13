"""Auto-cut allowance: the aligned words plus the phrase that follows them.

The aligner reports where words start and end. Cutting exactly there removes the
decay after the last consonant, which is what makes every line sound clipped, so
the cut walks outward into measured signal — but stops at silence rather than
padding the timeline with nothing.
"""

from __future__ import annotations

import numpy as np
import pytest

from drama_studio.aligner import (
    HOP_S,
    PAD_TAIL_S,
    envelope,
    padded_window,
    speech_floor,
)

RATE = 24000


def _take(bands: list[tuple[float, float, float]], total_s: float = 4.0) -> np.ndarray:
    """Silence with voiced bands at (start_s, end_s, amplitude), decay included."""
    samples = np.zeros(int(total_s * RATE), dtype=np.float32)
    for start, end, level in bands:
        a, b = int(start * RATE), int(end * RATE)
        window = np.hanning(b - a) if b - a > 8 else np.ones(b - a, dtype=np.float32)
        phase = np.linspace(0, (b - a) * 0.08, b - a, dtype=np.float32)
        samples[a:b] = (window * np.sin(phase * np.pi * 2) * level).astype(np.float32)
    return samples


def _floor(env: np.ndarray) -> float:
    return speech_floor(env)


def test_the_cut_catches_the_decay_after_the_last_word():
    # Voiced 1.0-2.0 s, a decaying tail to 2.3 s, then nothing.
    samples = _take([(1.0, 2.0, 0.4), (2.0, 2.3, 0.12)])
    env = envelope(samples, RATE)
    start, end = padded_window(
        env, 1.0, 2.0, lead_limit=0.9, tail_limit=2.6, floor=_floor(env)
    )
    assert start <= 1.0
    assert end > 2.15, f"the tail was left out: cut ends at {end}"
    assert end < 2.6, "a decaying phrase must not run to the whole allowance"


def test_silence_after_the_phrase_is_not_carried():
    # Voiced 1.0-2.0 s, hard silence from 2.02 s on.
    samples = _take([(1.0, 2.0, 0.4)], total_s=6.0)
    env = envelope(samples, RATE)
    _start, end = padded_window(
        env, 1.0, 2.0, lead_limit=0.9, tail_limit=5.9, floor=_floor(env)
    )
    assert end < 2.3, f"the cut swallowed {end - 2.0:.2f}s of dead air"


def test_a_dip_inside_the_phrase_does_not_end_the_cut():
    # Voiced, a plosive gap, then the phrase goes on loudly: the old
    # stop-at-the-first-dip rule ended the cut in the middle of the word.
    samples = _take([(1.0, 1.5, 0.4), (1.72, 2.35, 0.35)])
    env = envelope(samples, RATE)
    _start, end = padded_window(
        env, 1.0, 1.5, lead_limit=0.9, tail_limit=2.6, floor=_floor(env)
    )
    assert end > 2.2, f"the cut stopped at the dip, losing the rest of the line ({end})"


def test_the_lead_starts_at_the_first_sound_not_the_first_dip():
    samples = _take([(0.62, 0.8, 0.25), (0.98, 2.0, 0.4)])
    env = envelope(samples, RATE)
    start, _end = padded_window(
        env, 1.0, 2.0, lead_limit=0.5, tail_limit=2.4, floor=_floor(env)
    )
    assert start < 0.7, f"the breath before the first word was cut off ({start})"


def test_the_allowance_never_shortens_a_cut():
    silence = np.zeros(4 * RATE, dtype=np.float32)
    env = envelope(silence, RATE)
    start, end = padded_window(
        env, 1.0, 2.0, lead_limit=0.5, tail_limit=3.5, floor=speech_floor(env)
    )
    # Hop quantisation can round the end out by one 20 ms block; that is the
    # resolution of the envelope, not the allowance being spent on silence.
    assert start == pytest.approx(1.0, abs=HOP_S)
    assert end == pytest.approx(2.0, abs=HOP_S * 2)


def test_the_tail_stops_where_the_next_line_starts():
    # Continuous voicing across the whole take: nothing stops the walk but the
    # neighbour guard.
    samples = _take([(0.0, 4.0, 0.4)], total_s=4.0)
    env = envelope(samples, RATE)
    _start, end = padded_window(
        env, 1.0, 2.0, lead_limit=0.9, tail_limit=2.15, floor=_floor(env)
    )
    assert end <= 2.15 + 1e-6


def test_the_lead_stops_at_the_previous_cut():
    samples = _take([(0.0, 4.0, 0.4)], total_s=4.0)
    env = envelope(samples, RATE)
    start, _end = padded_window(
        env, 2.0, 3.0, lead_limit=1.9, tail_limit=3.6, floor=_floor(env)
    )
    assert start >= 1.9


def test_envelope_has_one_hop_per_window():
    samples = np.ones(RATE, dtype=np.float32)  # exactly one second
    env = envelope(samples, RATE)
    assert env.size == int(1.0 / HOP_S)
    assert float(env[0]) == pytest.approx(1.0, abs=1e-5)


def test_a_missing_envelope_leaves_the_cut_alone():
    env = envelope(np.zeros(0, dtype=np.float32), RATE)
    assert padded_window(env, 1.0, 2.0, lead_limit=0.0, tail_limit=3.0, floor=0.1) == (
        1.0,
        2.0,
    )


def test_alignment_leaves_timeline_positions_to_the_layout(tmp_path, monkeypatch):
    """A stem's own clock must never be written into `position_s`.

    Every stem starts counting at zero, and the mixer treats that field as a slot
    on the project timeline, so a per-stem cursor lands every scene's first line
    at t = 0 and the play mixes on top of itself. The contract is None = "derive
    script order" — asserted here at the producer, not only at the repair.
    """
    import numpy as np
    import soundfile as sf

    from drama_studio.aligner import WhisperAligner
    from drama_studio.models import Line

    rate = 24000
    take = tmp_path / "stem.wav"
    sf.write(str(take), (0.2 * np.sin(2 * np.pi * 200 * np.arange(rate * 3) / rate)).astype(np.float32), rate)

    aligner = WhisperAligner()
    monkeypatch.setattr(aligner, "load", lambda: None)
    aligner._align_cuts = lambda samples, sr, groups, asr, language=None: (
        [(0.0, 1.2), (1.5, 2.8)],
        [0.9, 0.9],
    )
    lines = [
        Line(id="s1-01", scene=1, speaker="A", text="First."),
        Line(id="s1-02", scene=1, speaker="A", text="Second."),
    ]
    result = aligner.align(take, lines, tmp_path / "out", stem_id="s1-a")
    assert [cut.position_s for cut in result.lines] == [None, None]
    # Windows are still real cuts, in take order, and the allowance may close a
    # gap between two lines but never overlap them.
    assert result.lines[0].start_s == pytest.approx(0.0, abs=HOP_S)
    assert result.lines[0].end_s <= result.lines[1].start_s + 1e-6
    assert result.lines[1].end_s <= 2.8 + PAD_TAIL_S
