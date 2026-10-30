from __future__ import annotations

import unittest.mock as mock

import numpy as np
import soundfile as sf

from drama_studio.api import create_app
from drama_studio.jobs import JobQueue
from drama_studio.stems import build_stems
from drama_studio.store import ProjectStore
from drama_studio.worker import Worker

SCRIPT = """# LIBRARY TEST

## SCENE 1

MARA (cold): You're late.
"""


class FakeBridge:
    def list_library(self, limit=50):
        return {
            "ok": True,
            "clips": [
                {
                    "id": "clip-1",
                    "title": "dialogue s1-mara",
                    "status": "complete",
                    "duration": 12.5,
                    "created_at": "2026-10-30T13:00:00Z",
                }
            ],
        }

    def wait_for_clip(self, clip_id, timeout_s=120):
        return {"id": clip_id, "status": "complete", "duration": 12.5}

    def download(self, clip_id, output_dir, fmt="wav"):
        from pathlib import Path

        path = Path(output_dir) / f"import_{clip_id[-4:]}.wav"
        sf.write(str(path), np.zeros(12_000, dtype=np.float32), 24000)
        return path


def make_api_client(tmp_path, monkeypatch, bridge):
    from fastapi.testclient import TestClient

    import drama_studio.api as api_module

    monkeypatch.setattr(api_module, "SunoBridge", lambda *args, **kwargs: bridge)
    app = create_app(tmp_path / "jobs.sqlite3", tmp_path / "projects")
    return TestClient(app)


def test_api_suno_library_lists(tmp_path, monkeypatch):
    client = make_api_client(tmp_path, monkeypatch, FakeBridge())
    response = client.get("/api/suno/library")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["clips"][0]["title"] == "dialogue s1-mara"


def test_api_suno_library_bridge_error_is_502(tmp_path, monkeypatch):
    from drama_studio.api import create_app
    from drama_studio.suno import SunoError

    class FailingBridge(FakeBridge):
        def list_library(self, limit=50):
            raise SunoError("down")

    client = make_api_client(tmp_path, monkeypatch, FailingBridge())
    assert client.get("/api/suno/library").status_code == 502


def _library_project(tmp_path):
    store = ProjectStore(tmp_path / "projects")
    project = store.create("Library", "en", SCRIPT)
    project.stems = build_stems(project)
    store.save(project)
    return store, project


def test_import_suno_take_attaches_take(tmp_path):
    import drama_studio.handlers as handler_module

    store, project = _library_project(tmp_path)
    stem = next(item for item in project.stems if item.id == "s1-mara")
    with mock.patch.object(handler_module, "_STORE", store), mock.patch.object(
        handler_module, "SunoBridge", lambda *args, **kwargs: FakeBridge()
    ):
        queue = JobQueue(tmp_path / "jobs.sqlite3")
        job = queue.enqueue(
            "stem.import_suno",
            {"project_id": project.id, "stem_id": stem.id, "clip_id": "clip-1"},
        )
        Worker(
            queue, {"stem.import_suno": handler_module.import_suno_take}, worker_id="test"
        ).run_once()
        finished = queue.get(job.id)
        assert finished.status == "succeeded", finished.error

    updated = store.get(project.id)
    stem = next(item for item in updated.stems if item.id == "s1-mara")
    assert len(stem.takes) == 1
    take = stem.takes[0]
    assert take.selected is True
    assert take.engine == "suno"
    assert take.clip_id == "clip-1"
    assert (store.project_dir(project.id) / take.file).exists()
    alignment = updated.alignments[stem.id]
    assert alignment.lines and all(line.fallback for line in alignment.lines)


def test_import_suno_take_requires_clip_id(tmp_path):
    import drama_studio.handlers as handler_module

    store, project = _library_project(tmp_path)
    stem = next(item for item in project.stems if item.id == "s1-mara")
    with mock.patch.object(handler_module, "_STORE", store):
        queue = JobQueue(tmp_path / "jobs.sqlite3")
        job = queue.enqueue(
            "stem.import_suno",
            {"project_id": project.id, "stem_id": stem.id, "clip_id": ""},
        )
        Worker(
            queue, {"stem.import_suno": handler_module.import_suno_take}, worker_id="test"
        ).run_once()
        finished = queue.get(job.id)
        assert finished.status == "failed"
        assert "clip_id" in (finished.error or "")


def test_import_suno_endpoint_enqueues(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import drama_studio.api as api_module

    monkeypatch.setattr(api_module, "SunoBridge", lambda *args, **kwargs: FakeBridge())
    client = TestClient(create_app(tmp_path / "jobs.sqlite3", tmp_path / "projects"))
    project = client.post(
        "/api/projects", json={"title": "Library", "language": "en", "script_text": SCRIPT}
    ).json()
    built = client.post(f"/api/projects/{project['id']}/stems").json()
    stem_id = built["stems"][0]["id"]
    response = client.post(
        f"/api/projects/{project['id']}/stems/{stem_id}/import-suno",
        json={"clip_id": "clip-1"},
    )
    assert response.status_code == 202
    assert response.json()["type"] == "stem.import_suno"
    missing = client.post(
        f"/api/projects/{project['id']}/stems/{stem_id}/import-suno", json={}
    )
    assert missing.status_code == 400
