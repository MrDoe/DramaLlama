from __future__ import annotations

import pytest

from drama_studio.cuts import (
    freeze_legacy_cuts,
    layout_script_order,
    normalize_alignment,
    pick_selected_take,
    safe_filename,
    scale_window,
    seed_positions,
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


def _project_with_script(line_ids, settings_pause_ms=200, settings_gap_ms=500):
    from drama_studio.models import Line, Project, ProjectSettings, Scene, Script

    script = Script(
        title="S",
        scenes=[
            Scene(
                id=1,
                lines=[
                    Line(id=lid, scene=1, speaker="A", text=f"line {lid}")
                    for lid in line_ids
                ],
            )
        ],
    )
    return Project(
        id="p",
        title="P",
        language="en",
        script=script,
        settings=ProjectSettings(pause_ms=settings_pause_ms, scene_gap_ms=settings_gap_ms),
    )


def test_normalize_does_not_seed_per_stem_overlap():
    """Legacy (get) API: normalize must not invent per-stem positions."""
    selected = _take("sel", selected=True)
    alignment = AlignmentResult(
        stem_id="s1",
        lines=[
            AlignedLine(line_id="a", take_id=None, start_s=0, end_s=2),
            AlignedLine(line_id="b", take_id=None, start_s=2, end_s=4),
        ],
    )
    normalize_alignment(alignment, [selected])
    assert alignment.lines[0].position_s is None
    assert alignment.lines[1].position_s is None


def test_layout_script_order_interleaves_stems():
    """Viktoria 1, Andreas 1, Grandmother 1, Viktoria 2, ...

    Positions follow global script order, not a per-stem cursor — the same
    regression the old seed_positions produced (every stem's first line at 0).
    """
    project = _project_with_script(["v1", "a1", "g1", "v2", "a2"], settings_pause_ms=200)
    project.alignments["s-v"] = AlignmentResult(
        stem_id="s-v",
        lines=[
            AlignedLine(line_id="v1", start_s=0, end_s=2, position_s=None),
            AlignedLine(line_id="v2", start_s=2, end_s=4, position_s=None),
        ],
    )
    project.alignments["s-a"] = AlignmentResult(
        stem_id="s-a",
        lines=[AlignedLine(line_id="a1", start_s=0, end_s=1, position_s=None)],
    )
    project.alignments["s-g"] = AlignmentResult(
        stem_id="s-g",
        lines=[AlignedLine(line_id="g1", start_s=0, end_s=3, position_s=None)],
    )
    project.alignments["s-a"].lines.append(
        AlignedLine(line_id="a2", start_s=1, end_s=2, position_s=None)
    )
    layout_script_order(project)
    pos = {
        seg.line_id: seg.position_s
        for al in project.alignments.values()
        for seg in al.lines
    }
    assert pos["v1"] == 0
    assert pos["a1"] == pytest.approx(2 + 0.2)
    assert pos["g1"] == pytest.approx(2 + 0.2 + 1 + 0.2)
    assert pos["v2"] == pytest.approx(2 + 0.2 + 1 + 0.2 + 3 + 0.2)
    assert pos["a2"] == pytest.approx(2 + 0.2 + 1 + 0.2 + 3 + 0.2 + 2 + 0.2)


def test_layout_script_order_keeps_moved_positions():
    project = _project_with_script(["a", "b", "c"], settings_pause_ms=100)
    project.alignments["s1"] = AlignmentResult(
        stem_id="s1",
        lines=[
            AlignedLine(line_id="a", start_s=0, end_s=2, position_s=None),
            AlignedLine(line_id="b", start_s=2, end_s=4, position_s=99.0),
            AlignedLine(line_id="c", start_s=4, end_s=6, position_s=None),
        ],
    )
    layout_script_order(project)
    pos = {seg.line_id: seg.position_s for al in project.alignments.values() for seg in al.lines}
    assert pos["a"] == 0
    assert pos["b"] == 99.0  # user-moved, untouched
    assert pos["c"] == pytest.approx(2 + 0.1 + 2 + 0.1)
