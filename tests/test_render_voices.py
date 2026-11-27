from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from drama_studio import handlers as handler_module
from drama_studio.api import create_app
from drama_studio.cuts import suno_take_needing_extraction
from drama_studio.engines.fake import FakeEngine
from drama_studio.engines.suno import SunoEngine
from drama_studio.jobs import JobQueue, enqueue_vocal_extraction
from drama_studio.models import (
    Character,
    RefVoice,
    Stem,
    StemRenderRequest,
    Take,
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


class CapturingEngine(FakeEngine):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[StemRenderRequest] = []

    def render_stem(self, request, *, progress=None, cancelled=None):
        self.requests.append(request)
        return super().render_stem(
            request, progress=progress, cancelled=cancelled
        )


def test_ref_voice_model_defaults():
    character = Character(id="MARA", name="Mara")
    assert character.references == []
    assert character.ref_file is None


def _character_with_legacy_default(tmp_path):
    store = ProjectStore(tmp_path / "projects")
    project = store.create("Test", "en", SCRIPT)
    character = project.script.characters["MARA"]
    ref_dir = store.project_dir(project.id) / "refs"
    ref_dir.mkdir(parents=True, exist_ok=True)
    (ref_dir / "mara.wav").write_bytes(b"default")
    character.ref_file = "refs/mara.wav"
    character.ref_text = "You're late."
    character.ref_instruction = "Cold."
    store.save(project)
    return store, project


def test_legacy_default_becomes_selectable(tmp_path):
    """A designed voice with no name still shows up in the per-stem list."""
    store, project = _character_with_legacy_default(tmp_path)
    raw = json.loads(
        (store.project_dir(project.id) / "project.json").read_text(encoding="utf-8")
    )
    assert raw["script"]["characters"]["MARA"]["references"] == []

    loaded = store.get(project.id)
    character = loaded.script.characters["MARA"]
    assert len(character.references) == 1
    entry = character.references[0]
    assert entry.file == "refs/mara.wav"
    assert entry.ref_text == "You're late."
    assert entry.name.startswith("Voice ")


def test_migration_does_not_change_default_resolution(tmp_path):
    """Selecting the adopted entry renders the same clip as the default."""
    from drama_studio.handlers import _resolve_reference

    store, project = _character_with_legacy_default(tmp_path)
    directory = store.project_dir(project.id)
    loaded = store.get(project.id)
    character = loaded.script.characters["MARA"]

    by_default = _resolve_reference(character, directory, None)
    by_id = _resolve_reference(character, directory, character.references[0].id)
    assert by_default is not None and by_id is not None
    assert by_default.path == by_id.path
    assert by_default.ref_text == by_id.ref_text


def test_migration_is_idempotent(tmp_path):
    store, project = _character_with_legacy_default(tmp_path)
    first = store.get(project.id)
    assert len(first.script.characters["MARA"].references) == 1
    first = store.save(first)
    again = store.get(project.id)
    assert len(again.script.characters["MARA"].references) == 1


def test_registered_default_is_not_duplicated(tmp_path):
    store, project = _character_with_legacy_default(tmp_path)
    character = project.script.characters["MARA"]
    character.references = [
        RefVoice(id="raspy", name="Raspy", file="refs/mara.wav", ref_text="You're late.")
    ]
    store.save(project)
    loaded = store.get(project.id)
    assert [item.id for item in loaded.script.characters["MARA"].references] == ["raspy"]


def test_character_without_reference_gains_nothing(tmp_path):
    store = ProjectStore(tmp_path / "projects")
    project = store.create("Test", "en", SCRIPT)
    store.save(project)
    loaded = store.get(project.id)
    assert loaded.script.characters["MARA"].references == []


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
        RefVoice(
            id="raspy",
            name="Raspy",
            file="refs/mara-raspy.wav",
            ref_text="You're late.",
        )
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


def test_render_uses_named_ref_transcript(tmp_path):
    store, project = _named_voice_project(tmp_path)
    stored = store.get(project.id)
    stored.script.characters["MARA"].references[0].ref_text = (
        "Raspy generated transcript."
    )
    store.save(stored)

    import unittest.mock as mock

    engine = CapturingEngine()
    with mock.patch.object(handler_module, "_STORE", store), mock.patch.object(
        handler_module, "_MANAGER", FakeManager(engine)
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
                "ref_voice_id": "raspy",
            },
        )
        Worker(
            queue, {"stem.render": handler_module.render_stem}, worker_id="test"
        ).run_once()
        assert queue.get(job.id).status == "succeeded"

    assert engine.requests
    assert engine.requests[0].ref_text == "Raspy generated transcript."


def test_render_named_ref_without_transcript_fails(tmp_path):
    store, project = _named_voice_project(tmp_path)
    stored = store.get(project.id)
    stored.script.characters["MARA"].references[0].ref_text = None
    store.save(stored)

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
                "ref_voice_id": "raspy",
            },
        )
        Worker(
            queue, {"stem.render": handler_module.render_stem}, worker_id="test"
        ).run_once()
        finished = queue.get(job.id)
        assert finished.status == "failed"
        assert "transcript" in (finished.error or "").lower()


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
    assert character.references[0].source == "designed"
    assert character.references[0].duration_s is not None


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
    project = store.create("T", "en", SCRIPT, default_engine="fake")
    project.stems = build_stems(project)
    store.save(project)
    project = store.get(project.id)
    stem = next(s for s in project.stems if s.id == "s1-mara")
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


def _suno_take(take_id: str, clip_id: str, **extra) -> Take:
    fields = {"engine": "suno", "duration_s": 1, "sample_rate": 24000, "seed": 1}
    fields.update(extra)
    return Take(id=take_id, file=f"audio/takes/{take_id}.wav", clip_id=clip_id, **fields)


def test_suno_take_needing_extraction_follows_the_selected_take():
    stem = Stem(id="s1-mara", scene=1, speaker="MARA", line_ids=[], text="", instruction="")
    picked = _suno_take("suno-a", "clip-a", selected=True)
    other = _suno_take("suno-b", "clip-b")
    stem.takes = [picked, other]
    assert suno_take_needing_extraction(stem) is picked

    # Once its vocal exists, the other candidate must not be pulled in: that
    # would override the user's choice of generation.
    vocal = _suno_take(
        "demucs-a", "clip-a", engine="demucs", vocal_isolated=True, selected=True
    )
    picked.selected = False
    stem.takes.append(vocal)
    assert suno_take_needing_extraction(stem) is None
    # Switching back to the original does not extract it a second time.
    vocal.selected = False
    picked.selected = True
    assert suno_take_needing_extraction(stem) is None


def _suno_project(tmp_path):
    client = make_client(tmp_path)
    project = client.post(
        "/api/projects",
        json={
            "title": "T", "language": "en", "script_text": SCRIPT,
            "default_engine": "suno",
        },
    ).json()
    built = client.post(f"/api/projects/{project['id']}/stems").json()
    stem = next(s for s in built["stems"] if s["speaker"] == "MARA")
    return client, project["id"], stem["id"]


def test_api_render_does_not_queue_extraction_up_front(tmp_path):
    client, project_id, stem_id = _suno_project(tmp_path)
    response = client.post(
        f"/api/projects/{project_id}/stems/{stem_id}/render",
        json={"auto_extract_vocals": True},
    )
    assert response.status_code == 202
    assert response.json()["payload"]["auto_extract_vocals"] is True
    jobs = JobQueue(tmp_path / "jobs.sqlite3").list_jobs()
    assert [job.type for job in jobs if job.type == "stem.extract"] == []


class SunoLikeEngine(FakeEngine):
    """Two candidates, the first one picked, like the Suno engine."""

    def __init__(self, *, fail: bool = False) -> None:
        super().__init__()
        self.fail = fail

    def render_stem(self, request, *, progress=None, cancelled=None):
        if self.fail:
            raise RuntimeError("quota exceeded")
        result = super().render_stem(request, progress=progress, cancelled=cancelled)
        for index, take in enumerate(result.takes):
            take.engine = "suno"
            take.clip_id = f"clip-{index}"
        result.selected_take_id = result.takes[0].id
        return result


@pytest.mark.parametrize("fail", [False, True])
def test_render_queues_extraction_only_after_success(tmp_path, fail):
    store = ProjectStore(tmp_path / "projects")
    project = store.create("T", "en", SCRIPT, default_engine="suno")
    project.stems = build_stems(project)
    # An older generation that was never extracted must be left alone.
    stem = next(s for s in project.stems if s.id == "s1-mara")
    stem.takes = [_suno_take("suno-old", "clip-old", selected=True)]
    store.save(project)
    queue = JobQueue(tmp_path / "jobs.sqlite3")
    render = queue.enqueue(
        "stem.render",
        {
            "project_id": project.id, "stem_id": stem.id, "candidates": 2,
            "align": False, "auto_extract_vocals": True,
        },
    )
    engine = SunoLikeEngine(fail=fail)
    with mock.patch.object(handler_module, "_STORE", store), mock.patch.object(
        handler_module, "_MANAGER", FakeManager(engine)
    ), mock.patch.object(handler_module, "_require_gpu", lambda _: None):
        Worker(queue, {"stem.render": handler_module.render_stem}, worker_id="t").run_once()

    queued = [job for job in queue.list_jobs() if job.type == "stem.extract"]
    finished = queue.get(render.id)
    if fail:
        assert finished.status != "succeeded"
        assert queued == []
        return
    assert finished.status == "succeeded", finished.error
    saved = store.get(project.id)
    selected = next(
        t for s in saved.stems if s.id == stem.id for t in s.takes if t.selected
    )
    assert selected.clip_id == "clip-0"
    assert [job.payload["take_id"] for job in queued] == [selected.id]
    assert finished.result["extract_job_id"] == queued[0].id


def test_extract_keeps_edits_made_while_it_runs(tmp_path):
    store = ProjectStore(tmp_path / "projects")
    project = store.create("T", "en", SCRIPT, default_engine="suno")
    project.stems = build_stems(project)
    stem = next(s for s in project.stems if s.id == "s1-mara")
    stem.takes = [_suno_take("suno-a", "clip-a", selected=True)]
    store.save(project)
    directory = store.project_dir(project.id)
    source = directory / stem.takes[0].file
    source.parent.mkdir(parents=True, exist_ok=True)
    sf.write(source, np.zeros(2400, dtype=np.float32), 24000)

    def demucs_with_concurrent_edit(cmd, **_kwargs):
        edited = store.get(project.id)
        edited.settings.pause_ms = 1234
        next(s for s in edited.stems if s.id == stem.id).suno_title = "Edited"
        store.save(edited)
        output = Path(cmd[cmd.index("-o") + 1]) / "htdemucs" / "track"
        output.mkdir(parents=True)
        sf.write(output / "vocals.wav", np.zeros(2400, dtype=np.float32), 24000)

    queue = JobQueue(tmp_path / "jobs.sqlite3")
    job = enqueue_vocal_extraction(queue, project.id, stem.id, "suno-a", align=False)
    with mock.patch.object(handler_module, "_STORE", store), mock.patch.object(
        handler_module.subprocess, "run", demucs_with_concurrent_edit
    ):
        Worker(queue, {"stem.extract": handler_module.extract_stem}, worker_id="t").run_once()

    assert queue.get(job.id).status == "succeeded", queue.get(job.id).error
    saved = store.get(project.id)
    saved_stem = next(s for s in saved.stems if s.id == stem.id)
    assert saved.settings.pause_ms == 1234
    assert saved_stem.suno_title == "Edited"
    assert any(t.vocal_isolated and t.selected for t in saved_stem.takes)


def test_api_extract_all_targets_only_stems_that_need_it(tmp_path):
    client, project_id, stem_id = _suno_project(tmp_path)
    store = ProjectStore(tmp_path / "projects")
    saved = store.get(project_id)
    stem = next(s for s in saved.stems if s.id == stem_id)
    stem.takes = [_suno_take("suno-1", "clip-1", selected=True)]
    store.save(saved)

    body = client.post(f"/api/projects/{project_id}/stems/extract-all").json()
    assert body["count"] == 1
    assert body["jobs"][0]["payload"]["take_id"] == "suno-1"

    for job in body["jobs"]:
        client.post(f"/api/jobs/{job['id']}/cancel")
    saved = store.get(project_id)
    stem = next(s for s in saved.stems if s.id == stem_id)
    stem.takes.append(
        _suno_take("demucs-1", "clip-1", engine="demucs", vocal_isolated=True)
    )
    store.save(saved)
    again = client.post(f"/api/projects/{project_id}/stems/extract-all").json()
    assert again == {"jobs": [], "count": 0}


def test_api_render_passes_voice_params(tmp_path):
    client = make_client(tmp_path)
    project = client.post(
        "/api/projects",
        json={
            "title": "T",
            "language": "en",
            "script_text": SCRIPT,
            "default_engine": "fake",
        },
    ).json()
    built = client.post(f"/api/projects/{project['id']}/stems").json()
    stem = next(s for s in built["stems"] if s["speaker"] == "MARA")
    response = client.post(
        f"/api/projects/{project['id']}/stems/{stem['id']}/render",
        json={"ref_voice_id": "raspy", "suno_weirdness": 10},
    )
    assert response.status_code == 202
    payload = response.json()["payload"]
    assert payload["ref_voice_id"] == "raspy"
    assert payload["suno_weirdness"] == 10


def test_unnamed_design_still_registers_the_voice(tmp_path):
    """A name-less REST design must not leave the panel offering the old voice.

    Writing only `ref_file` used to swap the audio the renders use while the
    Voices list kept showing the previous reference as if it were current.
    """
    store = ProjectStore(tmp_path / "projects")
    project = store.create("Test", "en", SCRIPT)
    character = project.script.characters["MARA"]
    character.references = [
        RefVoice(id="voice-1", name="Voice 1", file="refs/mara-voice-1.wav", source="designed")
    ]
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
            },
        )
        Worker(queue, {"reference.design": handler_module.design_reference}, worker_id="t").run_once()
        finished = queue.get(job.id)
        assert finished.status == "succeeded", finished.error

    updated = store.get(project.id)
    character = updated.script.characters["MARA"]
    assert [item.name for item in character.references] == ["Voice 1", "Voice 2"]
    assert character.ref_file in {item.file for item in character.references}


def test_design_keeps_an_edit_made_while_it_was_running(tmp_path):
    """A voice direction saved during a design must not be reverted by it.

    Seen in the browser: four directions were typed into the Voices panel, three
    vanished. The job had loaded project.json before the save landed and wrote
    its own stale copy back when the GPU finished.
    """
    import unittest.mock as mock

    store = ProjectStore(tmp_path / "projects")
    project = store.create("Test", "en", SCRIPT)
    project_id = project.id

    class MidEditEngine(FakeEngine):
        """Applies a user edit while the engine is busy, as the API would."""

        def design_reference(self, request):
            result = super().design_reference(request)
            during = store.get(project_id)
            during.script.characters["MARA"].description = "Low, gravelly, afraid"
            store.save(during)
            return result

    with mock.patch.object(handler_module, "_STORE", store), mock.patch.object(
        handler_module, "_MANAGER", FakeManager(MidEditEngine())
    ), mock.patch.object(handler_module, "_require_gpu", lambda name: None):
        queue = JobQueue(tmp_path / "jobs.sqlite3")
        job = queue.enqueue(
            "reference.design",
            {
                "project_id": project_id,
                "speaker": "MARA",
                "ref_text": "You're late.",
                "ref_instruction": "Cold.",
                "name": "Voice 2",
            },
        )
        Worker(queue, {"reference.design": handler_module.design_reference}, worker_id="t").run_once()
        finished = queue.get(job.id)
        assert finished.status == "succeeded", finished.error

    final = store.get(project_id)
    character = final.script.characters["MARA"]
    assert character.description == "Low, gravelly, afraid"
    assert character.ref_file in {item.file for item in character.references}
