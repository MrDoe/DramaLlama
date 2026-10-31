from __future__ import annotations

import json
import numpy as np

from drama_studio.audio import read_mono, write_mono
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
    dup = client.patch(
        url,
        json={
            "cuts": [
                {"line_id": line_a, "take_id": "take-a", "start_s": 0.0, "end_s": 0.2},
                {"line_id": line_a, "take_id": "take-a", "start_s": 0.2, "end_s": 0.4},
            ]
        },
    )
    assert dup.status_code == 400


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
