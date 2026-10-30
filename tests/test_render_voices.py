from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from drama_studio import handlers as handler_module
from drama_studio.api import create_app
from drama_studio.engines.fake import FakeEngine
from drama_studio.engines.suno import SunoEngine
from drama_studio.jobs import JobQueue
from drama_studio.models import (
    Character,
    RefVoice,
    Stem,
    StemRenderRequest,
)
from drama_studio.stems import build_stems
from drama_studio.sfx import SfxLibrary
from drama_studio.store import ProjectStore
from drama_studio.worker import Worker

SCRIPT = """# TEST

## SCENE 1 — ROOM

**MARA** *(cold)*
You're late.
"""


class FakeManager:
    def __init__(self, engine):
        self._engine = engine

    def get(self, name: str):
        return self._engine


def test_ref_voice_model_defaults():
    character = Character(id="MARA", name="Mara")
    assert character.references == []
    assert character.ref_file is None


def test_stem_range_validation():
    with pytest.raises(Exception):
        Stem(
            id="s1",
            scene=1,
            speaker="MARA",
            line_ids=[],
            text="Hi.",
            instruction="...",
            suno_weirdness=101,
        )


def test_suno_engine_forwards_params(tmp_path, monkeypatch):
    import drama_studio.settings as settings_module

    monkeypatch.setattr(settings_module, "SETTINGS_PATH", tmp_path / "settings.json")
    settings_module.write_suno({"plan": "pro"})

    captured: dict = {}

    class FakeBridge:
        def generate(self, payload):
            captured.update(payload)
            return {"clips": []}

    engine = SunoEngine()
    engine.bridge = FakeBridge()  # type: ignore[assignment]
    engine._loaded = True
    request = StemRenderRequest(
        project_id="p",
        stem_id="s1-mara",
        speaker="MARA",
        text="You're late.",
        instruction="...",
        output_dir=str(tmp_path),
        mv="chirp-goose",
        suno_weirdness=70,
        suno_style_weight=35,
        suno_audio_weight=80,
        suno_vocal_gender="f",
    )
    with pytest.raises(RuntimeError, match="no clips"):
        engine.render_stem(request)
    assert captured["mv"] == "chirp-goose"
    assert captured["weirdness"] == 70
    assert captured["style_weight"] == 35
    assert captured["audio_weight"] == 80
    assert captured["vocal_gender"] == "f"


def test_suno_engine_param_defaults(tmp_path):
    captured: dict = {}

    class FakeBridge:
        def generate(self, payload):
            captured.update(payload)
            return {"clips": []}

    engine = SunoEngine()
    engine.bridge = FakeBridge()  # type: ignore[assignment]
    engine._loaded = True
    request = StemRenderRequest(
        project_id="p",
        stem_id="s1-mara",
        speaker="MARA",
        text="You're late.",
        instruction="...",
        output_dir=str(tmp_path),
        suno_vocal_gender="bogus",
    )
    with pytest.raises(RuntimeError, match="no clips"):
        engine.render_stem(request)
    # Free-plan safe: advanced knobs are omitted unless explicitly set.
    assert "style_weight" not in captured
    assert captured["mv"] == "chirp-hawk"
    assert "vocal_gender" not in captured
    assert captured["mv"] == "chirp-hawk"
    assert "weirdness" not in captured
    assert "audio_weight" not in captured
    assert "vocal_gender" not in captured


def _named_voice_project(tmp_path):
    store = ProjectStore(tmp_path / "projects")
    project = store.create("Test", "en", SCRIPT)
    project.stems = build_stems(project)
    character = project.script.characters["MARA"]
    ref_dir = store.project_dir(project.id) / "refs"
    ref_dir.mkdir(parents=True, exist_ok=True)
    (ref_dir / "mara.wav").write_bytes(b"default")
    (ref_dir / "mara-raspy.wav").write_bytes(b"named")
    character.ref_file = "refs/mara.wav"
    character.ref_text = "You're late."
    character.ref_instruction = "Cold."
    character.references = [
        RefVoice(id="raspy", name="Raspy", file="refs/mara-raspy.wav")
    ]
    store.save(project)
    return store, project


def _run_render(tmp_path, store, project, payload_extra):
    monkeypatch_holder = {}
    import unittest.mock as mock

    with mock.patch.object(handler_module, "_STORE", store), mock.patch.object(
        handler_module, "_MANAGER", FakeManager(FakeEngine())
    ), mock.patch.object(handler_module, "_require_gpu", lambda name: None):
        queue = JobQueue(tmp_path / "jobs.sqlite3")
        payload = {
            "project_id": project.id,
            "stem_id": "s1-mara",
            "candidates": 1,
            "seed": None,
            "cfg_scale": None,
            "align": False,
            "engine": "breeze",
        }
        payload.update(payload_extra)
        job = queue.enqueue("stem.render", payload)
        Worker(queue, {"stem.render": handler_module.render_stem}, worker_id="test").run_once()
        finished = queue.get(job.id)
        assert finished.status == "succeeded", finished.error
        monkeypatch_holder["job"] = finished
    return store.get(project.id)


def test_render_uses_named_ref_voice(tmp_path):
    store, project = _named_voice_project(tmp_path)
    updated = _run_render(tmp_path, store, project, {"ref_voice_id": "raspy"})
    stem = next(item for item in updated.stems if item.id == "s1-mara")
    assert len(stem.takes) == 1


def test_render_unknown_ref_voice_fails(tmp_path):
    store, project = _named_voice_project(tmp_path)
    import unittest.mock as mock

    with mock.patch.object(handler_module, "_STORE", store), mock.patch.object(
        handler_module, "_MANAGER", FakeManager(FakeEngine())
    ), mock.patch.object(handler_module, "_require_gpu", lambda name: None):
        queue = JobQueue(tmp_path / "jobs.sqlite3")
        job = queue.enqueue(
            "stem.render",
            {
                "project_id": project.id,
                "stem_id": "s1-mara",
                "candidates": 1,
                "align": False,
                "engine": "breeze",
                "ref_voice_id": "nope",
            },
        )
        Worker(queue, {"stem.render": handler_module.render_stem}, worker_id="test").run_once()
        finished = queue.get(job.id)
        assert finished.status == "failed"
        assert "nope" in (finished.error or "")


def test_design_reference_with_name_appends(tmp_path):
    store = ProjectStore(tmp_path / "projects")
    project = store.create("Test", "en", SCRIPT)
    character = project.script.characters["MARA"]
    store.save(project)
    import unittest.mock as mock

    with mock.patch.object(handler_module, "_STORE", store), mock.patch.object(
        handler_module, "_MANAGER", FakeManager(FakeEngine())
    ), mock.patch.object(handler_module, "_require_gpu", lambda name: None):
        queue = JobQueue(tmp_path / "jobs.sqlite3")
        job = queue.enqueue(
            "reference.design",
            {
                "project_id": project.id,
                "speaker": "MARA",
                "ref_text": "You're late.",
                "ref_instruction": "Cold.",
                "name": "Raspy",
            },
        )
        Worker(
            queue, {"reference.design": handler_module.design_reference}, worker_id="test"
        ).run_once()
        finished = queue.get(job.id)
        assert finished.status == "succeeded", finished.error
    updated = store.get(project.id)
    character = updated.script.characters["MARA"]
    assert character.ref_file is not None
    assert [item.name for item in character.references] == ["Raspy"]
    assert character.references[0].file.endswith(".wav")


def make_client(tmp_path):
    from drama_studio.llm import LLMError

    class FailingLLM:
        def chat_json(self, *args, **kwargs):
            raise LLMError("offline")

    app = create_app(
        tmp_path / "jobs.sqlite3", tmp_path / "projects", llm_client=FailingLLM()
    )
    return TestClient(app)


def test_api_stem_voice_and_params(tmp_path):
    client = make_client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"title": "T", "language": "en", "script_text": SCRIPT},
    ).json()
    built = client.post(f"/api/projects/{project['id']}/stems").json()
    stem = next(s for s in built["stems"] if s["speaker"] == "MARA")
    updated = client.patch(
        f"/api/projects/{project['id']}/stems/{stem['id']}",
        json={
            "ref_voice_id": "raspy",
            "suno_weirdness": 70,
            "suno_mv": "chirp-goose",
            "suno_vocal_gender": "f",
        },
    )
    assert updated.status_code == 200
    saved = next(s for s in updated.json()["stems"] if s["id"] == stem["id"])
    assert saved["ref_voice_id"] == "raspy"
    assert saved["suno_weirdness"] == 70
    assert saved["suno_mv"] == "chirp-goose"
    assert saved["suno_vocal_gender"] == "f"
    cleared = client.patch(
        f"/api/projects/{project['id']}/stems/{stem['id']}",
        json={"ref_voice_id": "", "suno_mv": ""},
    )
    saved = next(s for s in cleared.json()["stems"] if s["id"] == stem["id"])
    assert saved["ref_voice_id"] is None
    assert saved["suno_mv"] is None
    bad_gender = client.patch(
        f"/api/projects/{project['id']}/stems/{stem['id']}",
        json={"suno_vocal_gender": "x"},
    )
    assert bad_gender.status_code == 400
    bad_range = client.patch(
        f"/api/projects/{project['id']}/stems/{stem['id']}",
        json={"suno_weirdness": 150},
    )
    assert bad_range.status_code == 422


def test_extract_stem_demucs(tmp_path):
    store = ProjectStore(tmp_path / "projects")
    project = store.create("T", "en", SCRIPT)
    project.stems = build_stems(project)
    store.save(project)
    project = store.get(project.id)
    stem = next(s for s in project.stems if s.id == "s1-mara")
    stem.engine = "fake"
    store.save(project)

    sfx_root = tmp_path / "sfx"
    sfx_root.mkdir()
    library = SfxLibrary(
        roots=[sfx_root],
        cache_path=tmp_path / "sfx_index.json",
        excludes=set(),
        upload_root=sfx_root,
    )

    import unittest.mock as mock

    with mock.patch.object(handler_module, "_STORE", store), mock.patch.object(
        handler_module, "_SFX", library
    ), mock.patch.object(handler_module, "_MANAGER", FakeManager(FakeEngine())):
        queue = JobQueue(tmp_path / "jobs.sqlite3")
        render_job = queue.enqueue(
            "stem.render",
            {
                "project_id": project.id,
                "stem_id": stem.id,
                "candidates": 1,
                "align": False,
                "engine": "fake",
            },
        )
        Worker(queue, {"stem.render": handler_module.render_stem}, worker_id="test").run_once()
        assert queue.get(render_job.id).status == "succeeded"

        project = store.get(project.id)
        stem = next(s for s in project.stems if s.id == stem.id)
        take = stem.takes[0]

        extract_job = queue.enqueue(
            "stem.extract",
            {
                "project_id": project.id,
                "stem_id": stem.id,
                "take_id": take.id,
                "method": "demucs",
                "align": False,
            },
        )
        Worker(
            queue,
            {"stem.extract": handler_module.extract_stem},
            worker_id="test",
        ).run_once()
        finished = queue.get(extract_job.id)
        assert finished.status == "succeeded", finished.error
        project = store.get(project.id)
        stem = next(s for s in project.stems if s.id == stem.id)
        assert any(t.engine == "demucs" and t.vocal_isolated for t in stem.takes)


def test_api_render_passes_voice_params(tmp_path):
    client = make_client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"title": "T", "language": "en", "script_text": SCRIPT},
    ).json()
    built = client.post(f"/api/projects/{project['id']}/stems").json()
    stem = next(s for s in built["stems"] if s["speaker"] == "MARA")
    response = client.post(
        f"/api/projects/{project['id']}/stems/{stem['id']}/render",
        json={"engine": "fake", "ref_voice_id": "raspy", "suno_weirdness": 10},
    )
    assert response.status_code == 202
    payload = response.json()["payload"]
    assert payload["ref_voice_id"] == "raspy"
    assert payload["suno_weirdness"] == 10
