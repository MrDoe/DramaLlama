from __future__ import annotations

import json
import numpy as np

from drama_studio.audio import apply_region_effects, read_mono, write_mono
from drama_studio.models import AlignedLine, AlignmentResult, Take

from test_api_projects import CUT_SCRIPT, make_client


def _cut_project(tmp_path):
    app, client = make_client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"title": "Cut Test", "language": "en", "script_text": CUT_SCRIPT},
    ).json()
    project = client.post(f"/api/projects/{project['id']}/stems").json()
    stored = app.state.store.get(project["id"])
    stem = stored.stems[0]
    directory = app.state.store.project_dir(project["id"])
    (directory / "audio" / "takes").mkdir(parents=True, exist_ok=True)
    write_mono(
        directory / "audio" / "takes" / "a.wav",
        np.full(24000, 0.25, dtype=np.float32),
        24000,
    )
    write_mono(
        directory / "audio" / "takes" / "b.wav",
        np.full(48000, -0.25, dtype=np.float32),
        24000,
    )
    stem.takes = [
        Take(
            id="take-a",
            file="audio/takes/a.wav",
            duration_s=1.0,
            sample_rate=24000,
            seed=1,
            selected=True,
        ),
        Take(
            id="take-b",
            file="audio/takes/b.wav",
            duration_s=2.0,
            sample_rate=24000,
            seed=2,
        ),
    ]
    app.state.store.save(stored)
    return app, client, project, stem, directory


def test_batch_alignment_rebuilds_mix_once(tmp_path):
    app, client, project, stem, directory = _cut_project(tmp_path)
    line_a, line_b = stem.line_ids
    body = {
        "stems": {
            stem.id: [
                {"line_id": line_a, "take_id": "take-a", "start_s": 0.0, "end_s": 0.5},
                {"line_id": line_b, "take_id": "take-b", "start_s": 0.0, "end_s": 1.0},
            ]
        },
        "rebuild_mix": True,
    }
    response = client.patch(
        f"/api/projects/{project['id']}/alignments", json=body
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert stem.id in payload["alignments"]
    updated = payload["project"]
    assert updated["mix"] is not None
    assert updated["mix_stale"] is False


def test_single_save_sets_mix_stale_when_no_rebuild(tmp_path):
    _, client, project, stem, _ = _cut_project(tmp_path)
    line_a = stem.line_ids[0]
    response = client.patch(
        f"/api/projects/{project['id']}/stems/{stem.id}/alignment",
        json={
            "cuts": [
                {"line_id": line_a, "take_id": "take-a", "start_s": 0.0, "end_s": 0.5}
            ],
            "rebuild_mix": False,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["project"]["mix_stale"] is True


def test_alignment_rejects_unknown_line_and_duplicates(tmp_path):
    _, client, project, stem, _ = _cut_project(tmp_path)
    line_a = stem.line_ids[0]
    url = f"/api/projects/{project['id']}/stems/{stem.id}/alignment"
    bad_owner = client.patch(
        url,
        json={
            "cuts": [
                {"line_id": "nope", "take_id": "take-a", "start_s": 0.0, "end_s": 0.5}
            ]
        },
    )
    assert bad_owner.status_code == 400
    bad_range = client.patch(
        url,
        json={
            "cuts": [
                {"line_id": line_a, "take_id": "take-a", "start_s": 0.0, "end_s": 99.0}
            ]
        },
    )
    assert bad_range.status_code == 400


REGION_FX = {
    "gain_db": -6.0,
    "fade_in_s": 0.1,
    "fade_out_s": 0.2,
    "speed": 1.5,
    "pitch_cents": -300,
    "reverb_mix": 0.4,
    "dereverb": True,
}


def test_alignment_persists_region_effects(tmp_path):
    app, client, project, stem, _ = _cut_project(tmp_path)
    line_a = stem.line_ids[0]
    response = client.patch(
        f"/api/projects/{project['id']}/stems/{stem.id}/alignment",
        json={
            "cuts": [
                {
                    "line_id": line_a,
                    "take_id": "take-a",
                    "start_s": 0.0,
                    "end_s": 0.5,
                    **REGION_FX,
                }
            ],
            "rebuild_mix": False,
        },
    )
    assert response.status_code == 200, response.text
    line = response.json()["alignment"]["lines"][0]
    for key, value in REGION_FX.items():
        assert line[key] == value, key
    stored = app.state.store.get(project["id"]).alignments[stem.id].lines[0]
    for key, value in REGION_FX.items():
        assert getattr(stored, key) == value, key


def test_batch_alignment_persists_region_effects(tmp_path):
    app, client, project, stem, _ = _cut_project(tmp_path)
    line_a, line_b = stem.line_ids
    response = client.patch(
        f"/api/projects/{project['id']}/alignments",
        json={
            "stems": {
                stem.id: [
                    {
                        "line_id": line_a,
                        "take_id": "take-a",
                        "start_s": 0.0,
                        "end_s": 0.5,
                        **REGION_FX,
                    },
                    {"line_id": line_b, "take_id": "take-b", "start_s": 0.0, "end_s": 1.0},
                ]
            },
            "rebuild_mix": False,
        },
    )
    assert response.status_code == 200, response.text
    lines = response.json()["alignments"][stem.id]["lines"]
    assert lines[0]["speed"] == 1.5
    assert lines[0]["dereverb"] is True
    stored = app.state.store.get(project["id"]).alignments[stem.id].lines
    assert stored[0].gain_db == -6.0
    assert stored[0].pitch_cents == -300


def test_alignment_omitted_position_follows_script_order(tmp_path):
    """A save without position_s must not stack every clip at 0."""
    _, client, project, stem, _ = _cut_project(tmp_path)
    line_a, line_b = stem.line_ids
    response = client.patch(
        f"/api/projects/{project['id']}/stems/{stem.id}/alignment",
        json={
            "cuts": [
                {"line_id": line_a, "take_id": "take-a", "start_s": 0.0, "end_s": 0.5},
                {"line_id": line_b, "take_id": "take-b", "start_s": 0.0, "end_s": 1.0},
            ],
            "rebuild_mix": False,
        },
    )
    assert response.status_code == 200, response.text
    lines = {line["line_id"]: line for line in response.json()["alignment"]["lines"]}
    assert lines[line_a]["position_s"] == 0.0
    # 0.5 s cut + default 500 ms pause.
    assert lines[line_b]["position_s"] == 1.0


def test_batch_alignment_allows_split_segments_for_one_line(tmp_path):
    """Cutting a marked section out of a clip saves as two adjacent segments.

    The batch endpoint (Save-all) must accept several cuts for the same line
    — the single-stem endpoint already does — and the mix must stagger them
    left-to-right instead of overlapping.
    """
    _, client, project, stem, _ = _cut_project(tmp_path)
    line_a = stem.line_ids[0]
    response = client.patch(
        f"/api/projects/{project['id']}/alignments",
        json={
            "stems": {
                stem.id: [
                    {
                        "id": line_a,
                        "line_id": line_a,
                        "take_id": "take-a",
                        "start_s": 0.0,
                        "end_s": 0.3,
                        "position_s": 0.0,
                    },
                    {
                        "id": f"{line_a}__tail",
                        "line_id": line_a,
                        "take_id": "take-a",
                        "start_s": 0.7,
                        "end_s": 1.0,
                        "position_s": 0.3,
                    },
                ]
            },
            "rebuild_mix": True,
        },
    )
    assert response.status_code == 200, response.text
    lines = response.json()["alignments"][stem.id]["lines"]
    assert [line["line_id"] for line in lines] == [line_a, line_a]
    assert lines[0]["position_s"] == 0.0
    assert lines[1]["position_s"] == 0.3
    assert response.json()["project"]["mix_stale"] is False


def test_alignment_persists_enabled_flag(tmp_path):
    """Suno-style disable survives a single-stem save; mix goes stale."""
    app, client, project, stem, _ = _cut_project(tmp_path)
    line_a = stem.line_ids[0]
    response = client.patch(
        f"/api/projects/{project['id']}/stems/{stem.id}/alignment",
        json={
            "cuts": [
                {
                    "line_id": line_a,
                    "take_id": "take-a",
                    "start_s": 0.0,
                    "end_s": 0.5,
                    "enabled": False,
                }
            ],
            "rebuild_mix": False,
        },
    )
    assert response.status_code == 200, response.text
    line = response.json()["alignment"]["lines"][0]
    assert line["enabled"] is False
    stored = app.state.store.get(project["id"]).alignments[stem.id].lines[0]
    assert stored.enabled is False


def test_batch_alignment_persists_enabled_flag(tmp_path):
    _, client, project, stem, _ = _cut_project(tmp_path)
    line_a = stem.line_ids[0]
    response = client.patch(
        f"/api/projects/{project['id']}/alignments",
        json={
            "stems": {
                stem.id: [
                    {
                        "line_id": line_a,
                        "take_id": "take-a",
                        "start_s": 0.0,
                        "end_s": 0.5,
                        "enabled": False,
                    }
                ]
            },
            "rebuild_mix": False,
        },
    )
    assert response.status_code == 200, response.text
    lines = response.json()["alignments"][stem.id]["lines"]
    assert lines[0]["enabled"] is False


def test_batch_alignment_rejects_duplicate_segment_ids(tmp_path):
    _, client, project, stem, _ = _cut_project(tmp_path)
    line_a = stem.line_ids[0]
    response = client.patch(
        f"/api/projects/{project['id']}/alignments",
        json={
            "stems": {
                stem.id: [
                    {"id": "seg", "line_id": line_a, "take_id": "take-a",
                     "start_s": 0.0, "end_s": 0.3},
                    {"id": "seg", "line_id": line_a, "take_id": "take-a",
                     "start_s": 0.3, "end_s": 0.6},
                ]
            },
            "rebuild_mix": False,
        },
    )
    assert response.status_code == 400


def test_region_effects_gain_fades_and_speed():
    sr = 24000
    data = np.ones(sr, dtype=np.float32)
    louder = apply_region_effects(data, sr, gain_db=6.0)
    assert np.isclose(float(np.mean(louder)), 1.995, rtol=0.01)

    faded = apply_region_effects(data, sr, fade_in_s=0.1, fade_out_s=0.1)
    assert faded[0] == 0.0
    assert faded[-1] == 0.0
    # 0.1 s at 24 kHz = 2400 samples per ramp; stay clear of both.
    assert np.isclose(float(np.mean(faded[3000:-3000])), 1.0)

    faster = apply_region_effects(data, sr, speed=2.0)
    assert len(faster) == sr // 2

    slower = apply_region_effects(data, sr, speed=0.5)
    assert len(slower) == sr * 2


def test_region_effects_pitch_shifts_frequency_and_preserves_duration():
    import pytest

    pytest.importorskip("librosa")
    sr = 24000
    data = np.sin(np.linspace(0, 2 * np.pi * 220, sr)).astype(np.float32)
    shifted = apply_region_effects(data, sr, pitch_cents=700)
    assert abs(len(shifted) - len(data)) <= 1
    assert np.all(np.isfinite(shifted))
    spectrum = np.abs(np.fft.rfft(shifted * np.hanning(len(shifted))))
    peak = float(np.fft.rfftfreq(len(shifted), 1.0 / sr)[np.argmax(spectrum)])
    # +700 cents = a perfect fifth: 220 Hz -> 329.63 Hz.
    assert abs(peak - 220.0 * 2 ** (7 / 12)) < 5.0


def test_region_effects_reverb_adds_tail():
    sr = 24000
    impulse = np.zeros(sr, dtype=np.float32)
    impulse[0] = 1.0
    wet = apply_region_effects(impulse, sr, reverb_mix=1.0)
    assert len(wet) == sr
    assert float(np.sum(np.abs(wet[100:2000]))) > 0.01


def test_region_effects_dereverb_runs():
    import pytest

    pytest.importorskip("scipy")
    sr = 24000
    rng = np.random.default_rng(3)
    data = (0.1 * rng.standard_normal(sr)).astype(np.float32)
    dry = apply_region_effects(data, sr, dereverb=True)
    assert len(dry) == len(data)
    assert np.all(np.isfinite(dry))


def test_render_rejects_per_stem_engine(tmp_path):
    _, client = make_client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"title": "T", "language": "en", "script_text": CUT_SCRIPT},
    ).json()
    built = client.post(f"/api/projects/{project['id']}/stems").json()
    stem = built["stems"][0]
    response = client.post(
        f"/api/projects/{project['id']}/stems/{stem['id']}/render",
        json={"engine": "fake"},
    )
    assert response.status_code == 400


def test_legacy_project_with_stem_engine_still_loads(tmp_path):
    app, client = make_client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"title": "Legacy", "language": "en", "script_text": CUT_SCRIPT},
    ).json()
    project = client.post(f"/api/projects/{project['id']}/stems").json()
    path = app.state.store.project_dir(project["id"]) / "project.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    # Simulate a pre-project-level project.json.
    raw["stems"][0]["engine"] = "breeze"
    raw["script"]["characters"][next(iter(raw["script"]["characters"]))]["engine"] = (
        "breeze"
    )
    path.write_text(json.dumps(raw), encoding="utf-8")
    loaded = app.state.store.get(project["id"])
    assert loaded.stems[0].takes == []
    assert loaded.default_engine is None
