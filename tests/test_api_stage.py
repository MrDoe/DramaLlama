"""Stage REST surface: capabilities, load, save, layout and preview gating."""

from __future__ import annotations

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from drama_studio.api import create_app
from drama_studio.models import StageActor, StageProject

SCRIPT = """# STAGE API

## SCENE 1

MARA: You're late.
ELIAS: Traffic.

## SCENE 2

MARA: Still here?
"""


def make_client(tmp_path):
    app = create_app(tmp_path / "jobs.sqlite3", tmp_path / "projects")
    return app, TestClient(app), tmp_path


def make_project(client) -> str:
    created = client.post(
        "/api/projects",
        json={"title": "Stage API", "language": "en", "script_text": SCRIPT},
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def test_capabilities_reports_presets_and_limits(tmp_path):
    _, client, _ = make_client(tmp_path)
    body = client.get("/api/stage/capabilities").json()
    assert isinstance(body["available"], bool)
    assert body["max_sources"] == 16
    assert body["orders"] == [1, 3]
    keys = {preset["key"] for preset in body["presets"]}
    assert {"living_room", "car", "outdoor"} <= keys
    # The room tail is bounded by the renderer's buffers at both rates.
    assert body["max_tail_s"]["48000"] < body["max_tail_s"]["24000"]


def test_get_stage_returns_defaults_and_scene_cast(tmp_path):
    _, client, _ = make_client(tmp_path)
    project_id = make_project(client)
    body = client.get(f"/api/projects/{project_id}/stage").json()
    assert body["stage"]["enabled"] is False
    assert body["stage"]["version"] == 1
    assert body["scene_speakers"] == {"1": ["ELIAS", "MARA"], "2": ["MARA"]}


def test_put_stage_persists_and_marks_mix_stale(tmp_path):
    _, client, _ = make_client(tmp_path)
    project_id = make_project(client)
    stage = {
        "version": 1,
        "enabled": True,
        "order": 3,
        "export_ambisonic": False,
        "tail_s": 0.5,
        "default_room": {
            "dims_m": [5.0, 4.0, 2.6],
            "absorption": [0.4] * 6,
            "reflections": 2,
        },
        "scenes": {
            "1": {
                "room": {
                    "dims_m": [5.0, 4.0, 2.6],
                    "absorption": [0.4] * 6,
                    "reflections": 2,
                },
                "listener": {"position_m": [2.5, 3.2, 1.6], "facing_deg": 0.0},
                "actors": [
                    {
                        "speaker": "MARA",
                        "position_m": [1.5, 1.5, 1.5],
                        "facing_deg": 0.0,
                        "gain_db": -2.0,
                        "direct": 1.0,
                    }
                ],
                "paths": [],
            }
        },
    }
    saved = client.put(f"/api/projects/{project_id}/stage", json={"stage": stage})
    assert saved.status_code == 200, saved.text

    project = client.get(f"/api/projects/{project_id}").json()
    assert project["stage"]["enabled"] is True
    assert project["stage"]["scenes"]["1"]["actors"][0]["speaker"] == "MARA"
    # Staging invalidates the existing master.
    assert project["mix_stale"] is True


def test_put_stage_rejects_unknown_speaker(tmp_path):
    _, client, _ = make_client(tmp_path)
    project_id = make_project(client)
    stage = StageProject(
        enabled=True,
        scenes={
            "1": {
                "actors": [StageActor(speaker="GHOST", position_m=(1.0, 1.0, 1.5))],
            }
        },
    )
    response = client.put(
        f"/api/projects/{project_id}/stage",
        json={"stage": stage.model_dump(mode="json")},
    )
    assert response.status_code == 400
    assert "GHOST" in response.json()["detail"]


def test_put_stage_rejects_unknown_scene(tmp_path):
    _, client, _ = make_client(tmp_path)
    project_id = make_project(client)
    stage = StageProject(
        enabled=True,
        scenes={
            "9": {
                "actors": [StageActor(speaker="MARA", position_m=(1.0, 1.0, 1.5))],
            }
        },
    )
    response = client.put(
        f"/api/projects/{project_id}/stage",
        json={"stage": stage.model_dump(mode="json")},
    )
    assert response.status_code == 400
    assert "scene 9" in response.json()["detail"]


def test_put_stage_rejects_position_outside_the_room(tmp_path):
    """Model validation surfaces as 422 rather than writing an unusable stage."""
    _, client, _ = make_client(tmp_path)
    project_id = make_project(client)
    stage = {
        "version": 1,
        "enabled": True,
        "order": 1,
        "export_ambisonic": False,
        "tail_s": 0.5,
        "default_room": {
            "dims_m": [4.0, 4.0, 3.0],
            "absorption": [0.3] * 6,
            "reflections": 2,
        },
        "scenes": {
            "1": {
                "room": {
                    "dims_m": [4.0, 4.0, 3.0],
                    "absorption": [0.3] * 6,
                    "reflections": 2,
                },
                "listener": {"position_m": [2.0, 3.0, 1.6], "facing_deg": 0.0},
                "actors": [
                    {
                        "speaker": "MARA",
                        "position_m": [40.0, 1.0, 1.5],
                        "facing_deg": 0.0,
                        "gain_db": 0.0,
                        "direct": 1.0,
                    }
                ],
                "paths": [],
            }
        },
    }
    response = client.put(f"/api/projects/{project_id}/stage", json={"stage": stage})
    assert response.status_code == 422


def test_layout_spreads_the_cast_and_saves_round_trip(tmp_path):
    _, client, _ = make_client(tmp_path)
    project_id = make_project(client)
    layout = client.post(f"/api/projects/{project_id}/stage/layout", json={"scene": 1}).json()

    assert {actor["speaker"] for actor in layout["actors"]} == {"ELIAS", "MARA"}
    assert layout["actors"], "layout produced no actors"
    # Everyone stands in front of the listener, not at the origin.
    for actor in layout["actors"]:
        assert actor["position_m"][0] > 0
        assert actor["position_m"][1] > 0
        assert actor["position_m"][2] == 1.5
    assert layout["listener"]["position_m"][1] > layout["actors"][0]["position_m"][1]

    # The layout is returned, not stored: saving stays an explicit user action.
    assert client.get(f"/api/projects/{project_id}/stage").json()["stage"]["scenes"] == {}


def test_layout_of_unknown_scene_is_404(tmp_path):
    _, client, _ = make_client(tmp_path)
    project_id = make_project(client)
    assert client.post(f"/api/projects/{project_id}/stage/layout", json={"scene": 77}).status_code == 404


def test_preview_without_renderer_is_refused(tmp_path, monkeypatch):
    from drama_studio import spatial

    monkeypatch.setattr(spatial, "library_path", lambda: None)
    monkeypatch.setattr(spatial, "_LOADED", None)
    monkeypatch.setattr(spatial, "_LOAD_FAILED", False)

    _, client, _ = make_client(tmp_path)
    project_id = make_project(client)
    response = client.post(f"/api/projects/{project_id}/stage/preview", json={"scene": 1})
    assert response.status_code == 409
    assert "not installed" in response.json()["detail"]


def test_stage_endpoints_404_for_missing_project(tmp_path):
    _, client, _ = make_client(tmp_path)
    assert client.get("/api/projects/nope/stage").status_code == 404
    assert client.put("/api/projects/nope/stage", json={"stage": StageProject().model_dump(mode="json")}).status_code == 404
    assert client.post("/api/projects/nope/stage/layout", json={"scene": 1}).status_code == 404


def test_staged_mix_over_api_produces_stereo_master(tmp_path):
    """End-to-end: stage scene 1, queue the mix, get a stereo 48 kHz master."""
    from drama_studio import spatial

    if not spatial.available():
        import pytest

        pytest.skip("spatial renderer not built")

    app, client, root = make_client(tmp_path)
    project_id = make_project(client)

    # Give the project rendered line clips so the mix has material to place.
    tone = (0.4 * np.sin(2 * np.pi * 220 * np.arange(24000) / 24000)).astype(np.float32)
    directory = root / "projects" / project_id
    (directory / "lines").mkdir(parents=True, exist_ok=True)
    project = client.get(f"/api/projects/{project_id}").json()
    line_ids = [line["id"] for scene in project["script"]["scenes"] for line in scene["lines"]]
    assert len(line_ids) == 3
    for line_id in line_ids:
        sf.write(directory / "lines" / f"{line_id}.wav", tone, 24000)

    stage = {
        "version": 1,
        "enabled": True,
        "order": 1,
        "export_ambisonic": True,
        "tail_s": 0.4,
        "default_room": {
            "dims_m": [6.0, 5.0, 3.0],
            "absorption": [0.35] * 6,
            "reflections": 2,
        },
        "scenes": {
            "1": {
                "room": {
                    "dims_m": [6.0, 5.0, 3.0],
                    "absorption": [0.35] * 6,
                    "reflections": 2,
                },
                "listener": {"position_m": [3.0, 4.0, 1.6], "facing_deg": 0.0},
                "actors": [
                    {
                        "speaker": "MARA",
                        "position_m": [2.0, 2.0, 1.5],
                        "facing_deg": 0.0,
                        "gain_db": 0.0,
                        "direct": 1.0,
                    },
                ],
                "paths": [],
            }
        },
    }
    assert client.put(f"/api/projects/{project_id}/stage", json={"stage": stage}).status_code == 200

    # Mix through the library directly: the queue needs a worker process.
    from drama_studio.mixer import build_mix

    store = app.state.store
    loaded = store.find(project_id)
    assert loaded is not None
    result = build_mix(loaded, store.project_dir(project_id), lambda _sfx_id: None)
    assert result.spatial is True

    master, rate = sf.read(str(store.project_dir(project_id) / result.file), dtype="float32")
    assert rate == 48000
    assert master.shape[1] == 2
    assert float(np.abs(master).max()) > 0.05

    ambisonic = store.project_dir(project_id) / result.ambisonic
    info = sf.info(str(ambisonic))
    assert info.channels == spatial.sh_channels(1)
    # The Ambisonic master must line up with the stereo one, or it cannot be
    # muxed against the same picture.
    assert info.frames == sf.info(str(store.project_dir(project_id) / result.file)).frames


def test_ambisonic_master_never_outlives_the_stereo_one(tmp_path):
    """A deep room's tail must not push the Ambisonic file past the master.

    Found by driving the real UI: staging the last scene as a 2 x 9 m corridor
    at order 3 wrote a 16-channel master 1.98 s longer than the stereo file, so
    the two could not be muxed against the same picture. Padding covers a short
    file; only trimming keeps the promise.
    """
    import pytest

    from drama_studio import spatial

    if not spatial.available():
        pytest.skip("spatial renderer not built")

    app, client, root = make_client(tmp_path)
    project_id = make_project(client)
    tone = (0.4 * np.sin(2 * np.pi * 220 * np.arange(24000) / 24000)).astype(np.float32)
    directory = root / "projects" / project_id
    (directory / "lines").mkdir(parents=True, exist_ok=True)
    project = client.get(f"/api/projects/{project_id}").json()
    for scene in project["script"]["scenes"]:
        for line in scene["lines"]:
            sf.write(directory / "lines" / f"{line['id']}.wav", tone, 24000)

    corridor = {
        "dims_m": [2.0, 9.0, 2.4],
        "absorption": [0.2, 0.2, 0.25, 0.25, 0.3, 0.15],
        "reflections": 3,
    }

    def scene_stage(*speakers: tuple[str, float]) -> dict:
        """Stage the scene's real cast, spaced deep into the corridor."""
        return {
            "room": corridor,
            "listener": {"position_m": [1.0, 7.4, 1.6], "facing_deg": 0.0},
            "actors": [
                {
                    "speaker": speaker,
                    "position_m": [1.0, depth, 1.5],
                    "facing_deg": 0.0,
                    "gain_db": 0.0,
                    "direct": 1.0,
                }
                for speaker, depth in speakers
            ],
            "paths": [],
        }

    stage = {
        "version": 1,
        "enabled": True,
        "order": 3,
        "export_ambisonic": True,
        "tail_s": 0.6,
        "default_room": corridor,
        # The script's real cast: MARA and ELIAS in scene 1, MARA alone in
        # scene 2. Staging anyone else would silently leave audio out of the
        # Ambisonic master, which is the point of the centroid check below.
        "scenes": {
            "1": scene_stage(("MARA", 3.0), ("ELIAS", 4.5)),
            "2": scene_stage(("MARA", 2.0)),
        },
    }
    saved = client.put(f"/api/projects/{project_id}/stage", json={"stage": stage})
    assert saved.status_code == 200, saved.text

    from drama_studio.mixer import build_mix

    store = app.state.store
    loaded = store.find(project_id)
    result = build_mix(loaded, store.project_dir(project_id), lambda _sfx_id: None)
    assert result.spatial is True and result.ambisonic

    stereo, rate = sf.read(str(store.project_dir(project_id) / result.file), dtype="float32")
    amb, arate = sf.read(str(store.project_dir(project_id) / result.ambisonic), dtype="float32")
    assert (rate, arate) == (48000, 48000)
    assert amb.shape[1] == spatial.sh_channels(3)
    assert amb.shape[0] == stereo.shape[0], (
        f"Ambisonic {amb.shape[0]} vs stereo {stereo.shape[0]} frames"
    )

    # Same length is not enough. The old writer stitched the beds together end
    # to end, so a scene whose tail reached into the next one played late in the
    # Ambisonic file while sitting correctly in the stereo mix. The two are
    # related by a room-dependent decode and a loudness pass, so their levels
    # differ; *when* the energy happens must not.
    def envelope(samples: np.ndarray, hop: int = 480) -> np.ndarray:
        track = np.abs(samples).mean(axis=1) if samples.ndim > 1 else np.abs(samples)
        usable = track.size // hop * hop
        return track[:usable].reshape(-1, hop).mean(axis=1)

    left, right = envelope(stereo), envelope(amb[:, 0])
    # Mean-removed, or the long silent gaps dominate and any shift looks alike.
    correlation = np.correlate(
        left - left.mean(), right - right.mean(), mode="full"
    )
    lag = int(np.argmax(correlation)) - (left.size - 1)
    # One envelope step is 10 ms at 48 kHz / hop 480.
    assert abs(lag) * 0.01 < 0.10, f"Ambisonic master is off by {lag * 0.01:+.2f}s"
