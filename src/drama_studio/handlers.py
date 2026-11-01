from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from .aligner import WhisperAligner
from .config import DATA_DIR, PROJECTS_DIR
from .cuts import (
    freeze_legacy_cuts,
    normalize_alignment,
    pick_selected_take,
    remap_removed_cuts,
)
from .engines import engine_info
from .engines.fake import FakeEngine
from .engines.manager import EngineManager
from .engines.suno import CaptchaRequiredError, _convert_to_wav, _pick_vocal_stem
from .mixer import build_mix
from .settings import resolve_suno
from .models import (
    AlignedLine,
    AlignmentResult,
    Character,
    Job,
    Line,
    Project,
    ReferenceRequest,
    RefVoice,
    StemRenderRequest,
    SunoTrack,
    Take,
)
from .sfx import SfxLibrary
from .store import ProjectStore, slugify
from .suno import SunoBridge, SunoError
from .suno_lyrics import format_suno_lyrics
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
    if project.default_engine:
        return project.default_engine
    return "breeze" if project.language == "en" else "chatterbox"


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
    if engine_name in {"fake", "chatterbox", "suno"}:
        return
    if _MANAGER.is_loaded(engine_name):
        # Resident already: rendering reuses the loaded model instead of
        # paying the load spike again, so the free-VRAM gate does not apply.
        return
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


def _freeze_legacy_cuts(
    alignment: AlignmentResult | None, fallback: Take | None
) -> None:
    """Back-compat wrapper around :func:`cuts.freeze_legacy_cuts`."""
    freeze_legacy_cuts(alignment, fallback)


def _remap_removed_cuts(
    alignment: AlignmentResult | None,
    removed: dict[str, Take],
    new_take: Take,
    directory: Path,
) -> None:
    """Back-compat wrapper: re-point cuts, failing clearly on missing audio."""
    try:
        remap_removed_cuts(alignment, removed, new_take, directory)
    except FileNotFoundError as exc:
        raise PermanentJobError(str(exc)) from exc


def _scene_heading(project: Project, scene_id: int) -> str:
    for scene in project.script.scenes:
        if scene.id == scene_id:
            return scene.heading
    return ""


def _resolve_ref_file(
    character: Character, directory: Path, ref_voice_id: str | None
) -> Path | None:
    """Resolve a reference wav for a render.

    ``ref_voice_id`` selects one of the character's named RefVoice entries;
    None means the default (legacy ``ref_file``). Returns None when the
    character has no usable reference instead of raising — callers decide
    whether that is an error (Breeze) or a fallback (Chatterbox default).
    """
    if ref_voice_id:
        entry = next(
            (item for item in character.references if item.id == ref_voice_id), None
        )
        if entry is None:
            raise PermanentJobError(
                f"unknown reference voice {ref_voice_id!r} for {character.name}"
            )
        candidate = directory / entry.file
        if not candidate.exists():
            raise PermanentJobError(f"reference file missing: {candidate}")
        return candidate
    if character.ref_file:
        candidate = directory / character.ref_file
        if candidate.exists():
            return candidate
    return None


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
    ref_name = (payload.get("name") or "").strip()
    speaker_slug = speaker.lower().replace(" ", "_")
    if ref_name:
        ref_id = slugify(ref_name, limit=32) or speaker_slug
        existing_ids = {item.id for item in character.references}
        suffix = 2
        base_id = ref_id
        while ref_id in existing_ids:
            ref_id = f"{base_id}-{suffix}"
            suffix += 1
        output = directory / "refs" / f"{speaker_slug}-{ref_id}.wav"
    else:
        ref_id = ""
        output = directory / "refs" / f"{speaker_slug}.wav"
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

    engine_name = _engine_name(project)
    if engine_name == "suno":
        raise PermanentJobError(
            "Suno uses preset voices; set a persona on the character instead "
            "of designing a reference"
        )
    _require_gpu(engine_name)
    ctx.progress(0.02, f"loading {engine_name}")
    engine = _MANAGER.get(engine_name)
    ctx.progress(0.1, f"designing reference for {speaker}")
    result = engine.design_reference(request)
    character.ref_file = str(output.relative_to(directory))
    character.f0_hz = result.f0_hz
    if ref_name:
        character.references = [
            item for item in character.references if item.id != ref_id
        ] + [
            RefVoice(
                id=ref_id,
                name=ref_name,
                file=str(output.relative_to(directory)),
                ref_text=character.ref_text,
                ref_instruction=character.ref_instruction,
                f0_hz=result.f0_hz,
            )
        ]
    _STORE.save(project)
    ctx.progress(1.0, "reference ready")
    return {
        "speaker": speaker,
        "ref_file": character.ref_file,
        "ref_voice_id": ref_id or None,
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
    try:
        info = engine_info(engine_name)
    except ValueError as exc:
        raise PermanentJobError(str(exc)) from exc
    if not info.language_agnostic and project.language not in info.languages:
        raise PermanentJobError(
            f"{engine_name} does not support project language {project.language!r}"
        )

    directory = _STORE.project_dir(project.id)
    ref_voice_id = payload.get("ref_voice_id") or stem.ref_voice_id
    ref_path = None
    if engine_name in {"breeze", "breeze-de"}:
        # A per-stem voice override wins; otherwise the default reference.
        # Either way Breeze needs a usable reference file.
        ref_path = _resolve_ref_file(character, directory, ref_voice_id)
        if ref_path is None:
            raise PermanentJobError(
                f"no reference voice for {stem.speaker}; design it first"
            )
    elif engine_name == "chatterbox":
        # Reference clips are optional; the Chatterbox server falls back to its
        # configured default voice when no sample is attached.
        ref_path = _resolve_ref_file(character, directory, ref_voice_id)
    elif engine_name == "suno":
        # Suno uses preset voices/personas; no local reference file.
        ref_path = None

    cfg_value = payload.get("cfg_scale")
    seed_value = payload.get("seed")
    lines = _lines_for_stem(project, stem.line_ids)
    # Suno sings the bracket-tagged lyric sheet, not the flat "..."-joined
    # prose: explicit payload override wins, then the stored stem sheet, then a
    # fresh deterministic sheet so legacy stems without one still render.
    suno_lyrics = payload.get("suno_lyrics") or stem.suno_lyrics
    if engine_name == "suno" and not suno_lyrics and lines:
        ctx.progress(0.03, "formatting Suno lyric sheet")
        suno_lyrics = format_suno_lyrics(
            lines, character, _scene_heading(project, stem.scene)
        )
    if suno_lyrics and suno_lyrics != stem.suno_lyrics:
        stem.suno_lyrics = suno_lyrics

    def _suno_param(name: str, default: Any = None) -> Any:
        """Render payload override wins, then the stored stem value."""
        value = payload.get(name)
        if value is None:
            value = getattr(stem, name, None)
        return default if value is None else value

    request = StemRenderRequest(
        project_id=project.id,
        stem_id=stem.id,
        speaker=stem.speaker,
        text=stem.text,
        instruction=stem.instruction,
        output_dir=str(directory),
        ref_audio_path=str(ref_path) if ref_path else None,
        ref_text=character.ref_text,
        language=project.language,
        cfg_scale=float(cfg_value) if cfg_value is not None else stem.cfg_scale,
        seed=int(seed_value) if seed_value is not None else stem.seed,
        candidates=int(payload.get("candidates", 2)),
        persona_id=getattr(character, "suno_persona_id", None),
        persona_model=getattr(character, "suno_persona_model", None),
        mv=(
            payload.get("suno_mv")
            or stem.suno_mv
            or getattr(character, "suno_mv", None)
            or "chirp-hawk"
        ),
        suno_lyrics=suno_lyrics,
        suno_title=stem.suno_title,
        suno_tags=stem.suno_tags,
        suno_negative_tags=stem.suno_negative_tags,
        suno_weirdness=_suno_param("suno_weirdness"),
        suno_style_weight=_suno_param("suno_style_weight"),
        suno_audio_weight=_suno_param("suno_audio_weight"),
        suno_vocal_gender=_suno_param("suno_vocal_gender"),
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
    except CaptchaRequiredError as exc:
        # A verification tick is required; retrying immediately would only pile
        # more requests onto Cloudflare. Fail permanently so the UI can offer
        # "Re-check verification" instead of burning attempts.
        raise PermanentJobError(str(exc)) from exc
    except RuntimeError as exc:
        if "cancelled" in str(exc).lower():
            raise JobCancelled(str(exc)) from exc
        raise

    old_selected = next((take for take in stem.takes if take.selected), None)
    existing_alignment = project.alignments.get(stem.id)
    _freeze_legacy_cuts(existing_alignment, old_selected)

    removed: dict[str, Take] = {}
    picked = pick_selected_take(result.takes, result.selected_take_id)
    if picked is None and result.takes:
        picked = result.takes[0]
    for take in result.takes:
        take.selected = picked is not None and take.id == picked.id
    if engine_name == "suno":
        # Suno generations accumulate: each render adds tracks and leaves the
        # earlier audio and cuts intact so clips can mix across generations.
        new_keys = {
            (take.engine, take.clip_id, take.vocal_isolated)
            for take in result.takes
        }
        kept: list[Take] = []
        for take in stem.takes:
            key = (take.engine, take.clip_id, take.vocal_isolated)
            if take.clip_id and key in new_keys:
                removed[take.id] = take
            else:
                kept.append(take)
        for take in kept:
            take.selected = False
        if picked is not None:
            picked.selected = True
        stem.takes = kept + result.takes
    else:
        replaced = [take for take in stem.takes if take.engine == engine_name]
        removed = {take.id: take for take in replaced}
        stem.takes = [
            take for take in stem.takes if take.engine != engine_name
        ] + result.takes
        for take in stem.takes:
            take.selected = False
        chosen = picked or result.takes[0]
        chosen.selected = True

    selected = next((take for take in stem.takes if take.selected), None)
    if selected is not None:
        _remap_removed_cuts(existing_alignment, removed, selected, directory)
    project.mix_stale = True
    _STORE.save(project)

    alignment = None
    if bool(payload.get("align", True)) and selected is not None:
        ctx.check_cancelled()
        lines = _lines_for_stem(project, stem.line_ids)
        if existing_alignment is not None and existing_alignment.lines:
            # Preserve the user's take-aware cut sheet across re-renders.
            alignment = existing_alignment
        elif lines and engine_name == "suno" and not selected.vocal_isolated:
            # Suno only separates vocals on paid plans. Whisper on a full song
            # (instruments + backing vocals) produces confidently wrong cuts,
            # so estimate timings by word count and mark them low-confidence.
            ctx.progress(0.78, "estimating line timing (no vocal stem)")
            alignment = _estimate_alignment(
                stem.id, selected.duration_s, lines, take_id=selected.id
            )
            from .cuts import seed_positions

            seed_positions(alignment)
            project.alignments[stem.id] = alignment
            project.mix_stale = True
            _STORE.save(project)
        elif lines:
            ctx.progress(0.78, "aligning stem with Whisper")
            aligner = WhisperAligner(language=project.language)
            alignment = aligner.align(
                directory / selected.file,
                lines,
                directory,
                stem_id=stem.id,
                language=project.language,
            )
            for line in alignment.lines:
                line.take_id = selected.id
            from .cuts import seed_positions

            seed_positions(alignment)
            project.alignments[stem.id] = alignment
            project.mix_stale = True
            _STORE.save(project)

    ctx.progress(1.0, "stem ready")
    return {
        "stem_id": stem.id,
        "render": result.model_dump(mode="json"),
        "alignment": alignment.model_dump(mode="json") if alignment else None,
    }


def _estimate_alignment(
    stem_id: str,
    duration_s: float,
    lines: list[Line],
    take_id: str | None = None,
) -> AlignmentResult:
    """Proportional timing for audio the aligner cannot trust.

    Used when a Suno take is a full mix (vocal separation is a paid feature):
    each line gets a slice of the clip proportional to its word count, with a
    low confidence so the UI flags it as an estimate rather than a cut.
    """
    weights = [max(1, len(line.text.split())) for line in lines]
    total = sum(weights) or 1
    cursor = 0.0
    aligned: list[AlignedLine] = []
    for line, weight in zip(lines, weights, strict=False):
        span = duration_s * weight / total
        pos = round(cursor, 2)
        aligned.append(
            AlignedLine(
                id=line.id,
                line_id=line.id,
                take_id=take_id,
                start_s=round(cursor, 2),
                end_s=round(cursor + span, 2),
                position_s=pos,
                confidence=0.05,
                fallback=True,
            )
        )
        cursor += span
    return AlignmentResult(stem_id=stem_id, lines=aligned, aligner="estimate")


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
    # A queued realign must not clobber newer manual trims: if the cut sheet
    # became manual (or mixed-take) after the job was queued, keep it.
    existing = project.alignments.get(stem.id)
    if existing is not None and existing.lines and existing.aligner == "manual":
        ctx.progress(1.0, "alignment kept (manual edits beat queued realign)")
        return existing.model_dump(mode="json")
    lines = _lines_for_stem(project, stem.line_ids)
    if not lines:
        raise PermanentJobError("stem has no lines")

    directory = _STORE.project_dir(project.id)
    if selected.engine == "suno" and not selected.vocal_isolated:
        ctx.progress(0.2, "estimating line timing (no vocal stem)")
        alignment = _estimate_alignment(
            stem.id, selected.duration_s, lines, take_id=selected.id
        )
        from .cuts import seed_positions

        seed_positions(alignment)
        project.alignments[stem.id] = alignment
        project.mix_stale = True
        _STORE.save(project)
        ctx.progress(1.0, "alignment ready (estimated)")
        return alignment.model_dump(mode="json")

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
    for line in alignment.lines:
        line.take_id = selected.id
    from .cuts import seed_positions

    seed_positions(alignment)
    project.alignments[stem.id] = alignment
    project.mix_stale = True
    _STORE.save(project)
    ctx.progress(1.0, "alignment ready")
    return alignment.model_dump(mode="json")


def import_suno_take(job: Job, ctx: JobContext) -> dict[str, Any]:
    """Attach an existing Suno library clip as a stem take.

    Recovers generations whose render job failed before downloading (or any
    other Suno clip): downloads + converts to wav, stores as the selected
    take with estimated line timing, like a fresh non-isolated Suno render.
    """
    payload = job.payload
    project_id = payload["project_id"]
    stem_id = payload["stem_id"]
    clip_id = str(payload.get("clip_id") or "").strip()
    if not clip_id:
        raise PermanentJobError("clip_id is required")

    project = _load_project(project_id)
    stem = next((item for item in project.stems if item.id == stem_id), None)
    if stem is None:
        raise PermanentJobError(f"stem {stem_id!r} not found in project")

    directory = _STORE.project_dir(project.id)
    take_dir = directory / "audio" / "takes"
    take_dir.mkdir(parents=True, exist_ok=True)
    lines = _lines_for_stem(project, stem.line_ids)

    bridge = SunoBridge()
    ctx.progress(0.1, f"waiting for Suno clip {clip_id}")
    try:
        finished = bridge.wait_for_clip(clip_id, timeout_s=120)
    except SunoError as exc:
        raise PermanentJobError(f"Suno clip {clip_id} not ready: {exc}") from exc
    preview_url = finished.get("audio_url")

    ctx.progress(0.4, "downloading Suno clip")
    try:
        m4a_path = bridge.download(clip_id, take_dir, "m4a")
    except SunoError:
        try:
            m4a_path = bridge.download(clip_id, take_dir, "wav")
        except SunoError as exc:
            raise PermanentJobError(f"Suno download failed: {exc}") from exc
    uid = uuid.uuid4().hex[:8]
    slug = slugify(f"{stem.id}-import")[:40]
    wav_path = take_dir / f"{slug}-{uid}.wav"
    try:
        if m4a_path.suffix.lower() != ".wav":
            _convert_to_wav(m4a_path, wav_path)
        else:
            shutil.copy2(m4a_path, wav_path)
    except Exception as exc:
        raise PermanentJobError(f"audio conversion failed: {exc}") from exc

    import soundfile as sf

    try:
        info = sf.info(str(wav_path))
        duration = float(info.frames) / float(info.samplerate)
        sample_rate = int(info.samplerate)
    except Exception:
        duration = float(finished.get("duration") or 0)
        sample_rate = 24000

    new_take_id = f"suno-import-{stem.id}-{uid}"
    take = Take(
        id=new_take_id,
        file=str(wav_path.relative_to(directory)),
        engine="suno",
        duration_s=duration,
        sample_rate=sample_rate,
        seed=stem.seed,
        clip_id=clip_id,
        preview_url=preview_url,
        vocal_isolated=False,
        selected=True,
    )
    old_selected = next((item for item in stem.takes if item.selected), None)
    existing_alignment = project.alignments.get(stem.id)
    _freeze_legacy_cuts(existing_alignment, old_selected)
    # Re-importing the same clip refreshes that track instead of duplicating
    # it; cuts that pointed at the old copy move to the fresh download.
    removed = {
        old.id: old
        for old in stem.takes
        if old.engine == take.engine
        and old.clip_id == take.clip_id
        and old.vocal_isolated == take.vocal_isolated
    }
    kept = [old for old in stem.takes if old.id not in removed]
    for existing in kept:
        existing.selected = False
    stem.takes = kept + [take]
    _remap_removed_cuts(existing_alignment, removed, take, directory)
    project.mix_stale = True
    _STORE.save(project)

    alignment = None
    if lines:
        if existing_alignment is not None and existing_alignment.lines:
            alignment = existing_alignment
        else:
            ctx.progress(0.8, "estimating line timing (imported Suno mix)")
            alignment = _estimate_alignment(
                stem.id, duration, lines, take_id=take.id
            )
            from .cuts import seed_positions

            seed_positions(alignment)
            project.alignments[stem.id] = alignment
            project.mix_stale = True
            _STORE.save(project)

    ctx.progress(1.0, "Suno clip imported")
    return {
        "stem_id": stem.id,
        "take": take.model_dump(mode="json"),
        "alignment": alignment.model_dump(mode="json") if alignment else None,
    }


def extract_stem(job: Job, ctx: JobContext) -> dict[str, Any]:
    payload = job.payload
    project_id = payload["project_id"]
    stem_id = payload["stem_id"]
    take_id = payload["take_id"]
    method = str(payload.get("method") or "demucs").strip().lower()
    if method not in {"demucs", "suno"}:
        raise PermanentJobError(f"unknown extraction method {method!r}")

    project = _load_project(project_id)
    stem = next((item for item in project.stems if item.id == stem_id), None)
    if stem is None:
        raise PermanentJobError(f"stem {stem_id!r} not found in project")
    take = next((item for item in stem.takes if item.id == take_id), None)
    if take is None:
        raise PermanentJobError(f"take {take_id!r} not found in stem")

    directory = _STORE.project_dir(project.id)
    source = directory / take.file
    if not source.exists():
        raise PermanentJobError(f"take audio not found: {source}")

    lines = _lines_for_stem(project, stem.line_ids)
    take_dir = directory / "audio" / "takes"
    take_dir.mkdir(parents=True, exist_ok=True)

    slug = slugify(f"{stem.id}-{take.id}")[:40]
    uid = uuid.uuid4().hex[:8]
    vocal_name = f"{slug}-{uid}-vocals.wav"
    instr_name = f"{slug}-{uid}-instrumental.wav"
    vocal_path = take_dir / vocal_name
    instr_path = take_dir / instr_name

    ctx.progress(0.05, f"extracting stem ({method})")
    instrumental_sfx_id = None

    if method == "demucs":
        device = str(payload.get("device") or "cpu").strip().lower()
        if device not in {"cpu", "cuda"}:
            device = "cpu"
        work_dir = directory / "audio" / "extracted" / take.id
        work_dir.mkdir(parents=True, exist_ok=True)
        # Clean previous outputs for this take.
        for child in work_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
        cmd = [
            sys.executable,
            "-m",
            "demucs",
            "-n",
            "htdemucs",
            "--two-stems",
            "vocals",
            "-d",
            device,
            "-o",
            str(work_dir),
            str(source),
        ]
        try:
            subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or b"").decode("utf-8", "replace").strip()
            raise PermanentJobError(f"Demucs failed: {detail or exc}") from exc
        # Demucs writes work_dir/htdemucs/<track>/vocals.wav + no_vocals.wav
        vocal_src = None
        instr_src = None
        for wav in work_dir.rglob("*.wav"):
            name = wav.name.lower()
            if name == "vocals.wav":
                vocal_src = wav
            elif name == "no_vocals.wav":
                instr_src = wav
        if vocal_src is None:
            raise PermanentJobError("Demucs did not produce vocals.wav")
        shutil.copy2(vocal_src, vocal_path)
        if instr_src is not None:
            shutil.copy2(instr_src, instr_path)
        engine_name = "demucs"
    else:
        plan, _ = resolve_suno()
        if plan.plan != "pro":
            raise PermanentJobError(
                "Suno stem extraction requires a Pro/Premier plan"
            )
        if not take.clip_id:
            raise PermanentJobError("take has no Suno clip id")
        try:
            stems_result = SunoBridge().make_stems(take.clip_id)
        except Exception as exc:
            raise PermanentJobError(f"Suno stem extraction failed: {exc}") from exc
        stem_clips = stems_result.get("clips") or []
        vocal = _pick_vocal_stem(stem_clips)
        if not vocal or not vocal.get("id"):
            raise PermanentJobError("Suno returned no vocal stem")
        instr_clip = None
        for clip in stem_clips:
            title = str(clip.get("title") or "").lower()
            if "instrumental" in title or "no_vocal" in title:
                instr_clip = clip
                break
        if instr_clip is None and len(stem_clips) > 1:
            # Fall back to the clip that is not the vocal.
            for clip in stem_clips:
                if clip.get("id") != vocal.get("id"):
                    instr_clip = clip
                    break

        def _download(clip_id: str, dst: Path) -> Path:
            m4a_path = None
            try:
                m4a_path = SunoBridge().download(clip_id, take_dir, "m4a")
            except SunoError:
                try:
                    m4a_path = SunoBridge().download(clip_id, take_dir, "wav")
                except SunoError as exc:
                    raise PermanentJobError(f"Suno download failed: {exc}") from exc
            wav_path = dst
            try:
                if m4a_path.suffix.lower() != ".wav":
                    _convert_to_wav(m4a_path, wav_path)
                else:
                    shutil.copy2(m4a_path, wav_path)
            except Exception as exc:
                raise PermanentJobError(f"audio conversion failed: {exc}") from exc
            return wav_path

        _download(vocal.get("id"), vocal_path)
        if instr_clip and instr_clip.get("id"):
            _download(instr_clip.get("id"), instr_path)
        engine_name = "suno"

    # Probe vocal duration.
    import soundfile as sf

    try:
        info = sf.info(str(vocal_path))
        duration = float(info.frames) / float(info.samplerate)
        sample_rate = int(info.samplerate)
    except Exception:
        duration = take.duration_s
        sample_rate = take.sample_rate

    new_take_id = f"{method}-{stem.id}-{uid}"
    vocal_take = Take(
        id=new_take_id,
        file=str(vocal_path.relative_to(directory)),
        engine=engine_name,
        duration_s=duration,
        sample_rate=sample_rate,
        seed=take.seed,
        clip_id=take.clip_id,
        preview_url=take.preview_url,
        vocal_isolated=True,
        selected=True,
    )
    old_selected = next((item for item in stem.takes if item.selected), None)
    existing_alignment = project.alignments.get(stem.id)
    _freeze_legacy_cuts(existing_alignment, old_selected)

    # Deselect others, append new vocal take.
    for existing in stem.takes:
        existing.selected = False
    stem.takes = [t for t in stem.takes if t.id != new_take_id] + [vocal_take]
    project.mix_stale = True
    _STORE.save(project)

    # Add instrumental to the Sound library.
    if instr_path.exists():
        try:
            item = _SFX.add_upload(
                instr_path,
                filename=f"{stem.speaker} {stem.id} instrumental.wav",
            )
            instrumental_sfx_id = item.id
        except Exception:
            instrumental_sfx_id = None

    # Auto-align vocal take if requested.
    alignment = None
    if bool(payload.get("align", True)) and lines:
        mixed = bool(existing_alignment and existing_alignment.lines) and any(
            line.take_id not in {None, take.id}
            for line in existing_alignment.lines
        )
        if mixed:
            # The cut sheet mixes several takes; replacing it would throw
            # those choices away, so leave it (and its line clips) alone.
            alignment = existing_alignment
        else:
            ctx.progress(0.85, "aligning extracted vocal")
            aligner = WhisperAligner(language=project.language)
            try:
                alignment = aligner.align(
                    directory / vocal_take.file,
                    lines,
                    directory,
                    stem_id=stem.id,
                    language=project.language,
                )
                for line in alignment.lines:
                    line.take_id = new_take_id
                from .cuts import seed_positions

                seed_positions(alignment)
                project.alignments[stem.id] = alignment
                project.mix_stale = True
                _STORE.save(project)
            except Exception:
                alignment = None

    ctx.progress(1.0, "stem extracted")
    return {
        "stem_id": stem.id,
        "take_id": take.id,
        "new_take_id": new_take_id,
        "method": method,
        "instrumental_sfx_id": instrumental_sfx_id,
        "alignment": alignment.model_dump(mode="json") if alignment else None,
    }

def suno_generate(job: Job, ctx: JobContext) -> dict[str, Any]:
    project = _load_project(job.payload["project_id"])
    payload = job.payload
    bridge = SunoBridge()
    try:
        health = bridge.health()
    except SunoError as exc:
        raise PermanentJobError(str(exc)) from exc
    if not health.get("extensionConnected"):
        raise PermanentJobError(
            "BetterSuno extension is not connected. Open suno.com with the "
            "BetterSuno extension loaded, then retry."
        )

    ctx.progress(0.05, "submitting music prompt to Suno")
    try:
        result = bridge.generate(
            {
                "kind": payload.get("kind", "instrumental"),
                "prompt": payload.get("prompt", ""),
                "title": payload.get("title", ""),
                "tags": payload.get("tags", ""),
                "duration": payload.get("duration"),
                "lyrics": payload.get("lyrics"),
                "vocal_gender": payload.get("vocal_gender"),
            }
        )
    except SunoError as exc:
        raise PermanentJobError(str(exc)) from exc

    clips = result.get("clips") or []
    if not clips:
        raise PermanentJobError("Suno returned no clips")
    ctx.progress(0.15, f"Suno queued {len(clips)} clip(s); waiting for audio")

    out_dir = DATA_DIR / "sfx" / "suno" / project.id
    out_dir.mkdir(parents=True, exist_ok=True)
    tracks: list[SunoTrack] = []
    for index, clip in enumerate(clips[:2]):
        clip_id = clip.get("id")
        if not clip_id:
            continue
        ctx.progress(0.2 + 0.5 * index / max(1, len(clips)), f"waiting for clip {clip_id}")
        try:
            finished = bridge.wait_for_clip(clip_id, timeout_s=180)
            file = bridge.download(clip_id, out_dir, "wav")
        except SunoError as exc:
            ctx.log(f"clip {clip_id} failed: {exc}")
            continue
        _SFX.scan()
        item = _SFX.find_by_path(file)
        if item is None:
            ctx.log(f"downloaded {file.name} but it was not indexed")
            continue
        track = SunoTrack(
            clip_id=clip_id,
            title=finished.get("title") or clip.get("title") or "Suno track",
            file=str(file.relative_to(DATA_DIR / "sfx")),
            sfx_id=item.id,
            duration_s=item.duration_s,
            prompt=payload.get("prompt", ""),
        )
        tracks.append(track)
        project.suno.append(track)

    if not tracks:
        raise PermanentJobError("Suno clips finished but no audio could be downloaded")
    _STORE.save(project)
    ctx.progress(1.0, "Suno music ready")
    return {"tracks": [track.model_dump(mode="json") for track in tracks]}


def mix_project(job: Job, ctx: JobContext) -> dict[str, Any]:
    project = _load_project(job.payload["project_id"])
    directory = _STORE.project_dir(project.id)
    ctx.progress(0.1, "building dialogue timeline")
    result = build_mix(project, directory, _SFX.get)
    project.mix = result
    project.mix_stale = False
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
        "stem.extract": extract_stem,
        "stem.import_suno": import_suno_take,
        "reference.design": design_reference,
        "reference.design.breeze": design_reference,
        "stem.align.breeze": align_stem,
        "stem.realign": realign_stem,
        "suno.generate": suno_generate,
        "mix.render": mix_project,
        "engine.unload": unload_engines,
    }
