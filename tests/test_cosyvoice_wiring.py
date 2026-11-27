"""How the app drives CosyVoice: voice gating, directions and generation."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from drama_studio import config
from drama_studio import handlers as handler_module
from drama_studio.api import create_app
from drama_studio.engines.cosyvoice_directions import instruction_for
from drama_studio.engines.fake import FakeEngine
from drama_studio.jobs import JobQueue
from drama_studio.llm import LLMError
from drama_studio.models import StemRenderRequest
from drama_studio.stems import build_stems
from drama_studio.store import ProjectStore
from drama_studio.worker import Worker

SCRIPT = """# TEST

## SZENE 1

ANNA (leise): Kommen Sie herein.
ANNA: Wann kommt der Bus?

## SZENE 2

ANNA (wütend): Raus!
"""


class RecordingEngine(FakeEngine):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[StemRenderRequest] = []

    def render_stem(self, request, *, progress=None, cancelled=None):
        self.requests.append(request)
        result = super().render_stem(request, progress=progress, cancelled=cancelled)
        for take in result.takes:
            take.engine = "cosyvoice"
        return result


class Manager:
    def __init__(self, engine, events: list[str]) -> None:
        self.engine = engine
        self.events = events

    def get(self, name):
        self.events.append(f"load {name}")
        return self.engine

    def unload_all(self):
        self.events.append("unload all")


@pytest.fixture
def studio(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / "projects")
    project = store.create("Test", "de", SCRIPT)
    project.default_engine = "cosyvoice"
    project.stems = build_stems(project)
    refs = store.project_dir(project.id) / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "anna.wav").write_bytes(b"clip")
    character = project.script.characters["ANNA"]
    character.ref_file = "refs/anna.wav"
    character.ref_text = "Guten Tag."
    store.save(project)

    events: list[str] = []
    engine = RecordingEngine()
    monkeypatch.setattr(handler_module, "_STORE", store)
    monkeypatch.setattr(handler_module, "_MANAGER", Manager(engine, events))
    monkeypatch.setattr(handler_module, "_require_gpu", lambda name: None)
    queue = JobQueue(tmp_path / "jobs.sqlite3")

    def run(kind: str, **payload):
        job = queue.enqueue(kind, {"project_id": project.id, **payload})
        handlers = handler_module.default_handlers()
        Worker(queue, {kind: handlers[kind]}, worker_id="test").run_once()
        return queue.get(job.id)

    def render(stem_id: str = "s1-anna"):
        return run("stem.render", stem_id=stem_id, candidates=1, align=False)

    return type(
        "Studio",
        (),
        {
            "store": store,
            "project": project,
            "engine": engine,
            "events": events,
            "run": staticmethod(run),
            "render": staticmethod(render),
        },
    )


def _classifier(monkeypatch, events, calls):
    def classify(directions, client):
        events.append("classify")
        calls.append(list(directions))
        return {name: {"leise": "soft", "wütend": "angry"}.get(name, "none") for name in directions}

    monkeypatch.setattr(handler_module, "local_llm_client", lambda: object())
    monkeypatch.setattr(handler_module, "classify_directions", classify)


def test_directions_become_instructions_and_are_classified_once(studio, monkeypatch):
    calls: list[list[str]] = []
    _classifier(monkeypatch, studio.events, calls)

    assert studio.render("s1-anna").status == "succeeded"
    assert studio.render("s2-anna").status == "succeeded"

    first, second = studio.engine.requests
    assert [(s.text, s.instruction) for s in first.segments] == [
        ("Kommen Sie herein.", instruction_for("soft")),
        ("Wann kommt der Bus?", None),
    ]
    assert second.segments[0].instruction == instruction_for("angry")
    # One model load covered the whole project, before the engine was loaded.
    assert calls == [["leise", "wütend"]]
    assert studio.events[:3] == ["unload all", "classify", "load cosyvoice"]
    stored = studio.store.get(studio.project.id)
    assert stored.direction_styles == {"leise": "soft", "wütend": "angry"}


def test_without_a_local_model_the_stem_renders_plain_and_says_so(studio, monkeypatch):
    monkeypatch.setattr(handler_module, "local_llm_client", lambda: None)

    assert studio.render().status == "succeeded"

    assert [s.instruction for s in studio.engine.requests[0].segments] == [None]
    stored = studio.store.get(studio.project.id)
    take = stored.stems[0].takes[0]
    assert any("local language model" in warning for warning in take.warnings)
    assert stored.direction_styles == {}


def test_a_failing_classification_is_not_cached(studio, monkeypatch):
    def broken(directions, client):
        raise LLMError("model crashed")

    monkeypatch.setattr(handler_module, "local_llm_client", lambda: object())
    monkeypatch.setattr(handler_module, "classify_directions", broken)

    assert studio.render().status == "succeeded"

    stored = studio.store.get(studio.project.id)
    assert stored.direction_styles == {}
    assert any("model crashed" in w for w in stored.stems[0].takes[0].warnings)


def test_an_edited_stem_text_is_noted(studio, monkeypatch):
    _classifier(monkeypatch, studio.events, [])
    project = studio.store.get(studio.project.id)
    project.stems[0].text = "Ganz anders."
    studio.store.save(project)

    assert studio.render().status == "succeeded"

    take = studio.store.get(studio.project.id).stems[0].takes[0]
    assert any("edited" in warning for warning in take.warnings)


def test_render_without_a_voice_points_to_the_voices_step(studio, monkeypatch):
    monkeypatch.setattr(handler_module, "local_llm_client", lambda: None)
    project = studio.store.get(studio.project.id)
    project.script.characters["ANNA"].ref_file = None
    project.script.characters["ANNA"].references = []
    studio.store.save(project)

    job = studio.render()

    assert job.status == "failed" and "Voices step" in job.error
    assert studio.engine.requests == []


def test_design_is_refused_before_any_engine_is_loaded(studio):
    job = studio.run("reference.design", speaker="ANNA")

    assert job.status == "failed" and "sample" in job.error
    assert studio.events == []


@pytest.mark.parametrize(("backend", "fails"), [("auto", True), ("cpu", False)])
def test_gpu_gate_applies_only_to_the_gpu_backend(monkeypatch, backend, fails):
    class Nothing:
        def is_loaded(self, name):
            return False

    monkeypatch.setattr(handler_module, "_MANAGER", Nothing())
    monkeypatch.setattr(handler_module, "_gpu_free_gb", lambda: 1.0)
    monkeypatch.setattr(config, "COSYVOICE_BACKEND", backend)
    if fails:
        with pytest.raises(handler_module.PermanentJobError, match="GPU busy"):
            handler_module._require_gpu("cosyvoice")
    else:
        handler_module._require_gpu("cosyvoice")


def _api(tmp_path, language: str):
    client = TestClient(create_app(tmp_path / "jobs.sqlite3", tmp_path / "projects"))
    created = client.post(
        "/api/projects",
        json={
            "title": "T",
            "language": language,
            "default_engine": "cosyvoice",
            "script_text": SCRIPT,
        },
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    assert client.post(f"/api/projects/{project_id}/stems").status_code < 300
    return client, project_id


def test_api_refuses_render_without_voice_and_design(tmp_path):
    client, project_id = _api(tmp_path, "de")

    render = client.post(f"/api/projects/{project_id}/stems/s1-anna/render", json={})
    design = client.post(f"/api/projects/{project_id}/characters/ANNA/reference", json={})

    assert render.status_code == 409 and "sample" in render.json()["detail"]
    assert design.status_code == 400 and "sample" in design.json()["detail"]


@pytest.mark.parametrize("language", ["de", "en"])
def test_api_accepts_sample_generation_in_both_languages(tmp_path, language):
    import io

    import numpy as np
    import soundfile as sf

    client, project_id = _api(tmp_path, language)
    clip = io.BytesIO()
    sf.write(clip, 0.2 * np.sin(np.linspace(0, 8000, 24000 * 8)), 24000, format="WAV")

    response = client.post(
        f"/api/projects/{project_id}/characters/ANNA/reference/generate",
        files={"file": ("sample.wav", clip.getvalue(), "audio/wav")},
    )

    assert response.status_code == 202, response.text
