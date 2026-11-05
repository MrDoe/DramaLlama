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
