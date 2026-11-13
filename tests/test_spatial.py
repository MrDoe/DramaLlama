"""Spatial stage: model validation, geometry helpers and room rendering.

Tests that need the native library are skipped when it is absent, so a checkout
without a C toolchain still runs the whole suite.
"""

from __future__ import annotations

import numpy as np
import pytest
import soundfile as sf

from drama_studio import spatial
from drama_studio.models import (
    Character,
    Line,
    Project,
    ProjectSettings,
    Scene,
    SceneStage,
    Script,
    StageActor,
    StageListener,
    StagePath,
    StageProject,
    StageRoom,
)

needs_native = pytest.mark.skipif(not spatial.available(), reason="spatial renderer not built")

SR = 48000
SPEED_OF_SOUND = 343.0


def room(**overrides) -> StageRoom:
    return StageRoom(**overrides)


def stage_with(*actors: StageActor, **kwargs) -> StageProject:
    enabled = kwargs.pop("enabled", True)
    scene = SceneStage(
        room=kwargs.pop("room", room()),
        listener=kwargs.pop("listener", StageListener(position_m=(3.0, 4.0, 1.6))),
        actors=list(actors),
        paths=kwargs.pop("paths", []),
    )
    return StageProject(enabled=enabled, scenes={"1": scene}, **kwargs)


# ------------------------------------------------------------------- model


def test_stage_defaults_are_backwards_compatible():
    """A project without a stage loads and mixes exactly as before."""
    assert Project.model_fields["stage"].default is None


def test_room_dimensions_are_bounded():
    with pytest.raises(ValueError):
        room(dims_m=(0.2, 5.0, 3.0))
    with pytest.raises(ValueError):
        room(dims_m=(6.0, 5.0, 9.0))


def test_wall_absorption_is_bounded():
    with pytest.raises(ValueError):
        room(absorption=(1.5, 0.25, 0.25, 0.25, 0.25, 0.25))


def test_scene_rejects_more_than_sixteen_sources():
    actors = [
        StageActor(speaker=f"SPK{i}", position_m=(1.0, 1.0, 1.5)) for i in range(17)
    ]
    with pytest.raises(ValueError):
        SceneStage(actors=actors)


def test_scene_rejects_duplicate_speakers():
    with pytest.raises(ValueError):
        SceneStage(
            actors=[
                StageActor(speaker="MARA", position_m=(1.0, 1.0, 1.5)),
                StageActor(speaker="MARA", position_m=(2.0, 1.0, 1.5)),
            ]
        )


def test_actor_position_must_be_inside_the_room():
    with pytest.raises(ValueError):
        SceneStage(room=room(dims_m=(4.0, 4.0, 3.0)), actors=[StageActor(speaker="MARA", position_m=(9.0, 1.0, 1.5))])


def test_listener_must_be_inside_the_room():
    with pytest.raises(ValueError):
        SceneStage(
            room=room(dims_m=(4.0, 4.0, 3.0)),
            listener=StageListener(position_m=(3.0, 9.0, 1.6)),
            actors=[StageActor(speaker="MARA", position_m=(1.0, 1.0, 1.5))],
        )


def test_path_keyframes_must_be_sorted():
    with pytest.raises(ValueError):
        StagePath(speaker="MARA", keys=[(1.0, 1, 1, 1), (0.5, 1, 1, 1)])


def test_direct_blend_is_bounded():
    with pytest.raises(ValueError):
        StageActor(speaker="MARA", position_m=(1, 1, 1), direct=1.4)


# ------------------------------------------------------------------ geometry


def test_sample_path_interpolates_linearly():
    keys = [(0.0, 1.0, 1.0, 1.5), (10.0, 3.0, 1.0, 1.5)]
    assert spatial.sample_path(keys, 5.0) == pytest.approx((2.0, 1.0, 1.5))


def test_sample_path_holds_endpoints_outside_the_key_range():
    keys = [(2.0, 1.0, 2.0, 1.5), (8.0, 5.0, 2.0, 1.5)]
    assert spatial.sample_path(keys, 0.0) == pytest.approx((1.0, 2.0, 1.5))
    assert spatial.sample_path(keys, 99.0) == pytest.approx((5.0, 2.0, 1.5))


def test_sample_path_without_keys_is_none():
    assert spatial.sample_path([], 1.0) is None


def test_positions_are_clamped_into_the_room():
    scene = SceneStage(
        room=room(dims_m=(4.0, 4.0, 2.0)),
        actors=[StageActor(speaker="MARA", position_m=(1.0, 1.0, 1.5))],
        paths=[StagePath(speaker="MARA", keys=[(0.0, 99.0, 99.0, 99.0)])],
    )
    positions, _gains, _direct = spatial.source_state_at(scene, 0.0)
    assert positions[0][0] <= 4.0
    assert positions[0][1] <= 4.0
    assert positions[0][2] <= 2.0


def test_actor_gain_is_linear():
    scene = SceneStage(actors=[StageActor(speaker="MARA", position_m=(1, 1, 1), gain_db=-6.0)])
    _positions, gains, _direct = spatial.source_state_at(scene, 0.0)
    assert float(gains[0]) == pytest.approx(10 ** (-6 / 20), rel=1e-5)


def test_all_wet_skips_the_dry_pass():
    wet = SceneStage(actors=[StageActor(speaker="A", position_m=(1, 1, 1))])
    blended = SceneStage(actors=[StageActor(speaker="A", position_m=(1, 1, 1), direct=0.5)])
    assert spatial.all_wet(wet) is True
    assert spatial.all_wet(blended) is False


def test_staged_scenes_ignores_empty_and_unparsable_entries():
    stage = StageProject(
        enabled=True,
        scenes={
            "1": SceneStage(actors=[StageActor(speaker="A", position_m=(1, 1, 1))]),
            "2": SceneStage(actors=[]),
            "not-a-number": SceneStage(actors=[StageActor(speaker="A", position_m=(1, 1, 1))]),
        },
    )
    assert spatial.staged_scenes(stage) == [1]


def test_disabled_stage_stages_nothing():
    stage = stage_with(StageActor(speaker="A", position_m=(1, 1, 1)), enabled=False)
    assert spatial.is_enabled(stage) is False


# ------------------------------------------------------------------ rendering


@needs_native
def test_direct_path_arrives_at_the_speed_of_sound():
    scene = SceneStage(
        room=room(dims_m=(8.0, 8.0, 3.0)),
        listener=StageListener(position_m=(4.0, 4.0, 1.5)),
        actors=[StageActor(speaker="MARA", position_m=(4.0, 4.0 - 3.43, 1.5))],
    )
    with spatial.RoomSimulator(scene, SR, 1) as sim:
        sim.apply_scene_state(scene, 0.0)
        click = np.zeros((1, 4800), dtype=np.float32)
        click[0, 0] = 1.0
        bed = sim.process(click)
        energy = np.sqrt((bed**2).sum(axis=0))
        onset = int(np.argmax(energy > energy.max() * 0.05))
    expected = int(3.43 / SPEED_OF_SOUND * SR)
    assert abs(onset - expected) < SR * 0.003


@needs_native
def test_front_source_decodes_centred_and_side_source_to_that_side():
    listener = (4.0, 4.0, 1.5)
    distance = 3.0

    def balance(offset):
        scene = SceneStage(
            room=room(dims_m=(8.0, 8.0, 3.0)),
            listener=StageListener(position_m=listener),
            actors=[StageActor(speaker="MARA", position_m=tuple(c + o for c, o in zip(listener, offset)))],
        )
        with spatial.RoomSimulator(scene, SR, 1) as sim:
            sim.apply_scene_state(scene, 0.0)
            click = np.zeros((1, 4800), dtype=np.float32)
            click[0, 0] = 1.0
            stereo = sim.decode_stereo(sim.process(click))
        left, right = float(np.abs(stereo[0]).max()), float(np.abs(stereo[1]).max())
        return (left - right) / (left + right)

    # Front wall is -y, so a source toward it is straight ahead.
    assert abs(balance((0.0, -distance, 0.0))) < 0.15
    # +x is the listener's left at the default facing.
    assert balance((distance, 0.0, 0.0)) > 0.3
    assert balance((-distance, 0.0, 0.0)) < -0.3


@needs_native
def test_listener_facing_recentres_a_side_source():
    listener = (3.0, 2.5, 1.5)
    scene = SceneStage(
        room=room(dims_m=(8.0, 8.0, 3.0)),
        listener=StageListener(position_m=listener, facing_deg=90.0),
        actors=[StageActor(speaker="MARA", position_m=(6.0, 2.5, 1.5))],
    )
    with spatial.RoomSimulator(scene, SR, 1) as sim:
        sim.apply_scene_state(scene, 0.0)
        click = np.zeros((1, 4800), dtype=np.float32)
        click[0, 0] = 1.0
        stereo = sim.decode_stereo(sim.process(click))
    left, right = float(np.abs(stereo[0]).max()), float(np.abs(stereo[1]).max())
    assert abs((left - right) / (left + right)) < 0.2


@needs_native
def test_render_scene_preserves_length_and_is_not_silent():
    scene = SceneStage(
        room=room(dims_m=(6.0, 5.0, 3.0)),
        listener=StageListener(position_m=(3.0, 4.0, 1.6)),
        actors=[
            StageActor(speaker="MARA", position_m=(2.0, 2.0, 1.5)),
            StageActor(speaker="ANDREAS", position_m=(4.0, 2.0, 1.5)),
        ],
    )
    tone = (0.3 * np.sin(2 * np.pi * 220 * np.arange(SR) / SR)).astype(np.float32)
    sources = np.stack([tone, np.roll(tone, SR // 2)])
    with spatial.RoomSimulator(scene, SR, 1) as sim:
        bed, dry = spatial.render_scene(sim, scene, sources, ["MARA", "ANDREAS"], 0.0, tail_s=0.2)
    assert bed.shape[0] == spatial.sh_channels(1)
    assert bed.shape[1] >= sources.shape[1]
    assert float(np.abs(bed).max()) > 0.01
    # Fully wet by default: nothing is diverted to the dry path.
    assert float(np.abs(dry).max()) == 0.0


@needs_native
def test_partial_direct_diverts_audio_to_the_centre():
    scene = SceneStage(
        room=room(dims_m=(6.0, 5.0, 3.0)),
        actors=[StageActor(speaker="NARRATOR", position_m=(3.0, 2.0, 1.5), direct=0.0)],
    )
    tone = (0.3 * np.sin(2 * np.pi * 220 * np.arange(SR) / SR)).astype(np.float32)
    with spatial.RoomSimulator(scene, SR, 1) as sim:
        _bed, dry = spatial.render_scene(sim, scene, tone[None, :], ["NARRATOR"], 0.0)
    assert float(np.abs(dry).max()) > 0.1


@needs_native
def test_a_followed_source_moves_over_time():
    scene = SceneStage(
        room=room(dims_m=(10.0, 8.0, 3.0)),
        listener=StageListener(position_m=(5.0, 6.0, 1.6)),
        actors=[StageActor(speaker="MARA", position_m=(5.0, 6.0, 1.5))],
        paths=[StagePath(speaker="MARA", keys=[(0.0, 2.0, 6.0, 1.5), (2.0, 8.0, 6.0, 1.5)])],
    )
    tone = (0.3 * np.sin(2 * np.pi * 220 * np.arange(2 * SR) / SR)).astype(np.float32)
    # Gate the tone so the near half and the far half can be compared.
    gated = tone.copy()
    gated[SR:] = 0.0
    with spatial.RoomSimulator(scene, SR, 1) as sim:
        bed, _dry = spatial.render_scene(sim, scene, gated[None, :], ["MARA"], 0.0)
        near = float(np.abs(bed[:, : SR // 2]).max())
    gated_far = np.zeros_like(tone)
    gated_far[SR:] = tone[SR:]
    with spatial.RoomSimulator(scene, SR, 1) as sim:
        bed_far, _dry = spatial.render_scene(sim, scene, gated_far[None, :], ["MARA"], 0.0)
        far = float(np.abs(bed_far[:, SR:]).max())
    # The actor walks from 3 m away to 3 m away on the other side, but passes
    # through the middle: the first half must be markedly quieter.
    assert near < far


@needs_native
def test_unstaged_speaker_row_is_left_silent_instead_of_mispitched():
    scene = SceneStage(
        room=room(),
        actors=[StageActor(speaker="MARA", position_m=(1.0, 1.0, 1.5))],
    )
    tone = np.zeros((1, 4800), dtype=np.float32)
    with spatial.RoomSimulator(scene, SR, 1) as sim, pytest.raises(spatial.SpatialUnavailable):
        spatial.render_scene(sim, scene, tone, ["MARA", "GHOST"], 0.0)


@needs_native
def test_to_sn3d_rescales_without_changing_channel_count():
    scene = SceneStage(actors=[StageActor(speaker="A", position_m=(1, 1, 1))])
    with spatial.RoomSimulator(scene, SR, 3) as sim:
        n3d = np.zeros((spatial.sh_channels(3), 4800), dtype=np.float32)
        n3d[0] = 0.5  # omni only
        sn3d = sim.to_sn3d(n3d)
    assert sn3d.shape == n3d.shape
    # N3D omni equals SN3D omni (both are the l=0 term), so channel 0 is stable.
    assert sn3d[0, 100] == pytest.approx(n3d[0, 100], rel=1e-3)


def test_max_tail_shrinks_with_sample_rate():
    assert spatial.max_tail_s(24000) == pytest.approx(4 * 8192 / 24000)
    assert spatial.max_tail_s(48000) < spatial.max_tail_s(24000)


def test_missing_library_reports_unavailable(monkeypatch):
    monkeypatch.setattr(spatial, "library_path", lambda: None)
    monkeypatch.setattr(spatial, "_LOADED", None)
    monkeypatch.setattr(spatial, "_LOAD_FAILED", False)
    assert spatial.available() is False
    with pytest.raises(spatial.SpatialUnavailable):
        spatial.RoomSimulator(SceneStage(), 48000, 1)


# -------------------------------------------------------------------- mixer


def make_project(tmp_path, characters=("MARA",), scenes=1, spatial_stage: StageProject | None = None):
    script = Script(
        title="Stage Test",
        language="en",
        characters={name: Character(id=name, name=name.title()) for name in characters},
        scenes=[],
    )
    directory = tmp_path / "stage-test"
    (directory / "lines").mkdir(parents=True)
    tone = (0.4 * np.sin(2 * np.pi * 220 * np.arange(24000) / 24000)).astype(np.float32)
    for scene_id in range(1, scenes + 1):
        lines = []
        for index, name in enumerate(characters):
            line_id = f"s{scene_id}-{index + 1:02d}"
            lines.append(Line(id=line_id, scene=scene_id, speaker=name, text="Hello."))
            sf.write(directory / "lines" / f"{line_id}.wav", tone, 24000)
        script.scenes.append(Scene(id=scene_id, heading=f"SCENE {scene_id}", lines=lines))

    project = Project(
        id="stage-test",
        title="Stage Test",
        language="en",
        raw_script="",
        script=script,
        settings=ProjectSettings(pause_ms=100, scene_gap_ms=1200),
        stage=spatial_stage,
    )
    return project, directory


@needs_native
def test_staged_project_mixes_spatially_in_stereo(tmp_path):
    from drama_studio.mixer import build_mix

    stage = stage_with(StageActor(speaker="MARA", position_m=(2.0, 2.0, 1.5)))
    project, directory = make_project(tmp_path, spatial_stage=stage)
    result = build_mix(project, directory, lambda _: None)
    assert result.spatial is True
    assert result.warnings == []
    data, rate = sf.read(str(directory / result.file), dtype="float32")
    assert rate == 48000
    assert data.shape[1] == 2
    assert float(np.abs(data).max()) > 0.05


@needs_native
def test_unstaged_scene_in_a_staged_project_still_audible(tmp_path):
    """Only scene 1 is staged; scene 2 must survive as a centre source."""
    from drama_studio.mixer import build_mix

    stage = stage_with(StageActor(speaker="MARA", position_m=(2.0, 2.0, 1.5)))
    project, directory = make_project(tmp_path, scenes=2, spatial_stage=stage)
    result = build_mix(project, directory, lambda _: None)
    assert result.spatial is True

    data, _ = sf.read(str(directory / result.file), dtype="float32")
    # Scene 2 starts after scene 1's clip plus the scene gap, so its window is
    # well past the end of scene 1's track.
    start = int(2.3 * 48000)
    stop = min(data.shape[0], int(3.1 * 48000))
    assert float(np.abs(data[start:stop]).max()) > 0.05


@needs_native
def test_ambisonic_master_is_exported_on_demand(tmp_path):
    stage = stage_with(StageActor(speaker="MARA", position_m=(2.0, 2.0, 1.5)), export_ambisonic=True)
    project, directory = make_project(tmp_path, spatial_stage=stage)
    from drama_studio.mixer import build_mix

    result = build_mix(project, directory, lambda _: None)
    assert result.ambisonic == "mix/master_ambisonic.wav"
    path = directory / result.ambisonic
    info = sf.info(str(path))
    assert info.channels == spatial.sh_channels(stage.order)
    assert info.samplerate == 48000
    # The file is only written, never in the stereo path; it should hold energy.
    data, _ = sf.read(str(path), dtype="float32")
    assert float(np.abs(data).max()) > 0.01


@needs_native
def test_ambisonic_master_is_omitted_by_default(tmp_path):
    stage = stage_with(StageActor(speaker="MARA", position_m=(2.0, 2.0, 1.5)))
    project, directory = make_project(tmp_path, spatial_stage=stage)
    from drama_studio.mixer import build_mix

    result = build_mix(project, directory, lambda _: None)
    assert result.ambisonic is None
    assert not (directory / "mix" / "master_ambisonic.wav").exists()


def test_stage_enabled_without_library_mixes_dry_with_a_warning(tmp_path, monkeypatch):
    from drama_studio import mixer

    monkeypatch.setattr(spatial, "library_path", lambda: None)
    monkeypatch.setattr(spatial, "_LOADED", None)
    monkeypatch.setattr(spatial, "_LOAD_FAILED", False)

    stage = stage_with(StageActor(speaker="MARA", position_m=(2.0, 2.0, 1.5)))
    project, directory = make_project(tmp_path, spatial_stage=stage)
    result = mixer.build_mix(project, directory, lambda _: None)
    assert result.spatial is False
    assert any("renderer library is missing" in warning for warning in result.warnings)
    data, rate = sf.read(str(directory / result.file), dtype="float32")
    assert rate == 48000
    assert float(np.abs(data).max()) > 0.05  # still audible


def test_enabled_stage_with_no_staged_scenes_is_not_enabled():
    assert spatial.is_enabled(StageProject(enabled=True, scenes={})) is False


# --------------------------------------------------------- cue-driven layout


def _lines(*pairs: tuple[str, str]) -> list[Line]:
    return [
        Line(id=f"l{index}", scene=1, speaker=speaker, text="Line.", direction=direction)
        for index, (speaker, direction) in enumerate(pairs)
    ]


def test_layout_ignores_stage_directions_when_none_are_given():
    """The old geometric arc is still what a scriptless caller gets."""
    stage = spatial.layout_scene(StageProject(), 1, ["A", "B"])
    assert stage.paths == []
    # Thirds across a 6 m room, alternating depth: the arc from before cues.
    assert [actor.position_m for actor in stage.actors] == [(2.0, 1.5, 1.5), (4.0, 2.0, 1.5)]


def test_layout_makes_no_paths_without_a_timeline():
    """No alignment yet means no idea when anyone speaks, so nothing moves."""
    lines = _lines(("MERIAN", "through the hatch"))
    stage = spatial.layout_scene(StageProject(), 1, ["MERIAN"], lines=lines, timing={})
    assert stage.paths == []
    assert stage.actors[0].position_m == (3.0, 1.5, 1.5)


def test_entering_speaker_starts_at_the_door_and_rests_inside():
    room = StageRoom(dims_m=(6.0, 5.0, 3.0))
    stage = spatial.layout_scene(
        StageProject(default_room=room),
        1,
        ["MERIAN"],
        lines=_lines(("MERIAN", "through the hatch, muffled")),
        timing={"MERIAN": (6.0, 11.5)},
    )
    path = stage.paths[0]
    # Alone in a 6 m room the slot is dead centre, so the threshold is too.
    assert (path.keys[0][1], path.keys[0][2]) == pytest.approx((3.0, 0.2), abs=0.01)
    assert path.keys[0][0] == 6.0 and path.keys[1][0] == 11.5
    # The dot on the board is where they end up, not where they came from.
    assert stage.actors[0].position_m == pytest.approx((3.0, 1.5, 1.5), abs=0.01)


def test_approaching_speaker_ends_where_they_are_placed():
    stage = spatial.layout_scene(
        StageProject(),
        1,
        ["MERIAN", "VOGEL"],
        lines=_lines(("MERIAN", "closer now, tender")),
        timing={"MERIAN": (2.0, 9.0)},
    )
    path = stage.paths[0]
    start, end = path.keys[0], path.keys[1]
    assert (end[1], end[2]) == pytest.approx(stage.actors[0].position_m[:2], abs=0.02)
    # Walks in from farther back: the first key is deeper into the room.
    assert (start[1] - 3.0) ** 2 + (start[2] - 4.1) ** 2 > (end[1] - 3.0) ** 2 + (end[2] - 4.1) ** 2


def test_leaving_speaker_faces_the_exit_not_the_listener():
    stage = spatial.layout_scene(
        StageProject(),
        1,
        ["SANDT", "VOGEL"],
        lines=_lines(("SANDT", "as he leaves")),
        timing={"SANDT": (1.0, 4.0)},
    )
    actor = next(actor for actor in stage.actors if actor.speaker == "SANDT")
    path = next(path for path in stage.paths if path.speaker == "SANDT")
    travel_x = path.keys[1][1] - path.keys[0][1]
    travel_y = path.keys[1][2] - path.keys[0][2]
    expected = float(np.degrees(np.arctan2(travel_x, -travel_y)))
    assert actor.facing_deg == pytest.approx(expected, abs=0.2)


def test_pacing_speaker_traverses_the_room_width():
    room = StageRoom(dims_m=(6.0, 5.0, 3.0))
    stage = spatial.layout_scene(
        StageProject(default_room=room),
        1,
        ["SANDT"],
        lines=_lines(("SANDT", "pacing")),
        timing={"SANDT": (0.0, 6.0)},
    )
    first, last = stage.paths[0].keys
    assert first[1] < last[1] and first[2] == pytest.approx(last[2])


def test_motion_cue_keywords_are_matched_case_insensitively():
    cues = spatial.speaker_cues(_lines(("A", "APPROACHING the console")), ["A"])
    assert cues == {"A": "approach"}


def test_a_plain_direction_carries_no_motion():
    assert spatial.speaker_cues(_lines(("A", "whispering")) , ["A"]) == {}


def test_path_keys_stay_inside_a_small_room():
    room = StageRoom(dims_m=(2.0, 2.0, 2.2))
    stage = spatial.layout_scene(
        StageProject(default_room=room),
        1,
        ["A", "B"],
        lines=_lines(("A", "pacing"), ("B", "through the doorway")),
        timing={"A": (0.0, 3.0), "B": (0.0, 3.0)},
    )
    for path in stage.paths:
        for _t, x, y, z in path.keys:
            assert 0.0 <= x <= room.dims_m[0]
            assert 0.0 <= y <= room.dims_m[1]
            assert 0.0 <= z <= room.dims_m[2]
