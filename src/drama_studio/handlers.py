from __future__ import annotations

import time
from typing import Any

from .aligner import WhisperAligner
from .config import PROJECTS_DIR
from .engines.fake import FakeEngine
from .engines.manager import EngineManager
from .mixer import build_mix
from .models import (
    Job,
    Line,
    Project,
    ReferenceRequest,
    StemRenderRequest,
)
from .sfx import SfxLibrary
from .store import ProjectStore
from .worker import Handler, JobCancelled, JobContext, PermanentJobError

_MANAGER = EngineManager()
_STORE = ProjectStore(PROJECTS_DIR)
_SFX = SfxLibrary()


def demo_sleep(job: Job, ctx: JobContext) -> dict[str, Any]:
    seconds = float(job.payload.get("seconds", 2.0))
    steps = max(1, int(seconds * 5))
    for step in range(steps):
        ctx.check_cancelled()
        time.sleep(seconds / steps)
        ctx.progress((step + 1) / steps, f"slept {step + 1}/{steps}")
    return {"slept_s": seconds}


def demo_echo(job: Job, ctx: JobContext) -> dict[str, Any]:
    return {"echo": job.payload}


def fake_render_stem(job: Job, ctx: JobContext) -> dict[str, Any]:
    try:
        request = StemRenderRequest.model_validate(job.payload)
    except Exception as exc:
        raise PermanentJobError(f"invalid stem render payload: {exc}") from exc

    engine = FakeEngine()
    try:
        result = engine.render_stem(
            request,
            progress=lambda value, message: ctx.progress(value, message),
            cancelled=lambda: ctx.cancelled(),
        )
    except RuntimeError as exc:
        if "cancelled" in str(exc).lower():
            raise JobCancelled(str(exc)) from exc
        raise
    return result.model_dump(mode="json")


def _engine_name(project: Project) -> str:
    return "breeze" if project.language == "en" else "qwen3"


def _load_project(project_id: str) -> Project:
    try:
        return _STORE.get(project_id)
    except KeyError as exc:
        raise PermanentJobError(f"project {project_id!r} not found") from exc


def _gpu_free_gb() -> float | None:
    try:
        import torch

        free, _ = torch.cuda.mem_get_info()
        return free / 1e9
    except Exception:
        return None


def _require_gpu(engine_name: str) -> None:
    needed = 9.0 if engine_name == "breeze" else 5.0
    free = _gpu_free_gb()
    if free is not None and free < needed:
        raise PermanentJobError(
            f"GPU busy: {free:.1f} GB free, need about {needed:.1f} GB for "
            f"{engine_name}. Unload other models and retry."
        )


def _lines_for_stem(project: Project, line_ids: list[str]) -> list[Line]:
    by_id = {
        line.id: line
        for scene in project.script.scenes
        for line in scene.lines
    }
    return [by_id[line_id] for line_id in line_ids if line_id in by_id]


def design_reference(job: Job, ctx: JobContext) -> dict[str, Any]:
    payload = job.payload
    project_id = payload["project_id"]
    speaker = payload["speaker"]
    project = _load_project(project_id)
    character = project.script.characters.get(speaker)
    if character is None:
        raise PermanentJobError(f"character {speaker!r} not found in project")

    if payload.get("ref_text"):
        character.ref_text = payload["ref_text"].strip()
    if payload.get("ref_instruction"):
        character.ref_instruction = payload["ref_instruction"].strip()
    if not character.ref_text or not character.ref_instruction:
        raise PermanentJobError("reference text and instruction are required")

    directory = _STORE.project_dir(project.id)
    output = directory / "refs" / f"{speaker.lower().replace(' ', '_')}.wav"
    output.parent.mkdir(parents=True, exist_ok=True)
    cfg_value = payload.get("cfg_scale")
    seed_value = payload.get("seed")
    request = ReferenceRequest(
        character_id=speaker,
        text=character.ref_text,
        instruction=character.ref_instruction,
        output_path=str(output),
        cfg_scale=float(cfg_value) if cfg_value is not None else 4.0,
        seed=int(seed_value) if seed_value is not None else 42,
    )

    _require_gpu(_engine_name(project))
    ctx.progress(0.02, f"loading {_engine_name(project)}")
    engine = _MANAGER.get(_engine_name(project))
    ctx.progress(0.1, f"designing reference for {speaker}")
    result = engine.design_reference(request)
    character.ref_file = str(output.relative_to(directory))
    character.f0_hz = result.f0_hz
    _STORE.save(project)
    ctx.progress(1.0, "reference ready")
    return {
        "speaker": speaker,
        "ref_file": character.ref_file,
        "reference": result.model_dump(mode="json"),
    }


def render_stem(job: Job, ctx: JobContext) -> dict[str, Any]:
    payload = job.payload
    project_id = payload["project_id"]
    stem_id = payload["stem_id"]
    project = _load_project(project_id)
    stem = next((item for item in project.stems if item.id == stem_id), None)
    if stem is None:
        raise PermanentJobError(f"stem {stem_id!r} not found in project")
    character = project.script.characters.get(stem.speaker)
    if character is None:
        raise PermanentJobError(f"character {stem.speaker!r} not found in project")

    engine_name = _engine_name(project)
    directory = _STORE.project_dir(project.id)
    ref_path = None
    if engine_name == "breeze":
        if not character.ref_file:
            raise PermanentJobError(
                f"no reference voice for {stem.speaker}; design it first"
            )
        ref_path = directory / character.ref_file
        if not ref_path.exists():
            raise PermanentJobError(f"reference file missing: {ref_path}")
    elif not character.qwen_speaker:
        raise PermanentJobError(
            f"choose a Qwen3 preset voice for {stem.speaker} before rendering"
        )

    cfg_value = payload.get("cfg_scale")
    seed_value = payload.get("seed")
    request = StemRenderRequest(
        project_id=project.id,
        stem_id=stem.id,
        speaker=stem.speaker,
        text=stem.text,
        instruction=stem.instruction,
        output_dir=str(directory),
        ref_audio_path=str(ref_path) if ref_path else None,
        ref_text=character.ref_text,
        voice_preset=character.qwen_speaker,
        language=project.language,
        cfg_scale=float(cfg_value) if cfg_value is not None else stem.cfg_scale,
        seed=int(seed_value) if seed_value is not None else stem.seed,
        candidates=int(payload.get("candidates", 2)),
    )

    _require_gpu(engine_name)
    ctx.progress(0.02, f"loading {engine_name}")
    engine = _MANAGER.get(engine_name)
    ctx.progress(0.05, f"rendering {stem.speaker} / scene {stem.scene}")
    try:
        result = engine.render_stem(
            request,
            progress=lambda value, message: ctx.progress(0.05 + 0.7 * value, message),
            cancelled=lambda: ctx.cancelled(),
        )
    except RuntimeError as exc:
        if "cancelled" in str(exc).lower():
            raise JobCancelled(str(exc)) from exc
        raise

    stem.takes = result.takes
    _STORE.save(project)

    selected = next((take for take in result.takes if take.selected), None)
    alignment = None
    if bool(payload.get("align", True)) and selected is not None:
        ctx.check_cancelled()
        ctx.progress(0.78, "aligning stem with Whisper")
        lines = _lines_for_stem(project, stem.line_ids)
        if lines:
            aligner = WhisperAligner(language=project.language)
            alignment = aligner.align(
                directory / selected.file,
                lines,
                directory,
                stem_id=stem.id,
                language=project.language,
            )
            project.alignments[stem.id] = alignment
            _STORE.save(project)

    ctx.progress(1.0, "stem ready")
    return {
        "stem_id": stem.id,
        "render": result.model_dump(mode="json"),
        "alignment": alignment.model_dump(mode="json") if alignment else None,
    }


def align_stem(job: Job, ctx: JobContext) -> dict[str, Any]:
    payload = job.payload
    try:
        stem_path = payload["stem_path"]
        output_dir = payload["output_dir"]
        lines = [Line.model_validate(item) for item in payload["lines"]]
        language = payload.get("language", "en")
    except Exception as exc:
        raise PermanentJobError(f"invalid align payload: {exc}") from exc

    ctx.progress(0.02, "loading Whisper")
    aligner = WhisperAligner(language=language)
    ctx.progress(0.1, f"aligning {len(lines)} lines")
    result = aligner.align(
        stem_path,
        lines,
        output_dir,
        stem_id=payload.get("stem_id"),
        language=language,
    )
    return result.model_dump(mode="json")


def realign_stem(job: Job, ctx: JobContext) -> dict[str, Any]:
    project = _load_project(job.payload["project_id"])
    stem = next(
        (item for item in project.stems if item.id == job.payload["stem_id"]), None
    )
    if stem is None:
        raise PermanentJobError("stem not found")
    selected = next((take for take in stem.takes if take.selected), None)
    if selected is None:
        raise PermanentJobError("stem has no selected take")
    lines = _lines_for_stem(project, stem.line_ids)
    if not lines:
        raise PermanentJobError("stem has no lines")

    directory = _STORE.project_dir(project.id)
    ctx.progress(0.1, "loading Whisper")
    aligner = WhisperAligner(language=project.language)
    ctx.progress(0.2, f"realigning {len(lines)} lines")
    alignment = aligner.align(
        directory / selected.file,
        lines,
        directory,
        stem_id=stem.id,
        language=project.language,
    )
    project.alignments[stem.id] = alignment
    _STORE.save(project)
    ctx.progress(1.0, "alignment ready")
    return alignment.model_dump(mode="json")


def mix_project(job: Job, ctx: JobContext) -> dict[str, Any]:
    project = _load_project(job.payload["project_id"])
    directory = _STORE.project_dir(project.id)
    ctx.progress(0.1, "building dialogue timeline")
    result = build_mix(project, directory, _SFX.get)
    project.mix = result
    _STORE.save(project)
    ctx.progress(1.0, "mix ready")
    return result.model_dump(mode="json")


def unload_engines(job: Job, ctx: JobContext) -> dict[str, Any]:
    _MANAGER.unload_all()
    return {"unloaded": True}


def default_handlers() -> dict[str, Handler]:
    return {
        "demo.sleep": demo_sleep,
        "demo.echo": demo_echo,
        "stem.render.fake": fake_render_stem,
        "stem.render": render_stem,
        "stem.render.breeze": render_stem,
        "reference.design": design_reference,
        "reference.design.breeze": design_reference,
        "stem.align.breeze": align_stem,
        "stem.realign": realign_stem,
        "mix.render": mix_project,
        "engine.unload": unload_engines,
    }
