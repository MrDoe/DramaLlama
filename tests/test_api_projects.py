from __future__ import annotations

from fastapi.testclient import TestClient

from drama_studio.api import create_app
from drama_studio.llm import LLMError
from drama_studio.models import Take


class FailingLLM:
    def chat_json(self, *args, **kwargs):
        raise LLMError("offline in tests")


def make_client(tmp_path):
    app = create_app(
        tmp_path / "jobs.sqlite3",
        tmp_path / "projects",
        llm_client=FailingLLM(),
    )
    return app, TestClient(app)


SCRIPT = """# API TEST

## SCENE 1

MARA: You're late.
ELIAS: Traffic.
"""


def test_project_crud(tmp_path):
    _, client = make_client(tmp_path)
    created = client.post(
        "/api/projects",
        json={"title": "API Test", "language": "en", "script_text": SCRIPT},
    )
    assert created.status_code == 201
    project = created.json()
    assert project["title"] == "API Test"
    assert len(project["script"]["scenes"][0]["lines"]) == 2

    listed = client.get("/api/projects")
    assert listed.status_code == 200
    assert listed.json()[0]["lines"] == 2

    fetched = client.get(f"/api/projects/{project['id']}")
    assert fetched.status_code == 200

    updated = client.put(
        f"/api/projects/{project['id']}",
        json={"raw_script": SCRIPT + "\nMARA: Pack."},
    )
    assert updated.status_code == 200
    assert len(updated.json()["script"]["scenes"][0]["lines"]) == 3

    parsed = client.post(f"/api/projects/{project['id']}/parse", json={"method": "deterministic"})
    assert parsed.status_code == 200

    deleted = client.delete(f"/api/projects/{project['id']}")
    assert deleted.status_code == 204
    assert client.get(f"/api/projects/{project['id']}").status_code == 404


def test_llm_parse_falls_back_when_offline(tmp_path):
    _, client = make_client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"title": "API Test", "language": "en", "script_text": SCRIPT},
    ).json()
    response = client.post(
        f"/api/projects/{project['id']}/parse", json={"method": "llm"}
    )
    assert response.status_code == 200
    assert response.json()["report"]["method"] == "deterministic"
    assert any(
        "LLM parse failed" in warning["message"]
        for warning in response.json()["report"]["warnings"]
    )


def test_reference_and_render_queue(tmp_path):
    app, client = make_client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"title": "API Test", "language": "en", "script_text": SCRIPT},
    ).json()
    project = client.post(f"/api/projects/{project['id']}/stems").json()
    stem = project["stems"][0]

    blocked = client.post(
        f"/api/projects/{project['id']}/stems/{stem['id']}/render",
        json={"candidates": 1},
    )
    assert blocked.status_code == 409

    stored = app.state.store.get(project["id"])
    character = stored.script.characters["MARA"]
    character.ref_file = "refs/mara.wav"
    character.ref_text = "You're late."
    character.ref_instruction = "Cold."
    app.state.store.save(stored)

    reference = client.post(
        f"/api/projects/{project['id']}/characters/MARA/reference",
        json={"cfg_scale": 4.0, "seed": 42},
    )
    assert reference.status_code == 202
    assert reference.json()["type"] == "reference.design"

    render = client.post(
        f"/api/projects/{project['id']}/stems/{stem['id']}/render",
        json={"candidates": 1},
    )
    assert render.status_code == 202
    assert render.json()["type"] == "stem.render"

    jobs = client.get(f"/api/projects/{project['id']}/jobs")
    assert jobs.status_code == 200
    assert {job["type"] for job in jobs.json()} == {"reference.design", "stem.render"}


def test_select_take_queues_realign(tmp_path):
    app, client = make_client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"title": "API Test", "language": "en", "script_text": SCRIPT},
    ).json()
    project = client.post(f"/api/projects/{project['id']}/stems").json()
    stored = app.state.store.get(project["id"])
    stem = stored.stems[0]
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
            duration_s=1.0,
            sample_rate=24000,
            seed=2,
        ),
    ]
    app.state.store.save(stored)

    response = client.patch(
        f"/api/projects/{project['id']}/stems/{stem.id}/take",
        json={"take_id": "take-b"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["job"]["type"] == "stem.realign"
    selected = [take for take in body["project"]["stems"][0]["takes"] if take["selected"]]
    assert selected[0]["id"] == "take-b"


def test_engine_selection_and_suno_queue(tmp_path):
    _, client = make_client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"title": "API Test", "language": "en", "script_text": SCRIPT},
    ).json()
    project = client.post(f"/api/projects/{project['id']}/stems").json()
    stem = project["stems"][0]

    changed = client.patch(
        f"/api/projects/{project['id']}/stems/{stem['id']}",
        json={"engine": "chatterbox"},
    )
    assert changed.status_code == 200
    assert changed.json()["stems"][0]["engine"] == "chatterbox"

    german = client.post(
        "/api/projects",
        json={"title": "German Test", "language": "de", "script_text": SCRIPT},
    ).json()
    german = client.post(f"/api/projects/{german['id']}/stems").json()
    invalid = client.patch(
        f"/api/projects/{german['id']}/stems/{german['stems'][0]['id']}",
        json={"engine": "breeze"},
    )
    assert invalid.status_code == 400

    suno = client.post(
        f"/api/projects/{project['id']}/suno/generate",
        json={"kind": "instrumental", "prompt": "slow rain jazz", "duration": 60},
    )
    assert suno.status_code == 202
    assert suno.json()["type"] == "suno.generate"


def test_mix_queue_and_project_files(tmp_path):
    app, client = make_client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"title": "API Test", "language": "en", "script_text": SCRIPT},
    ).json()
    mix = client.post(f"/api/projects/{project['id']}/mix")
    assert mix.status_code == 202
    assert mix.json()["type"] == "mix.render"
    jobs = client.get(f"/api/projects/{project['id']}/jobs").json()
    assert any(job["type"] == "mix.render" for job in jobs)

    directory = app.state.store.project_dir(project["id"])
    (directory / "note.txt").write_text("hello", encoding="utf-8")
    assert client.get(f"/api/projects/{project['id']}/files/note.txt").status_code == 200
    assert (
        client.get(f"/api/projects/{project['id']}/files/../project.json").status_code
        in {404, 400}
    )


def test_update_script_endpoint(tmp_path):
    _, client = make_client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"title": "API Test", "language": "en", "script_text": SCRIPT},
    ).json()
    script = project["script"]
    script["scenes"][0]["lines"].append(
        {
            "id": "s1-03",
            "scene": 1,
            "speaker": "MARA",
            "text": "Pack.",
            "direction": "",
            "sfx": [],
            "emotion": "",
            "intensity": 3,
        }
    )
    response = client.put(
        f"/api/projects/{project['id']}/script", json={"script": script}
    )
    assert response.status_code == 200
    assert len(response.json()["script"]["scenes"][0]["lines"]) == 3
