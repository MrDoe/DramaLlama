from __future__ import annotations

import numpy as np
from fastapi.testclient import TestClient

from drama_studio.api import create_app
from drama_studio.audio import read_mono, write_mono
from drama_studio.llm import LLMError
from drama_studio.models import AlignedLine, AlignmentResult, Take


class FailingLLM:
    def chat_json(self, *args, **kwargs):
        raise LLMError("offline in tests")

    def chat_text(self, *args, **kwargs):
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

CUT_SCRIPT = """# CUT TEST

## SCENE 1

MARA: You're late.
MARA: Pack.
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


def test_alignment_slices_each_cut_from_its_take(tmp_path):
    app, client, project, stem, directory = _cut_project(tmp_path)
    line_a, line_b = stem.line_ids
    url = f"/api/projects/{project['id']}/stems/{stem.id}/alignment"

    response = client.patch(
        url,
        json={
            "cuts": [
                {
                    "line_id": line_a,
                    "take_id": "take-b",
                    "start_s": 0.5,
                    "end_s": 1.0,
                },
                {
                    "line_id": line_b,
                    "take_id": "take-a",
                    "start_s": 0.0,
                    "end_s": 0.5,
                },
            ]
        },
    )
    assert response.status_code == 200, response.text
    lines = {line["line_id"]: line for line in response.json()["alignment"]["lines"]}
    assert lines[line_a]["take_id"] == "take-b"
    assert lines[line_b]["take_id"] == "take-a"

    # take-b is the negative constant, take-a the positive one.
    a_clip, _ = read_mono(directory / "lines" / f"{line_a}.wav")
    b_clip, _ = read_mono(directory / "lines" / f"{line_b}.wav")
    assert float(a_clip.mean()) < -0.2
    assert float(b_clip.mean()) > 0.2

    unknown = client.patch(
        url,
        json={
            "cuts": [
                {
                    "line_id": line_a,
                    "take_id": "nope",
                    "start_s": 0.0,
                    "end_s": 0.5,
                }
            ]
        },
    )
    assert unknown.status_code == 404

    too_long = client.patch(
        url,
        json={
            "cuts": [
                {
                    "line_id": line_a,
                    "take_id": "take-a",
                    "start_s": 0.0,
                    "end_s": 1.5,
                }
            ]
        },
    )
    assert too_long.status_code == 400
    assert "take-a" in too_long.json()["detail"]


def test_alignment_accepts_legacy_cut_without_take(tmp_path):
    app, client, project, stem, directory = _cut_project(tmp_path)
    line_a = stem.line_ids[0]
    response = client.patch(
        f"/api/projects/{project['id']}/stems/{stem.id}/alignment",
        json={"cuts": [{"line_id": line_a, "start_s": 0.0, "end_s": 0.5}]},
    )
    assert response.status_code == 200, response.text
    line = response.json()["alignment"]["lines"][0]
    assert line["take_id"] == "take-a"
    clip, _ = read_mono(directory / "lines" / f"{line_a}.wav")
    assert float(clip.mean()) > 0.2


def test_select_take_keeps_mixed_cut_sheet(tmp_path):
    app, client, project, stem, _ = _cut_project(tmp_path)
    stored = app.state.store.get(project["id"])
    line_a, line_b = stem.line_ids
    stored.alignments[stem.id] = AlignmentResult(
        stem_id=stem.id,
        lines=[
            AlignedLine(line_id=line_a, take_id="take-a", start_s=0.1, end_s=0.4),
            AlignedLine(line_id=line_b, take_id="take-b", start_s=0.2, end_s=0.9),
        ],
        aligner="manual",
    )
    app.state.store.save(stored)

    response = client.patch(
        f"/api/projects/{project['id']}/stems/{stem.id}/take",
        json={"take_id": "take-b"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["job"] is None
    lines = {
        line["line_id"]: line for line in body["project"]["alignments"][stem.id]["lines"]
    }
    assert lines[line_a]["take_id"] == "take-a"
    assert lines[line_a]["start_s"] == 0.1
    assert lines[line_b]["take_id"] == "take-b"


def test_engine_selection_and_suno_queue(tmp_path):
    _, client = make_client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"title": "API Test", "language": "en", "script_text": SCRIPT},
    ).json()
    project = client.post(f"/api/projects/{project['id']}/stems").json()
    stem = project["stems"][0]

    # Per-stem overrides were removed: engines are project-level only.
    rejected = client.patch(
        f"/api/projects/{project['id']}/stems/{stem['id']}",
        json={"engine": "chatterbox"},
    )
    assert rejected.status_code == 400
    assert "project" in rejected.json()["detail"].lower()

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


def test_suno_project_does_not_require_breeze_reference(tmp_path):
    _, client = make_client(tmp_path)
    project = client.post(
        "/api/projects",
        json={
            "title": "Suno API Test",
            "language": "en",
            "script_text": SCRIPT,
            "default_engine": "suno",
        },
    ).json()
    assert project["default_engine"] == "suno"
    built = client.post(f"/api/projects/{project['id']}/stems")
    assert built.status_code == 200
    stem = built.json()["stems"][0]
    render = client.post(
        f"/api/projects/{project['id']}/stems/{stem['id']}/render",
        json={"candidates": 1},
    )
    assert render.status_code == 202
    assert render.json()["payload"]["engine"] == "suno"


def test_project_engine_update_and_validation(tmp_path):
    _, client = make_client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"title": "Engine", "language": "de", "script_text": SCRIPT},
    ).json()
    updated = client.put(
        f"/api/projects/{project['id']}", json={"default_engine": "suno"}
    )
    assert updated.status_code == 200
    assert updated.json()["default_engine"] == "suno"
    cleared = client.put(
        f"/api/projects/{project['id']}", json={"default_engine": "auto"}
    )
    assert cleared.status_code == 200
    assert cleared.json()["default_engine"] is None
    rejected = client.put(
        f"/api/projects/{project['id']}", json={"default_engine": "breeze"}
    )
    assert rejected.status_code == 400


def test_changing_project_engine_updates_default_stems(tmp_path):
    _, client = make_client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"title": "Engine Stems", "language": "de", "script_text": SCRIPT},
    ).json()
    built = client.post(f"/api/projects/{project['id']}/stems").json()
    # Engines are project-level only: stems carry no engine key.
    assert all("engine" not in stem for stem in built["stems"])
    updated = client.put(
        f"/api/projects/{project['id']}", json={"default_engine": "suno"}
    ).json()
    assert updated["default_engine"] == "suno"
    assert all("engine" not in stem for stem in updated["stems"])
