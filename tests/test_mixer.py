from __future__ import annotations

import numpy as np
import soundfile as sf

from drama_studio.mixer import build_mix
from drama_studio.models import (
    Character,
    Cue,
    Line,
    Project,
    ProjectSettings,
    Scene,
    Script,
)
from drama_studio.sfx import SfxItem


def make_project(tmp_path) -> tuple[Project, np.ndarray]:
    script = Script(
        title="Mix Test",
        language="en",
        characters={"MARA": Character(id="MARA", name="Mara")},
        scenes=[
            Scene(
                id=1,
                heading="SCENE 1",
                lines=[
                    Line(id="s1-01", scene=1, speaker="MARA", text="Hello."),
                    Line(id="s1-02", scene=1, speaker="MARA", text="Again."),
                ],
            )
        ],
    )
    project = Project(
        id="mix-test",
        title="Mix Test",
        language="en",
        raw_script="",
        script=script,
        settings=ProjectSettings(pause_ms=100, scene_gap_ms=200),
    )
    directory = tmp_path / "mix-test"
    (directory / "lines").mkdir(parents=True)
    sf.write(directory / "lines" / "s1-01.wav", np.zeros(24000, dtype=np.float32), 24000)
    sf.write(directory / "lines" / "s1-02.wav", np.zeros(24000, dtype=np.float32), 24000)
    return project, np.zeros(24000, dtype=np.float32)


def test_legacy_per_stem_positions_do_not_stack_the_scenes(tmp_path):
    """A cut sheet that opens two scenes at t=0 is a bug, not a placement.

    The aligner used to write a cursor that started at 0 inside each stem, so
    every scene's first line landed at the top of the timeline and the mix played
    the whole drama at once. The positions are stored, so `build_mix` has to
    notice the pattern and lay the script out in order.
    """
    from drama_studio.models import AlignedLine, AlignmentResult

    def tone(seconds: float, level: float) -> np.ndarray:
        n = int(seconds * 24000)
        return (level * np.sin(2 * np.pi * 220 * np.arange(n) / 24000)).astype(np.float32)

    script = Script(
        title="Stacked",
        language="en",
        characters={"A": Character(id="A", name="A"), "B": Character(id="B", name="B")},
        scenes=[
            Scene(id=1, heading="SCENE 1", lines=[Line(id="s1-01", scene=1, speaker="A", text="First.")]),
            Scene(id=2, heading="SCENE 2", lines=[Line(id="s2-01", scene=2, speaker="B", text="Second.")]),
        ],
    )
    project = Project(
        id="stacked",
        title="Stacked",
        language="en",
        raw_script="",
        script=script,
        settings=ProjectSettings(pause_ms=100, scene_gap_ms=200),
    )
    # Both stems open at 0.0 — the legacy signature.
    project.alignments = {
        "s1-a": AlignmentResult(
            stem_id="s1-a",
            aligner="whisper-large-v3",
            lines=[AlignedLine(id="s1-01", line_id="s1-01", start_s=0.0, end_s=1.0, position_s=0.0)],
        ),
        "s2-b": AlignmentResult(
            stem_id="s2-b",
            aligner="whisper-large-v3",
            lines=[AlignedLine(id="s2-01", line_id="s2-01", start_s=0.0, end_s=1.0, position_s=0.0)],
        ),
    }
    directory = tmp_path / "stacked"
    (directory / "lines").mkdir(parents=True)
    # Scene 1 is silent, scene 2 is a tone. Stacked, the tone lands at t=0;
    # sequential, it only appears after the gap. Comparing windows survives the
    # loudness normaliser, absolute levels do not.
    sf.write(directory / "lines" / "s1-01.wav", np.zeros(24000, dtype=np.float32), 24000)
    sf.write(directory / "lines" / "s2-01.wav", tone(1.0, 0.9), 24000)

    result = build_mix(project, directory, lambda _: None)
    master, sr = sf.read(str(directory / result.file), dtype="float32")
    if master.ndim > 1:
        master = master.mean(axis=1)

    def rms(a: int, b: int) -> float:
        return float(np.sqrt((master[int(a * sr) : int(b * sr)] ** 2).mean()))

    peak = max(rms(0.0, 0.5), rms(1.5, 2.0), 1e-9)
    # The tone must not be at the top of the timeline, where scene 1 sits.
    assert rms(0.1, 0.9) < 0.05 * peak, "scene 2 played on top of scene 1"
    # One second of scene 1, then the pause and the scene gap, then scene 2.
    start_2 = 1.0 + project.settings.pause_ms / 1000 + project.settings.scene_gap_ms / 1000
    assert rms(start_2 + 0.1, start_2 + 0.9) > 0.5 * peak, "scene 2 never arrived"
    assert result.duration_s > start_2


def _two_scene_project(tmp_path):
    """Silent dialogue over two scenes; scene 1 runs 0-1 s, scene 2 from 1.3 s."""
    script = Script(
        title="Cue Timeline",
        language="en",
        characters={"A": Character(id="A", name="A"), "B": Character(id="B", name="B")},
        scenes=[
            Scene(id=1, heading="SCENE 1", lines=[Line(id="s1-01", scene=1, speaker="A", text="First.")]),
            Scene(id=2, heading="SCENE 2", lines=[Line(id="s2-01", scene=2, speaker="B", text="Second.")]),
        ],
    )
    project = Project(
        id="cue-timeline",
        title="Cue Timeline",
        language="en",
        raw_script="",
        script=script,
        settings=ProjectSettings(pause_ms=100, scene_gap_ms=200),
    )
    directory = tmp_path / "cue-timeline"
    (directory / "lines").mkdir(parents=True)
    silence = np.zeros(24000, dtype=np.float32)
    sf.write(directory / "lines" / "s1-01.wav", silence, 24000)
    sf.write(directory / "lines" / "s2-01.wav", silence, 24000)
    bed = tmp_path / "bed.wav"
    sf.write(bed, (0.5 * np.sin(2 * np.pi * 440 * np.arange(48000) / 24000)).astype(np.float32), 24000)
    item = SfxItem(
        id="bed",
        name="bed",
        path=str(bed),
        folder="test",
        duration_s=2.0,
        sample_rate=24000,
        channels=1,
        size_bytes=bed.stat().st_size,
        mtime=bed.stat().st_mtime,
        tags=["music"],
    )
    def lookup(sfx_id):
        return item if sfx_id == "bed" else None

    return project, directory, lookup


def _window_rms(data, sr, lo, hi):
    return float(np.sqrt((data[int(lo * sr) : int(hi * sr)] ** 2).mean()))


def test_scene_audition_is_trimmed_to_the_scene(tmp_path):
    """A scene audition is scene-relative, like the motion paths it clocks.

    The Stage transport samples keyframes on the scene's own clock (`t=0` is
    the first sound in that scene). Rendering the whole play into the preview
    file made every scene after the first move at the wrong time and start
    playing the previous scene, so `only_scene` filters and rebases.
    """
    from drama_studio.models import AlignedLine, AlignmentResult

    script = Script(
        title="Preview",
        language="en",
        characters={"A": Character(id="A", name="A"), "B": Character(id="B", name="B")},
        scenes=[
            Scene(id=1, heading="SCENE 1", lines=[Line(id="s1-01", scene=1, speaker="A", text="First.")]),
            Scene(id=2, heading="SCENE 2", lines=[Line(id="s2-01", scene=2, speaker="B", text="Second.")]),
        ],
    )
    project = Project(
        id="preview",
        title="Preview",
        language="en",
        raw_script="",
        script=script,
        settings=ProjectSettings(pause_ms=100, scene_gap_ms=200),
    )
    project.alignments = {
        "s1-a": AlignmentResult(
            stem_id="s1-a",
            aligner="whisper-large-v3",
            lines=[AlignedLine(id="s1-01", line_id="s1-01", start_s=0.0, end_s=1.0, position_s=0.0)],
        ),
        "s2-b": AlignmentResult(
            stem_id="s2-b",
            aligner="whisper-large-v3",
            lines=[AlignedLine(id="s2-01", line_id="s2-01", start_s=0.0, end_s=1.0, position_s=1.3)],
        ),
    }
    directory = tmp_path / "preview"
    (directory / "lines").mkdir(parents=True)
    sf.write(directory / "lines" / "s1-01.wav", np.zeros(24000, dtype=np.float32), 24000)
    tone = (0.9 * np.sin(2 * np.pi * 220 * np.arange(24000) / 24000)).astype(np.float32)
    sf.write(directory / "lines" / "s2-01.wav", tone, 24000)

    result = build_mix(project, directory, lambda _: None, name="preview_s2", only_scene=2)
    assert result.file == "mix/preview_s2.wav"
    # One second of scene 2 plus the mixer tail — not the 4.3 s full play.
    assert 1.0 <= result.duration_s <= 3.5, result.duration_s
    data, sr = sf.read(str(directory / result.file), dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    # The scene starts at sample 0: no scene-1 silence and no 1.3 s offset.
    assert _window_rms(data, sr, 0.1, 0.9) > 0.1, "the scene did not start at 0"


def test_freeform_music_ignores_the_scene_of_its_anchor(tmp_path):
    """A music cue placed on the timeline belongs to the timeline.

    Anchored cues are clipped to the scene their anchor line is in, which is right
    for a footstep and wrong for a score: a bed dragged or set to start in scene 2
    while still pointing at a scene 1 line used to be measured against scene 1's
    end, come out non-positive, and be skipped — silently, with no warning.
    """
    project, directory, lookup = _two_scene_project(tmp_path)
    project.cues = [
        Cue(
            id="score",
            kind="music",
            sfx_id="bed",
            name="Score",
            anchor_line_id="s1-01",
            position_s=1.4,
            gain_db=-6,
            loop=True,
        )
    ]
    result = build_mix(project, directory, lookup)
    assert result.cue_count == 1, result.warnings
    assert result.warnings == []
    data, sr = sf.read(str(directory / result.file), dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    # Scene 1 ends around 1.0 s, so a scene-bound bed would be silent here.
    assert _window_rms(data, sr, 1.5, 2.2) > 0.05, "the bed never reached scene 2"


def test_freeform_music_end_bounds_the_bed(tmp_path):
    project, directory, lookup = _two_scene_project(tmp_path)
    project.cues = [
        Cue(
            id="score",
            kind="music",
            sfx_id="bed",
            name="Score",
            anchor_line_id=None,
            position_s=0.2,
            end_s=0.8,
            gain_db=-6,
            loop=True,
        )
    ]
    result = build_mix(project, directory, lookup)
    assert result.cue_count == 1
    data, sr = sf.read(str(directory / result.file), dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    assert _window_rms(data, sr, 0.3, 0.7) > 0.05, "the bed never played"
    assert _window_rms(data, sr, 1.5, 2.1) < 0.01, "the bed ran past its end"


def test_anchored_cue_still_clips_to_its_scene(tmp_path):
    """The compatibility clause: no position means the old rule, unchanged."""
    project, directory, lookup = _two_scene_project(tmp_path)
    project.cues = [
        Cue(
            id="score",
            kind="music",
            sfx_id="bed",
            name="Score",
            anchor_line_id="s1-01",
            gain_db=-6,
            loop=True,
        )
    ]
    result = build_mix(project, directory, lookup)
    assert result.cue_count == 1
    data, sr = sf.read(str(directory / result.file), dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    assert _window_rms(data, sr, 0.2, 0.8) > 0.05
    assert _window_rms(data, sr, 1.5, 2.1) < 0.01, "an anchored bed escaped its scene"


def test_cue_off_the_end_of_the_timeline_warns(tmp_path):
    """A cue nobody can hear must be a warning, not a quiet omission."""
    project, directory, lookup = _two_scene_project(tmp_path)
    project.cues = [
        Cue(
            id="ghost",
            kind="music",
            sfx_id="bed",
            name="Ghost",
            anchor_line_id=None,
            position_s=900.0,
            loop=True,
        )
    ]
    result = build_mix(project, directory, lookup)
    assert result.cue_count == 0
    assert any("Ghost" in w for w in result.warnings), result.warnings


def test_build_mix_without_cues(tmp_path):
    project, _ = make_project(tmp_path)
    result = build_mix(project, tmp_path / "mix-test", lambda _: None)
    master = tmp_path / "mix-test" / result.file
    assert master.exists()
    assert result.line_count == 2
    assert result.duration_s > 2
    assert result.warnings == []


def test_disabled_segment_keeps_slot_but_stays_silent(tmp_path):
    """Suno-style clip disable: timing unchanged, no audio placed."""
    from drama_studio.models import AlignedLine, AlignmentResult

    project, _ = make_project(tmp_path)
    directory = tmp_path / "mix-test"
    tone = (
        0.5 * np.sin(2 * np.pi * 220 * np.arange(24000) / 24000)
    ).astype(np.float32)
    sf.write(directory / "lines" / "s1-01.wav", tone, 24000)

    def mix_with(enabled: bool):
        project.alignments = {
            "stem": AlignmentResult(
                stem_id="stem",
                lines=[
                    AlignedLine(
                        id="s1-01",
                        line_id="s1-01",
                        start_s=0,
                        end_s=1.0,
                        position_s=None,
                        enabled=enabled,
                    ),
                    AlignedLine(
                        id="s1-02",
                        line_id="s1-02",
                        start_s=0,
                        end_s=1.0,
                        position_s=None,
                    ),
                ],
            )
        }
        result = build_mix(project, directory, lambda _: None)
        data, _ = sf.read(str(directory / result.file), dtype="float32")
        return result, np.asarray(data)

    on_result, on_data = mix_with(True)
    off_result, off_data = mix_with(False)
    # Slot timing is identical (disabled keeps its place on the timeline).
    assert off_result.duration_s == on_result.duration_s
    # First line's second is loud when enabled, silent when disabled.
    assert float(np.max(np.abs(on_data[:24000]))) > 0.1
    assert float(np.max(np.abs(off_data[:24000]))) < 1e-6


def test_build_mix_with_bed(tmp_path):
    project, _ = make_project(tmp_path)
    sfx_path = tmp_path / "rain.wav"
    sf.write(sfx_path, np.zeros(48000, dtype=np.float32), 24000)
    item = SfxItem(
        id="rain",
        name="rain",
        path=str(sfx_path),
        folder="test",
        duration_s=2.0,
        sample_rate=24000,
        channels=1,
        size_bytes=sfx_path.stat().st_size,
        mtime=sfx_path.stat().st_mtime,
        tags=["rain"],
    )
    project.cues = [
        Cue(
            id="cue1",
            kind="bed",
            sfx_id="rain",
            name="rain",
            anchor_line_id="s1-01",
            gain_db=-18,
            loop=True,
        )
    ]
    result = build_mix(project, tmp_path / "mix-test", lambda sfx_id: item if sfx_id == "rain" else None)
    assert result.cue_count == 1
    assert result.mp3 is not None
    assert (tmp_path / "mix-test" / result.mp3).exists()
