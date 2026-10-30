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
