from __future__ import annotations

from pathlib import Path

import pytest

from drama_studio import handlers as handler_module
from drama_studio.jobs import JobQueue
from drama_studio.refvoice import (
    PreparedReference,
    ReferencePreparationError,
)
from drama_studio.store import ProjectStore
from drama_studio.worker import Worker

SCRIPT = """# TEST

## SCENE 1 — ROOM

**MARA** *(cold)*
You're late.
"""


@pytest.fixture
def project_store(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / "projects")
    project = store.create("Test", "en", SCRIPT)
    character = project.script.characters["MARA"]
    directory = store.project_dir(project.id)
    upload_dir = directory / "refs" / ".uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    upload = upload_dir / "sample.wav"
    upload.write_bytes(b"fake audio")
    monkeypatch.setattr(handler_module, "_STORE", store)
    return store, project, character, upload


def _prepared(output: Path, text: str = "You're late.") -> PreparedReference:
    return PreparedReference(
        file=str(output),
        duration_s=8.0,
        sample_rate=24000,
        f0_hz=150.0,
        f0_range_hz=(140.0, 160.0),
        ref_text=text,
        transcript_confidence=0.91,
        window_start_s=1.0,
        window_end_s=9.0,
        warnings=["Advisory warning"],
    )


def test_generate_reference_appends_voice_and_sets_first_default(
    project_store, monkeypatch, tmp_path
):
    store, project, _character, upload = project_store
    queue_path = tmp_path / "jobs.sqlite3"
    monkeypatch.setattr(handler_module, "prepare_reference", lambda *a, **k: _prepared(tmp_path / "refs" / "mara-sample.wav"))
    monkeypatch.setattr(
        handler_module,
        "_require_gpu",
        lambda name: (_ for _ in ()).throw(AssertionError("generation must not touch the GPU")),
    )
    queue = JobQueue(queue_path)
    job = queue.enqueue(
        "reference.generate",
        {
            "project_id": project.id,
            "speaker": "MARA",
            "input_path": str(upload.relative_to(store.project_dir(project.id))),
            "name": "Sample 1",
            "make_default": False,
        },
    )
    Worker(
        queue,
        {"reference.generate": handler_module.generate_reference},
        worker_id="test",
    ).run_once()
    finished = queue.get(job.id)
    assert finished.status == "succeeded", finished.error

    updated = store.get(project.id)
    updated_character = updated.script.characters["MARA"]
    assert len(updated_character.references) == 1
    entry = updated_character.references[0]
    assert entry.source == "generated"
    assert entry.ref_text == "You're late."
    assert entry.transcript_source == "whisper"
    assert entry.transcript_confidence == pytest.approx(0.91)
    assert entry.duration_s == pytest.approx(8.0)
    assert entry.f0_hz == pytest.approx(150.0)
    assert entry.warnings == ["Advisory warning"]
    assert updated_character.ref_file == entry.file
    assert updated_character.ref_text == entry.ref_text
    assert finished.result["ref_voice_id"] == entry.id
    assert not upload.exists()


def test_generate_reference_keeps_existing_default(project_store, monkeypatch, tmp_path):
    store, project, character, upload = project_store
    directory = store.project_dir(project.id)
    (directory / "refs" / "mara.wav").write_bytes(b"default")
    character.ref_file = "refs/mara.wav"
    character.ref_text = "Default transcript."
    store.save(project)

    monkeypatch.setattr(
        handler_module,
        "prepare_reference",
        lambda *a, **k: _prepared(tmp_path / "refs" / "mara-sample.wav"),
    )
    queue = JobQueue(tmp_path / "jobs.sqlite3")
    job = queue.enqueue(
        "reference.generate",
        {
            "project_id": project.id,
            "speaker": "MARA",
            "input_path": str(upload.relative_to(directory)),
            "name": "Sample 1",
            "make_default": False,
        },
    )
    Worker(
        queue,
        {"reference.generate": handler_module.generate_reference},
        worker_id="test",
    ).run_once()
    assert queue.get(job.id).status == "succeeded"

    updated = store.get(project.id)
    updated_character = updated.script.characters["MARA"]
    assert updated_character.ref_file == "refs/mara.wav"
    assert updated_character.ref_text == "Default transcript."
    # The pre-existing default is now registered too (so the Stems step can
    # select it), and the new voice is the one flagged as generated.
    generated = [item for item in updated_character.references if item.source == "generated"]
    assert [item.name for item in generated] == ["Sample 1"]
    assert generated[0].file == "refs/mara-sample-1.wav"
    assert updated_character.ref_file == "refs/mara.wav"


def test_generate_reference_rejects_chatterbox(project_store, monkeypatch, tmp_path):
    store, project, _character, upload = project_store
    project.default_engine = "chatterbox"
    store.save(project)
    queue = JobQueue(tmp_path / "jobs.sqlite3")
    job = queue.enqueue(
        "reference.generate",
        {
            "project_id": project.id,
            "speaker": "MARA",
            "input_path": str(upload.relative_to(store.project_dir(project.id))),
        },
    )
    Worker(
        queue,
        {"reference.generate": handler_module.generate_reference},
        worker_id="test",
    ).run_once()
    finished = queue.get(job.id)
    assert finished.status == "failed"
    assert "chatterbox" in (finished.error or "").lower()


def test_generate_reference_maps_preparation_error(project_store, monkeypatch, tmp_path):
    store, project, _character, upload = project_store

    def fail(*args, **kwargs):
        raise ReferencePreparationError("no speech here")

    monkeypatch.setattr(handler_module, "prepare_reference", fail)
    queue = JobQueue(tmp_path / "jobs.sqlite3")
    job = queue.enqueue(
        "reference.generate",
        {
            "project_id": project.id,
            "speaker": "MARA",
            "input_path": str(upload.relative_to(store.project_dir(project.id))),
        },
    )
    Worker(
        queue,
        {"reference.generate": handler_module.generate_reference},
        worker_id="test",
    ).run_once()
    finished = queue.get(job.id)
    assert finished.status == "failed"
    assert "no speech" in (finished.error or "")


def test_generate_reference_uses_german_for_breeze_de(
    project_store, monkeypatch, tmp_path
):
    store, project, _character, upload = project_store
    project.language = "de"
    project.default_engine = "breeze-de"
    project.script.language = "de"
    store.save(project)
    captured: dict = {}

    def fake_prepare(input_path, output_path, **kwargs):
        captured.update(kwargs)
        return _prepared(tmp_path / "refs" / "mara-sample.wav", text="Guten Tag.")

    monkeypatch.setattr(handler_module, "prepare_reference", fake_prepare)
    queue = JobQueue(tmp_path / "jobs.sqlite3")
    job = queue.enqueue(
        "reference.generate",
        {
            "project_id": project.id,
            "speaker": "MARA",
            "input_path": str(upload.relative_to(store.project_dir(project.id))),
        },
    )
    Worker(
        queue,
        {"reference.generate": handler_module.generate_reference},
        worker_id="test",
    ).run_once()
    assert queue.get(job.id).status == "succeeded"
    assert captured["language"] == "de"


def test_generate_reference_rejects_path_escape(project_store, monkeypatch, tmp_path):
    _store, project, _character, _upload = project_store
    queue = JobQueue(tmp_path / "jobs.sqlite3")
    job = queue.enqueue(
        "reference.generate",
        {
            "project_id": project.id,
            "speaker": "MARA",
            "input_path": "../../outside.wav",
        },
    )
    Worker(
        queue,
        {"reference.generate": handler_module.generate_reference},
        worker_id="test",
    ).run_once()
    finished = queue.get(job.id)
    assert finished.status == "failed"
    assert "escapes" in (finished.error or "")
