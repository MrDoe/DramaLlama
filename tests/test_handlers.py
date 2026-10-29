from __future__ import annotations

from drama_studio import handlers as handler_module
from drama_studio.engines.fake import FakeEngine
from drama_studio.jobs import JobQueue
from drama_studio.stems import build_stems
from drama_studio.store import ProjectStore
from drama_studio.worker import Worker

SCRIPT = """# TEST

## SCENE 1 — ROOM

**MARA** *(cold)*
You're late.

**MARA** *(sharper)*
Pack.
"""


class FakeManager:
    def get(self, name: str):
        return FakeEngine()


def test_render_stem_handler_updates_project(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / "projects")
    project = store.create("Test", "en", SCRIPT)
    project.stems = build_stems(project)
    character = project.script.characters["MARA"]
    character.ref_file = "refs/mara.wav"
    character.ref_text = "You're late."
    character.ref_instruction = "Cold."
    ref = store.project_dir(project.id) / character.ref_file
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_bytes(b"placeholder")
    store.save(project)

    monkeypatch.setattr(handler_module, "_STORE", store)
    monkeypatch.setattr(handler_module, "_MANAGER", FakeManager())
    monkeypatch.setattr(handler_module, "_require_gpu", lambda name: None)

    queue = JobQueue(tmp_path / "jobs.sqlite3")
    job = queue.enqueue(
        "stem.render",
        {
            "project_id": project.id,
            "stem_id": "s1-mara",
            "candidates": 1,
            "seed": None,
            "cfg_scale": None,
            "align": False,
        },
    )
    Worker(queue, {"stem.render": handler_module.render_stem}, worker_id="test").run_once()

    finished = queue.get(job.id)
    assert finished.status == "succeeded", finished.error
    updated = store.get(project.id)
    stem = next(item for item in updated.stems if item.id == "s1-mara")
    assert len(stem.takes) == 1
    assert stem.takes[0].selected
    assert (store.project_dir(project.id) / stem.takes[0].file).exists()
