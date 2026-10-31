from __future__ import annotations

import pytest

from drama_studio.cuts import (
    freeze_legacy_cuts,
    normalize_alignment,
    pick_selected_take,
    safe_filename,
    scale_window,
    validate_cut,
)
from drama_studio.models import AlignedLine, AlignmentResult, Take


def _take(take_id: str, duration: float = 10.0, selected: bool = False) -> Take:
    return Take(
        id=take_id,
        file=f"audio/takes/{take_id}.wav",
        engine="fake",
        duration_s=duration,
        sample_rate=24000,
        seed=1,
        selected=selected,
    )


def test_safe_filename():
    assert safe_filename("s1-01") == "s1-01"
    assert safe_filename("a/b c") == "a_b_c"
    assert safe_filename("___") == "line"


def test_scale_window_ratio():
    start, end = scale_window(3.0, 5.0, 12.0, 14.0)
    assert start == pytest.approx(3.5)
    assert end == pytest.approx(5.833)


def test_validate_cut_rejects_out_of_range():
    take = _take("t1", duration=2.0)
    validate_cut("l1", take, 0.0, 1.0, 2.0)
    with pytest.raises(ValueError):
        validate_cut("l1", take, 0.0, 2.5, 2.0)


def test_freeze_legacy_pins_old_selection():
    alignment = AlignmentResult(
        stem_id="s1",
        lines=[AlignedLine(line_id="a", take_id=None, start_s=0.1, end_s=0.4)],
    )
    old = _take("old", selected=True)
    freeze_legacy_cuts(alignment, old)
    assert alignment.lines[0].take_id == "old"


def test_normalize_alignment_fills_legacy():
    selected = _take("sel", selected=True)
    other = _take("other")
    alignment = AlignmentResult(
        stem_id="s1",
        lines=[AlignedLine(line_id="a", take_id=None, start_s=0, end_s=1)],
    )
    normalize_alignment(alignment, [other, selected])
    assert alignment.lines[0].take_id == "sel"


def test_pick_selected_take_honors_result_id():
    takes = [_take("a"), _take("b", selected=True)]
    picked = pick_selected_take(takes, "a")
    assert picked is not None and picked.id == "a"
    # Unknown id falls back to flagged take.
    picked = pick_selected_take(takes, "missing")
    assert picked is not None and picked.id == "b"
