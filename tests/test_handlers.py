from __future__ import annotations

from drama_studio import handlers as handler_module
from drama_studio.engines.fake import FakeEngine
from drama_studio.jobs import JobQueue
from drama_studio.models import StemRenderResult, Take
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

    def is_loaded(self, name: str) -> bool:
        return False


def test_require_gpu_exempts_resident_engine(monkeypatch):
    """A second same-engine job must not fail the free-VRAM gate.

    The load spike was already paid; rendering reuses the resident model.
    """
    monkeypatch.setattr(handler_module, "_gpu_free_gb", lambda: 0.5)
    with monkeypatch.context() as patch:
        patch.setattr(handler_module, "_MANAGER", FakeManager())
        try:
            handler_module._require_gpu("breeze")
        except Exception as exc:
            assert "GPU busy" in str(exc)
        else:
            raise AssertionError("expected GPU busy without a resident engine")

    class ResidentManager(FakeManager):
        def is_loaded(self, name: str) -> bool:
            return True

    monkeypatch.setattr(handler_module, "_MANAGER", ResidentManager())
    handler_module._require_gpu("breeze")  # must not raise


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


class SunoStubEngine:
    """One new full-mix take per call, like a Suno generation."""

    def __init__(self) -> None:
        self.calls = 0

    def render_stem(self, request, *, progress=None, cancelled=None):
        self.calls += 1
        number = self.calls
        take = Take(
            id=f"suno-{request.stem_id}-gen{number}",
            file=f"audio/takes/gen{number}.wav",
            engine="suno",
            duration_s=10.0,
            sample_rate=24000,
            seed=number,
            clip_id=f"clip-{number}",
            vocal_isolated=False,
            selected=True,
        )
        return StemRenderResult(
            stem_id=request.stem_id,
            engine="suno",
            sample_rate=24000,
            takes=[take],
            selected_take_id=take.id,
            text_sha256="stub",
        )


class SunoManager:
    def __init__(self) -> None:
        self.engine = SunoStubEngine()

    def get(self, name: str):
        assert name == "suno"
        return self.engine


def test_suno_render_appends_takes_and_preserves_cuts(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / "projects")
    project = store.create("Test", "en", SCRIPT, default_engine="suno")
    project.stems = build_stems(project)
    store.save(project)

    manager = SunoManager()
    monkeypatch.setattr(handler_module, "_STORE", store)
    monkeypatch.setattr(handler_module, "_MANAGER", manager)
    monkeypatch.setattr(handler_module, "_require_gpu", lambda name: None)

    queue = JobQueue(tmp_path / "jobs.sqlite3")
    workers = {"stem.render": handler_module.render_stem}
    payload = {
        "project_id": project.id,
        "stem_id": "s1-mara",
        "candidates": 1,
        "engine": "suno",
        "align": True,
    }
    queue.enqueue("stem.render", payload)
    Worker(queue, workers, worker_id="test").run_once()

    first = store.get(project.id)
    stem = next(item for item in first.stems if item.id == "s1-mara")
    first_take = stem.takes[0]
    alignment = first.alignments[stem.id]
    assert {line.take_id for line in alignment.lines} == {first_take.id}

    # A user trim: this must survive the next generation untouched.
    alignment.lines[0].start_s = 1.25
    alignment.lines[0].end_s = 2.75
    first.alignments[stem.id] = alignment
    store.save(first)

    queue.enqueue("stem.render", payload)
    Worker(queue, workers, worker_id="test").run_once()

    updated = store.get(project.id)
    stem = next(item for item in updated.stems if item.id == "s1-mara")
    assert len(stem.takes) == 2
    newest = next(take for take in stem.takes if take.selected)
    assert newest.id != first_take.id
    assert newest.id == "suno-s1-mara-gen2"

    kept = updated.alignments[stem.id]
    assert kept.lines[0].take_id == first_take.id
    assert kept.lines[0].start_s == 1.25
    assert kept.lines[0].end_s == 2.75


def test_suno_render_dedupes_same_clip(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / "projects")
    project = store.create("Test", "en", SCRIPT, default_engine="suno")
    project.stems = build_stems(project)
    store.save(project)

    manager = SunoManager()

    # Reuse the same clip id on every generation: the old copy is replaced.
    def same_clip(request, *, progress=None, cancelled=None):
        manager.engine.calls += 1
        take = Take(
            id=f"suno-{request.stem_id}-copy{manager.engine.calls}",
            file=f"audio/takes/copy{manager.engine.calls}.wav",
            engine="suno",
            duration_s=10.0,
            sample_rate=24000,
            seed=1,
            clip_id="clip-same",
            vocal_isolated=False,
            selected=True,
        )
        return StemRenderResult(
            stem_id=request.stem_id,
            engine="suno",
            sample_rate=24000,
            takes=[take],
            selected_take_id=take.id,
            text_sha256="stub",
        )

    manager.engine.render_stem = same_clip  # type: ignore[method-assign]
    monkeypatch.setattr(handler_module, "_STORE", store)
    monkeypatch.setattr(handler_module, "_MANAGER", manager)
    monkeypatch.setattr(handler_module, "_require_gpu", lambda name: None)

    queue = JobQueue(tmp_path / "jobs.sqlite3")
    workers = {"stem.render": handler_module.render_stem}
    payload = {
        "project_id": project.id,
        "stem_id": "s1-mara",
        "candidates": 1,
        "engine": "suno",
        "align": False,
    }
    queue.enqueue("stem.render", payload)
    Worker(queue, workers, worker_id="test").run_once()
    queue.enqueue("stem.render", payload)
    Worker(queue, workers, worker_id="test").run_once()

    stem = next(
        item for item in store.get(project.id).stems if item.id == "s1-mara"
    )
    assert len(stem.takes) == 1
    assert stem.takes[0].id.endswith("copy2")
