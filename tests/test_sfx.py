from __future__ import annotations

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from drama_studio.api import create_app
from drama_studio.llm import LLMError
from drama_studio.sfx import SfxLibrary


class FailingLLM:
    def chat_json(self, *args, **kwargs):
        raise LLMError("offline")

    def chat_text(self, *args, **kwargs):
        raise LLMError("offline")


SCRIPT = """# TEST

## SCENE 1

MARA: You're late.
ELIAS: Traffic.
"""


def make_client(tmp_path):
    sfx_root = tmp_path / "sfx"
    sfx_root.mkdir()
    sf.write(sfx_root / "rain-on-window.wav", np.zeros(24000, dtype=np.float32), 24000)
    sf.write(sfx_root / "door-slam.wav", np.zeros(12000, dtype=np.float32), 24000)
    library = SfxLibrary(
        roots=[sfx_root],
        cache_path=tmp_path / "sfx_index.json",
        excludes=set(),
        upload_root=sfx_root,
    )
    app = create_app(
        tmp_path / "jobs.sqlite3",
        tmp_path / "projects",
        llm_client=FailingLLM(),
        sfx_library=library,
    )
    return app, TestClient(app), sfx_root


def test_search_and_audio(tmp_path):
    _, client, _ = make_client(tmp_path)
    response = client.get("/api/sfx", params={"q": "rain"})
    assert response.status_code == 200
    items = response.json()
    assert items
    assert "rain" in items[0]["name"]
    audio = client.get(f"/api/sfx/{items[0]['id']}/audio")
    assert audio.status_code == 200
    assert audio.headers["content-type"].startswith("audio/")


def test_upload_indexes_file(tmp_path):
    _, client, _ = make_client(tmp_path)
    path = tmp_path / "thunder.wav"
    sf.write(path, np.zeros(12000, dtype=np.float32), 24000)
    with path.open("rb") as handle:
        response = client.post(
            "/api/sfx/upload",
            files={"file": ("thunder.wav", handle, "audio/wav")},
        )
    assert response.status_code == 201
    assert response.json()["name"] == "thunder"
    found = client.get("/api/sfx", params={"q": "thunder"}).json()
    assert found


def test_cue_create_and_delete(tmp_path):
    _, client, _ = make_client(tmp_path)
    project = client.post(
        "/api/projects", json={"title": "Test", "language": "en", "script_text": SCRIPT}
    ).json()
    sfx_item = client.get("/api/sfx").json()[0]
    line_id = project["script"]["scenes"][0]["lines"][0]["id"]
    created = client.post(
        f"/api/projects/{project['id']}/cues",
        json={
            "kind": "bed",
            "sfx_id": sfx_item["id"],
            "name": sfx_item["name"],
            "anchor_line_id": line_id,
            "gain_db": -18,
            "loop": True,
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["cues"][0]["name"] == sfx_item["name"]
    cue_id = body["cues"][0]["id"]
    deleted = client.delete(f"/api/projects/{project['id']}/cues/{cue_id}")
    assert deleted.status_code == 204
    assert client.get(f"/api/projects/{project['id']}").json()["cues"] == []


def test_cue_patch_moves_clip_and_marks_mix_stale(tmp_path):
    _, client, _ = make_client(tmp_path)
    project = client.post(
        "/api/projects", json={"title": "Test", "language": "en", "script_text": SCRIPT}
    ).json()
    lines = project["script"]["scenes"][0]["lines"]
    sfx_item = client.get("/api/sfx").json()[0]
    created = client.post(
        f"/api/projects/{project['id']}/cues",
        json={
            "kind": "one_shot",
            "sfx_id": sfx_item["id"],
            "name": sfx_item["name"],
            "anchor_line_id": lines[0]["id"],
        },
    ).json()
    cue_id = created["cues"][0]["id"]

    patched = client.patch(
        f"/api/projects/{project['id']}/cues/{cue_id}",
        json={
            "kind": "music",
            "anchor_line_id": lines[1]["id"],
            "offset_s": -1.25,
            "gain_db": -22,
            "loop": True,
        },
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    cue = body["cues"][0]
    assert cue["kind"] == "music"
    assert cue["anchor_line_id"] == lines[1]["id"]
    assert cue["offset_s"] == -1.25
    assert cue["gain_db"] == -22
    assert cue["loop"] is True
    assert cue["name"] == sfx_item["name"]
    assert body["mix_stale"] is True

    bad_anchor = client.patch(
        f"/api/projects/{project['id']}/cues/{cue_id}",
        json={"anchor_line_id": "nope"},
    )
    assert bad_anchor.status_code == 400

    missing = client.patch(
        f"/api/projects/{project['id']}/cues/no-such-cue", json={"gain_db": -6}
    )
    assert missing.status_code == 404


def test_cue_patch_keeps_missing_sfx_id(tmp_path):
    """A cue edit must survive when its SFX is no longer indexed.

    Regression: the Cut board saves full cue bodies. Re-validating an
    unchanged ``sfx_id`` against the live library 404d every save on
    machines without that file (e.g. demo SFX missing from ~/Sounds).
    Only a *changed* sfx_id is validated.
    """
    _, client, sfx_root = make_client(tmp_path)
    project = client.post(
        "/api/projects", json={"title": "Test", "language": "en", "script_text": SCRIPT}
    ).json()
    lines = project["script"]["scenes"][0]["lines"]
    sfx_item = client.get("/api/sfx").json()[0]
    cue_id = client.post(
        f"/api/projects/{project['id']}/cues",
        json={
            "kind": "bed",
            "sfx_id": sfx_item["id"],
            "name": sfx_item["name"],
            "anchor_line_id": lines[0]["id"],
        },
    ).json()["cues"][0]["id"]

    # Drop the file from the library; the stored cue still references it.
    from pathlib import Path as _Path

    _Path(sfx_item["path"]).unlink()
    client.post("/api/sfx/scan")
    assert client.get(f"/api/sfx/{sfx_item['id']}/audio").status_code == 404

    # Full-body save with the unchanged sfx_id must succeed.
    full = client.get(f"/api/projects/{project['id']}").json()["cues"][0]
    kept = client.patch(f"/api/projects/{project['id']}/cues/{cue_id}", json=full)
    assert kept.status_code == 200, kept.text
    assert kept.json()["cues"][0]["offset_s"] == full["offset_s"]

    # Changing to a genuinely unknown sfx still 404s.
    assert (
        client.patch(
            f"/api/projects/{project['id']}/cues/{cue_id}",
            json={"sfx_id": "does-not-exist"},
        ).status_code
        == 404
    )



def _two_views(tmp_path):
    import time as _time

    root = tmp_path / "sfx"
    root.mkdir()
    sf.write(root / "bed-a.wav", np.zeros(12000, dtype=np.float32), 24000)
    api_view = SfxLibrary(
        roots=[root],
        cache_path=tmp_path / "idx.json",
        excludes=set(),
        upload_root=root,
    )
    api_view.scan()
    worker_view = SfxLibrary(
        roots=[root],
        cache_path=tmp_path / "idx.json",
        excludes=set(),
        upload_root=root,
    )
    return root, api_view, worker_view


def test_get_finds_a_file_the_worker_indexed_after_us(tmp_path):
    """Two processes, one folder: a Suno download must not look like a 404.

    The worker indexes what it downloads and keeps its own copy of the index.
    The API's copy predates the file, so answering straight from it tells the
    browser that the track it just created does not exist.
    """
    root, api_view, worker_view = _two_views(tmp_path)
    sf.write(root / "suno-theme.wav", np.zeros(24000, dtype=np.float32), 24000)
    created = worker_view.find_by_path(root / "suno-theme.wav")
    assert created is not None, "the worker's own scan should see the file"
    assert api_view.get(created.id) is not None
    assert api_view.get(created.id).name == "suno-theme"


def test_the_self_heal_does_not_rescan_on_every_miss(tmp_path):
    """A deleted cue must not turn into a folder walk, once per cue."""
    import time

    root, api_view, worker_view = _two_views(tmp_path)
    sf.write(root / "suno-two.wav", np.zeros(12000, dtype=np.float32), 24000)
    created = worker_view.find_by_path(root / "suno-two.wav")
    api_view._healed_at = time.monotonic()  # healed a moment ago
    assert api_view.get(created.id) is None


def test_music_cue_can_live_on_the_timeline_without_a_line(tmp_path):
    """A score is not an event at a word.

    Music used to require `anchor_line_id`, which also decided the scene it was
    clipped to — so it could not score more than one scene, and dragging it out
    of that one dropped it from the mix.
    """
    _, client, _ = make_client(tmp_path)
    project = client.post(
        "/api/projects", json={"title": "Test", "language": "en", "script_text": SCRIPT}
    ).json()
    sfx_item = client.get("/api/sfx").json()[0]

    created = client.post(
        f"/api/projects/{project['id']}/cues",
        json={
            "kind": "music",
            "sfx_id": sfx_item["id"],
            "name": "Score",
            "anchor_line_id": None,
            "position_s": 12.5,
            "end_s": 40.0,
            "loop": True,
        },
    )
    assert created.status_code == 201, created.text
    cue = created.json()["cues"][0]
    assert cue["anchor_line_id"] is None
    assert cue["position_s"] == 12.5
    assert cue["end_s"] == 40.0


def test_cue_needs_either_an_anchor_or_a_position(tmp_path):
    """No anchor and no position is not "at zero", it is a mistake."""
    _, client, _ = make_client(tmp_path)
    project = client.post(
        "/api/projects", json={"title": "Test", "language": "en", "script_text": SCRIPT}
    ).json()
    sfx_item = client.get("/api/sfx").json()[0]
    refused = client.post(
        f"/api/projects/{project['id']}/cues",
        json={"kind": "music", "sfx_id": sfx_item["id"], "name": "Nowhere"},
    )
    assert refused.status_code == 400
    assert "position" in refused.json()["detail"]


def test_cue_can_be_detached_and_reanchored(tmp_path):
    _, client, _ = make_client(tmp_path)
    project = client.post(
        "/api/projects", json={"title": "Test", "language": "en", "script_text": SCRIPT}
    ).json()
    lines = project["script"]["scenes"][0]["lines"]
    sfx_item = client.get("/api/sfx").json()[0]
    cue_id = client.post(
        f"/api/projects/{project['id']}/cues",
        json={
            "kind": "one_shot",
            "sfx_id": sfx_item["id"],
            "name": "Door",
            "anchor_line_id": lines[1]["id"],
        },
    ).json()["cues"][0]["id"]

    detached = client.patch(
        f"/api/projects/{project['id']}/cues/{cue_id}",
        json={"anchor_line_id": None, "position_s": 3.25},
    ).json()["cues"][0]
    assert detached["anchor_line_id"] is None
    assert detached["position_s"] == 3.25

    reanchored = client.patch(
        f"/api/projects/{project['id']}/cues/{cue_id}",
        json={"anchor_line_id": lines[0]["id"], "position_s": None, "end_s": None},
    ).json()["cues"][0]
    assert reanchored["anchor_line_id"] == lines[0]["id"]
    assert reanchored["position_s"] is None


def test_cue_end_before_its_start_is_refused(tmp_path):
    _, client, _ = make_client(tmp_path)
    project = client.post(
        "/api/projects", json={"title": "Test", "language": "en", "script_text": SCRIPT}
    ).json()
    sfx_item = client.get("/api/sfx").json()[0]
    cue_id = client.post(
        f"/api/projects/{project['id']}/cues",
        json={
            "kind": "music",
            "sfx_id": sfx_item["id"],
            "name": "Score",
            "anchor_line_id": None,
            "position_s": 5.0,
        },
    ).json()["cues"][0]["id"]
    bad = client.patch(
        f"/api/projects/{project['id']}/cues/{cue_id}", json={"end_s": 4.0}
    )
    assert bad.status_code == 400
