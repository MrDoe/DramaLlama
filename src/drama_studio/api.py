from __future__ import annotations

import asyncio
import json
import mimetypes
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .config import DB_PATH, PROJECTS_DIR, SUNO_BRIDGE_URL, WEB_DIST
from .engines import ENGINES, all_engine_info

def _engine_available(name: str) -> bool:
    factory = ENGINES.get(name)
    if factory is None:
        return False
    try:
        return bool(factory().available())
    except Exception:
        return False
from .jobs import JobQueue
from .llm import (
    LLMClient,
    LLMError,
    LLMParseError,
    llm_parse_script,
    make_llm_client,
    make_llm_client_for,
    normalize_messages,
    parse_normalized,
)
from .models import (
    Cue,
    EngineName,
    JobStatus,
    Language,
    ParseReport,
    ParseWarning,
    Project,
    ProjectSettings,
    RefVoice,
    Script,
)
from .settings import preview_llm, resolve_llm, resolve_suno, write_llm, write_suno
from .sfx import SfxLibrary
from .stems import build_stems
from .store import ProjectStore, slugify
from .suno import SunoBridge, SunoError
from .suno_lyrics import (
    format_suno_lyrics,
    llm_format_suno_lyrics,
    llm_suggest_suno_style,
    suggest_suno_style_deterministic,
)


class JobCreate(BaseModel):
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    priority: int = 0
    max_attempts: int = 1


class ProjectCreate(BaseModel):
    title: str = ""
    language: Language = "en"
    default_engine: EngineName | None = None
    script_text: str = ""


class ProjectUpdate(BaseModel):
    title: str | None = None
    language: Language | None = None
    raw_script: str | None = None
    settings: ProjectSettings | None = None
    default_engine: Literal["auto"] | EngineName | None = None


class ParseRequest(BaseModel):
    method: str = "deterministic"


class ScriptUpdate(BaseModel):
    script: Script
    report: ParseReport | None = None


class ReferenceBody(BaseModel):
    ref_text: str | None = None
    ref_instruction: str | None = None
    cfg_scale: float = 4.0
    seed: int = 42
    # Optional display name: stores the design as an additional named voice
    # instead of only replacing the character default.
    name: str | None = None


class RenderBody(BaseModel):
    candidates: int = Field(default=2, ge=1, le=3)
    cfg_scale: float | None = None
    seed: int | None = None
    # Deprecated per-stem override: engines are project-level only.
    # Any non-null value is rejected with 400 (see render endpoint).
    engine: str | None = None
    # Explicit Suno lyric sheet override for this render. When None the
    # stored stem sheet is used.
    suno_lyrics: str | None = None
    # Reference voice override (a RefVoice id of the speaker).
    ref_voice_id: str | None = None
    # Per-render Suno params (sliders/model/gender); None = stem value.
    suno_weirdness: float | None = Field(default=None, ge=0, le=100)
    suno_style_weight: float | None = Field(default=None, ge=0, le=100)
    suno_audio_weight: float | None = Field(default=None, ge=0, le=100)
    suno_mv: str | None = None
    suno_vocal_gender: str | None = None


class StemUpdate(BaseModel):
    # Deprecated per-stem override: engines are project-level only.
    # Any non-null value is rejected with 400 (see update_stem).
    engine: str | None = None
    cfg_scale: float | None = None
    seed: int | None = None
    # Editable source text (TTS input) for this stem.
    text: str | None = None
    # Manual lyric sheet edit (empty string clears back to the build default).
    suno_lyrics: str | None = None
    # Per-stem Suno title/tags (empty string clears back to engine defaults).
    suno_title: str | None = None
    suno_tags: str | None = None
    suno_negative_tags: str | None = None
    # Per-stem Suno params (empty/None clears back to defaults).
    suno_weirdness: float | None = Field(default=None, ge=0, le=100)
    suno_style_weight: float | None = Field(default=None, ge=0, le=100)
    suno_audio_weight: float | None = Field(default=None, ge=0, le=100)
    suno_mv: str | None = None
    suno_vocal_gender: str | None = None
    # Reference voice override (RefVoice id, empty string clears to default).
    ref_voice_id: str | None = None


class SunoLyricsBody(BaseModel):
    method: str = "llm"


class SunoStyleBody(BaseModel):
    method: str = "llm"
    overwrite: bool = False
    # Restrict the suggestion to a subset of {"title", "tags"}.
    # The tags wand passes ["tags"] so the title is never touched.
    only: list[str] | None = None


class SunoGenerate(BaseModel):
    kind: str = "instrumental"
    prompt: str = ""
    title: str = ""
    tags: str = ""
    duration: int | None = None
    lyrics: str | None = None
    vocal_gender: str | None = None


class CueCreate(BaseModel):
    kind: str = "one_shot"
    sfx_id: str
    name: str
    anchor_line_id: str
    offset_s: float = 0.0
    gain_db: float = -12.0
    fade_in_s: float = 0.0
    fade_out_s: float = 0.0
    loop: bool = False
    duck: bool = True
    filter: str = "none"


class TakeSelect(BaseModel):
    take_id: str


class CutUpdate(BaseModel):
    # Segment id (optional; backend generates if missing).
    id: str | None = None
    line_id: str
    # Which take this cut window belongs to. None = the selected take.
    take_id: str | None = None
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)
    # Freeform global timeline start. None => seed from cut durations.
    position_s: float | None = None


class AlignmentPatch(BaseModel):
    cuts: list[CutUpdate]
    rebuild_mix: bool = True


class BatchAlignmentPatch(BaseModel):
    stems: dict[str, list[CutUpdate]]
    rebuild_mix: bool = True

class CharacterUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    ref_text: str | None = None
    ref_instruction: str | None = None
    baseline_instruct: str | None = None
    # Suno voice selection. suno_persona_id "": clear back to preset voice.
    suno_persona_id: str | None = None
    suno_persona_model: str | None = None
    suno_mv: str | None = None


class LlmUpdate(BaseModel):
    api: str | None = None
    url: str | None = None
    model: str | None = None
    # null/absent: keep the stored key; "": clear it.
    api_key: str | None = None


class ConfigUpdate(BaseModel):
    llm: LlmUpdate | None = None
    suno: dict[str, str] | None = None


def create_app(
    db_path: str | Path | None = None,
    projects_dir: str | Path | None = None,
    llm_client: LLMClient | None = None,
    sfx_library: SfxLibrary | None = None,
) -> FastAPI:
    queue = JobQueue(db_path or DB_PATH)
    store = ProjectStore(Path(projects_dir) if projects_dir else PROJECTS_DIR)
    llm = llm_client or make_llm_client()
    sfx = sfx_library or SfxLibrary()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        queue.close()

    app = FastAPI(title="Drama Llama", version=__version__, lifespan=lifespan)
    app.state.queue = queue
    app.state.store = store
    app.state.llm = llm
    app.state.sfx = sfx
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "jobs": queue.stats(),
            "engines": [
                {
                    **info.__dict__,
                    "languages": sorted(info.languages),
                    "available": _engine_available(info.name),
                }
                for info in all_engine_info()
            ],
        }

    @app.get("/api/engines")
    def engines() -> list[dict[str, Any]]:
        return [
            {
                **info.__dict__,
                "languages": sorted(info.languages),
                "available": _engine_available(info.name),
            }
            for info in all_engine_info()
        ]

    def llm_status() -> dict[str, Any]:
        """Public LLM config. Never includes secrets (no API keys)."""
        settings, sources = resolve_llm()
        provider = getattr(llm, "provider", "local")
        if provider not in ("local", "cloud"):
            provider = "local"
        return {
            "llm": {
                "model": getattr(llm, "model", settings.model),
                "provider": provider,
                "api": getattr(llm, "api_name", settings.api),
                "url": settings.url,
                "apiKeyConfigured": bool(
                    getattr(llm, "api_key", settings.api_key)
                ),
                "sources": sources,
            },
            "suno": suno_block(),
        }

    def suno_block() -> dict[str, Any]:
        suno, suno_sources = resolve_suno()
        return {"plan": suno.plan, "sources": suno_sources}

    @app.get("/api/config")
    def config() -> dict[str, Any]:
        return llm_status()

    @app.put("/api/config")
    def update_config(body: ConfigUpdate) -> dict[str, Any]:
        nonlocal llm
        patch = {}
        if body.llm is not None:
            patch = {
                key: value
                for key, value in body.llm.model_dump().items()
                if value is not None
            }
        try:
            if patch:
                write_llm(patch)
            if body.suno is not None:
                write_suno(body.suno)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if patch:
            llm = make_llm_client()
            app.state.llm = llm
        return llm_status()

    @app.post("/api/config/test")
    def test_config(body: ConfigUpdate | None = None) -> dict[str, Any]:
        patch = {}
        if body is not None and body.llm is not None:
            patch = {
                key: value
                for key, value in body.llm.model_dump().items()
                if value is not None
            }
        try:
            settings = preview_llm(patch)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        client = make_llm_client_for(settings)
        if client.api_name == "openai":
            result = client.check()
            ok, usable, excluded, error = (
                result["ok"],
                result["models"],
                result["excluded"],
                result["error"],
            )
        else:
            ok = client.available()
            usable, excluded = client.probe_models() if ok else ([], [])
            error = None if ok else f"no response at {settings.url}"
        return {
            "ok": ok,
            "api": settings.api,
            "provider": client.provider,
            "model": settings.model,
            "models": usable,
            "excluded": excluded,
            "error": error,
        }

    def project_or_404(project_id: str) -> Project:
        project = store.find(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        return project

    @app.get("/api/projects")
    def list_projects() -> list[dict[str, Any]]:
        return [summary.model_dump(mode="json") for summary in store.list()]

    def check_engine(engine: str | None, language: Language) -> None:
        if engine is None:
            return
        info = next((item for item in all_engine_info() if item.name == engine), None)
        if info is None:
            raise HTTPException(status_code=400, detail=f"unknown engine {engine!r}")
        if not info.language_agnostic and language not in info.languages:
            raise HTTPException(
                status_code=400,
                detail=f"{engine} does not support language {language!r}",
            )

    @app.post("/api/projects", status_code=201)
    def create_project(body: ProjectCreate) -> dict[str, Any]:
        check_engine(body.default_engine, body.language)
        project = store.create(
            body.title, body.language, body.script_text, default_engine=body.default_engine
        )
        return project.model_dump(mode="json")

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str) -> dict[str, Any]:
        return project_or_404(project_id).model_dump(mode="json")

    @app.put("/api/projects/{project_id}")
    def update_project(project_id: str, body: ProjectUpdate) -> dict[str, Any]:
        project = project_or_404(project_id)
        changed_text = False
        if body.title is not None and body.title.strip():
            project.title = body.title.strip()
            project.script.title = project.title
        if body.language is not None and body.language != project.language:
            project.language = body.language
            changed_text = True
        if body.raw_script is not None and body.raw_script != project.raw_script:
            project.raw_script = body.raw_script
            changed_text = True
        if body.settings is not None:
            project.settings = body.settings
        effective_language = body.language or project.language
        if body.default_engine is not None:
            if body.default_engine == "auto":
                project.default_engine = None
            else:
                check_engine(body.default_engine, effective_language)
                project.default_engine = body.default_engine
            # Engines are project-level only: stems inherit the project
            # default at render time, no per-stem override is stored.
        elif project.default_engine is not None:
            info = next(
                (item for item in all_engine_info() if item.name == project.default_engine),
                None,
            )
            if (
                info is not None
                and not info.language_agnostic
                and effective_language not in info.languages
            ):
                project.default_engine = None
        if changed_text:
            project = store.reparse(project, method="deterministic")
        else:
            project = store.save(project)
        return project.model_dump(mode="json")

    @app.delete("/api/projects/{project_id}", status_code=204)
    def delete_project(project_id: str) -> None:
        project_or_404(project_id)
        store.delete(project_id)

    @app.post("/api/projects/{project_id}/parse")
    def parse_project(project_id: str, body: ParseRequest) -> dict[str, Any]:
        project = project_or_404(project_id)
        if body.method not in {"deterministic", "llm"}:
            raise HTTPException(status_code=400, detail="method must be deterministic or llm")
        if body.method == "llm":
            if any(
                job.type in {"stem.render", "reference.design"}
                for job in queue.list_jobs("running", 50)
            ):
                raise HTTPException(
                    status_code=409,
                    detail="GPU is rendering; wait for the stem to finish before LLM parse",
                )
            try:
                result = llm_parse_script(
                    project.raw_script, project.language, client=llm
                )
                result.script.title = project.title
                project = store.reparse(
                    project,
                    method="llm",
                    script=result.script,
                    report=result.report,
                )
                return project.model_dump(mode="json")
            except (LLMParseError, LLMError) as exc:
                fallback = store.reparse(project, method="deterministic")
                if fallback.report is not None:
                    fallback.report.warnings.append(
                        ParseWarning(
                            source_line=0,
                            message=f"LLM parse failed; deterministic parse kept: {exc}",
                        )
                    )
                    fallback = store.save(fallback)
                return fallback.model_dump(mode="json")
        project = store.reparse(project, method="deterministic")
        return project.model_dump(mode="json")

    @app.post("/api/projects/{project_id}/parse/stream")
    def parse_project_stream(project_id: str, body: ParseRequest):
        from fastapi.responses import StreamingResponse

        project = project_or_404(project_id)
        if body.method != "llm":
            raise HTTPException(status_code=400, detail="method must be llm for streaming")
        if any(
            job.type in {"stem.render", "reference.design"}
            for job in queue.list_jobs("running", 50)
        ):
            raise HTTPException(
                status_code=409,
                detail="GPU is rendering; wait for the stem to finish before LLM parse",
            )

        def generate():
            client = app.state.llm
            collected = ""
            try:
                for chunk in client.chat_stream(
                    normalize_messages(project.raw_script, project.language),
                    temperature=0.2,
                    keep_alive=LLM_KEEP_ALIVE if False else 0,
                    model=None,
                ):
                    collected += chunk
                    yield f"event: token\ndata: {json.dumps(chunk)}\n\n"
            except Exception as exc:
                yield f"event: error\ndata: {json.dumps(str(exc))}\n\n"
                return
            try:
                result = parse_normalized(
                    collected, project.raw_script, project.language
                )
                result.script.title = project.title
                updated = store.reparse(
                    project,
                    method="llm",
                    script=result.script,
                    report=result.report,
                )
                updated = store.save(updated)
                yield f"event: result\ndata: {json.dumps(updated.model_dump(mode='json'))}\n\n"
            except (LLMParseError, LLMError) as exc:
                fallback = store.reparse(project, method="deterministic")
                if fallback.report is not None:
                    fallback.report.warnings.append(
                        ParseWarning(
                            source_line=0,
                            message=f"LLM parse failed; deterministic parse kept: {exc}",
                        )
                    )
                    fallback = store.save(fallback)
                yield f"event: result\ndata: {json.dumps(fallback.model_dump(mode='json'))}\n\n"
            except Exception as exc:
                yield f"event: error\ndata: {json.dumps(str(exc))}\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    @app.post("/api/projects/{project_id}/stems")
    def build_project_stems(project_id: str) -> dict[str, Any]:
        project = project_or_404(project_id)
        project.stems = build_stems(project)
        project = store.save(project)
        return project.model_dump(mode="json")

    @app.post("/api/projects/{project_id}/stems/{stem_id}/suno-lyrics")
    def suno_lyrics(project_id: str, stem_id: str, body: SunoLyricsBody) -> dict[str, Any]:
        """(Re)build a stem's Suno lyric sheet.

        ``method="deterministic"`` maps each line's direction metadata to
        ``[...]`` tags instantly. ``method="llm"`` (default) asks the local LLM
        for richer delivery tags; dialogue stays verbatim and the sheet is
        rejected with 502 when the model drops or rewrites a line.
        """
        project = project_or_404(project_id)
        stem = next((item for item in project.stems if item.id == stem_id), None)
        if stem is None:
            raise HTTPException(status_code=404, detail="stem not found")
        if body.method not in {"deterministic", "llm"}:
            raise HTTPException(status_code=400, detail="method must be deterministic or llm")
        character = project.script.characters.get(stem.speaker)
        by_id = {
            line.id: line
            for scene in project.script.scenes
            for line in scene.lines
        }
        lines = [by_id[line_id] for line_id in stem.line_ids if line_id in by_id]
        if not lines:
            raise HTTPException(status_code=400, detail="stem has no lines")
        heading = next(
            (scene.heading for scene in project.script.scenes if scene.id == stem.scene),
            "",
        )
        if body.method == "deterministic":
            stem.suno_lyrics = format_suno_lyrics(lines, character, heading)
            project = store.save(project)
            return project.model_dump(mode="json")
        if any(
            job.type in {"stem.render", "reference.design"}
            for job in queue.list_jobs("running", 50)
        ):
            raise HTTPException(
                status_code=409,
                detail="GPU is rendering; wait for the stem to finish before LLM lyrics",
            )
        try:
            stem.suno_lyrics = llm_format_suno_lyrics(
                lines, character=character, scene_heading=heading, client=llm
            )
        except (LLMParseError, LLMError) as exc:
            raise HTTPException(status_code=502, detail=f"Suno lyrics LLM failed: {exc}")
        project = store.save(project)
        return project.model_dump(mode="json")

    @app.post("/api/projects/{project_id}/stems/{stem_id}/suno-style")
    def suno_style(project_id: str, stem_id: str, body: SunoStyleBody) -> dict[str, Any]:
        """Prefill a stem's Suno title/tags.

        ``method="deterministic"`` composes tags from direction metadata
        instantly; ``method="llm"`` (default) asks the local LLM for mood-fit
        tags. Only blank fields are filled unless ``overwrite`` is true.
        """
        project = project_or_404(project_id)
        stem = next((item for item in project.stems if item.id == stem_id), None)
        if stem is None:
            raise HTTPException(status_code=404, detail="stem not found")
        if body.method not in {"deterministic", "llm"}:
            raise HTTPException(status_code=400, detail="method must be deterministic or llm")
        only = body.only if body.only is not None else ["title", "tags"]
        if unknown := [name for name in only if name not in {"title", "tags"}]:
            raise HTTPException(
                status_code=400, detail=f"unknown style fields: {unknown}"
            )
        character = project.script.characters.get(stem.speaker)
        by_id = {
            line.id: line
            for scene in project.script.scenes
            for line in scene.lines
        }
        lines = [by_id[line_id] for line_id in stem.line_ids if line_id in by_id]
        if not lines:
            raise HTTPException(status_code=400, detail="stem has no lines")
        heading = next(
            (scene.heading for scene in project.script.scenes if scene.id == stem.scene),
            "",
        )
        if body.method == "deterministic":
            suggestion = suggest_suno_style_deterministic(lines, character, heading)
        else:
            if any(
                job.type in {"stem.render", "reference.design"}
                for job in queue.list_jobs("running", 50)
            ):
                raise HTTPException(
                    status_code=409,
                    detail="GPU is rendering; wait for the stem to finish before LLM style",
                )
            try:
                suggestion = llm_suggest_suno_style(
                    lines, character=character, scene_heading=heading, client=llm
                )
            except (LLMParseError, LLMError) as exc:
                raise HTTPException(status_code=502, detail=f"Suno style LLM failed: {exc}")
        if not stem.suno_title or body.overwrite:
            if "title" in only:
                stem.suno_title = suggestion["title"] or stem.suno_title
        if not stem.suno_tags or body.overwrite:
            if "tags" in only:
                stem.suno_tags = suggestion["tags"] or stem.suno_tags
        project = store.save(project)
        return project.model_dump(mode="json")

    def active_job(project_id: str, job_type: str, stem_id: str | None = None):
        for status in ("queued", "running"):
            for existing in queue.list_jobs(status, 200):
                if existing.type != job_type:
                    continue
                if existing.payload.get("project_id") != project_id:
                    continue
                if stem_id is not None and existing.payload.get("stem_id") != stem_id:
                    continue
                return existing
        return None

    def _upload_voice_sample(
        project_id: str,
        speaker: str,
        file: UploadFile,
        name: str | None = None,
        name_form: str | None = None,
        ref_text: str | None = None,
        ref_instruction: str | None = None,
    ) -> dict[str, Any]:
        name = (name_form or name or "").strip() or None
        if ref_text is not None:
            # allow empty? keep None if blank
            ref_text = ref_text.strip() or None
        if ref_instruction is not None:
            ref_instruction = ref_instruction.strip() or None
        project = project_or_404(project_id)
        character = project.script.characters.get(speaker)
        if character is None:
            raise HTTPException(status_code=404, detail="character not found")
        if ref_text is not None:
            character.ref_text = ref_text
        if ref_instruction is not None:
            character.ref_instruction = ref_instruction
        suffix = Path(file.filename or "sample.wav").suffix.lower()
        if suffix not in {".wav", ".mp3", ".m4a", ".flac", ".ogg"}:
            raise HTTPException(status_code=400, detail="unsupported audio format")
        directory = store.project_dir(project.id) / "refs"
        directory.mkdir(parents=True, exist_ok=True)
        safe_speaker = "".join(
            ch if ch.isalnum() else "_" for ch in speaker.lower()
        ).strip("_") or "voice"

        # Determine the stored file name up front so named voices get a
        # unique, stable path (mirroring design_reference).
        if name:
            slug = slugify(name, limit=32) or safe_speaker
            existing_ids = {item.id for item in character.references}
            ref_id = slug
            counter = 2
            base_id = ref_id
            while ref_id in existing_ids:
                ref_id = f"{base_id}-{counter}"
                counter += 1
            base_name = f"{safe_speaker}-{ref_id}"
        else:
            ref_id = None
            base_name = safe_speaker

        # Read upload into a temp file first.
        temp = directory / f".upload-{uuid.uuid4().hex}{suffix}"
        with temp.open("wb") as handle:
            while chunk := file.file.read(1024 * 1024):
                handle.write(chunk)

        # Best-effort conversion to WAV; fall back to original bytes.
        converted = False
        if suffix != ".wav":
            wav_path = directory / f"{base_name}.wav"
            try:
                import subprocess

                subprocess.run(
                    ["ffmpeg", "-y", "-i", str(temp), "-ar", "24000", "-ac", "1", str(wav_path)],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                temp.unlink(missing_ok=True)
                temp = wav_path
                converted = True
            except Exception:
                converted = False

        if converted:
            destination = directory / f"{base_name}.wav"
        else:
            destination = directory / f"{base_name}{suffix}"
        if temp != destination:
            temp.replace(destination)
        elif not destination.exists():
            temp.replace(destination)

        relative = str(destination.relative_to(store.project_dir(project.id)))

        # Default voice (no name): keep legacy behaviour of setting ref_file.
        if not name:
            character.ref_file = relative
            project = store.save(project)
            return project.model_dump(mode="json")

        # Named reference voice: append to character.references.
        character.references = [
            item for item in character.references if item.id != ref_id
        ] + [
            RefVoice(
                id=ref_id,
                name=name,
                file=relative,
                ref_text=character.ref_text,
                ref_instruction=character.ref_instruction,
                f0_hz=character.f0_hz,
            )
        ]
        if character.ref_file is None:
            character.ref_file = relative
        project = store.save(project)
        return project.model_dump(mode="json")

    # Keep the original endpoint name for backwards compatibility.
    @app.post(
        "/api/projects/{project_id}/characters/{speaker}/voice-sample",
        status_code=201,
    )
    def upload_voice_sample(
        project_id: str,
        speaker: str,
        file: UploadFile = File(...),
        name_form: str | None = Form(default=None, alias="name"),
        name_query: str | None = Query(default=None, alias="name"),
        ref_text: str | None = Form(default=None),
        ref_instruction: str | None = Form(default=None),
    ) -> dict[str, Any]:
        return _upload_voice_sample(
            project_id,
            speaker,
            file,
            name=name_form or name_query,
            ref_text=ref_text,
            ref_instruction=ref_instruction,
        )

    # Additional convenience endpoints for uploading named reference voices.
    for _path in (
        "/api/projects/{project_id}/characters/{speaker}/reference/upload",
        "/api/projects/{project_id}/characters/{speaker}/reference-upload",
        "/api/projects/{project_id}/characters/{speaker}/upload-voice",
        "/api/projects/{project_id}/characters/{speaker}/upload-voice-sample",
        "/api/projects/{project_id}/characters/{speaker}/upload_reference",
        "/api/projects/{project_id}/characters/{speaker}/references/upload",
    ):
        def _make(path: str):
            @app.post(path, status_code=201)
            def _upload(
                project_id: str,
                speaker: str,
                file: UploadFile = File(...),
                name: str | None = Form(default=None),
                name_query: str | None = Query(default=None, alias="name"),
                ref_text: str | None = Form(default=None),
                ref_instruction: str | None = Form(default=None),
            ) -> dict[str, Any]:
                return _upload_voice_sample(
                    project_id,
                    speaker,
                    file,
                    name=name_query or name,
                    ref_text=ref_text,
                    ref_instruction=ref_instruction,
                )

            return _upload

        _make(_path)

    @app.post("/api/projects/{project_id}/stems/{stem_id}/extract", status_code=202)
    def extract_project_stem(
        project_id: str, stem_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        project = project_or_404(project_id)
        stem = next((item for item in project.stems if item.id == stem_id), None)
        if stem is None:
            raise HTTPException(status_code=404, detail="stem not found")
        if not stem.takes:
            raise HTTPException(status_code=409, detail="render the stem first")
        take_id = body.get("take_id") or (
            next((t.id for t in stem.takes if t.selected), None)
            or stem.takes[0].id
        )
        if not any(t.id == take_id for t in stem.takes):
            raise HTTPException(status_code=404, detail="take not found")
        method = str(body.get("method") or "demucs").strip().lower()
        if method not in {"demucs", "suno"}:
            raise HTTPException(status_code=400, detail="method must be demucs or suno")
        existing = active_job(project_id, "stem.extract", stem_id)
        if existing is not None:
            return existing.model_dump(mode="json")
        job = queue.enqueue(
            "stem.extract",
            {
                "project_id": project_id,
                "stem_id": stem_id,
                "take_id": take_id,
                "method": method,
                "align": bool(body.get("align", True)),
            },
            priority=10,
        )
        return job.model_dump(mode="json")

    @app.post("/api/projects/{project_id}/stems/{stem_id}/import-suno", status_code=202)
    def import_suno_take(
        project_id: str, stem_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        """Attach an existing Suno library clip as a take (recover generations).

        Downloads the clip, converts to wav and stores it as the selected take
        with estimated line timing — the same shape as a fresh Suno render.
        """
        project = project_or_404(project_id)
        stem = next((item for item in project.stems if item.id == stem_id), None)
        if stem is None:
            raise HTTPException(status_code=404, detail="stem not found")
        clip_id = str(body.get("clip_id") or "").strip()
        if not clip_id:
            raise HTTPException(status_code=400, detail="clip_id is required")
        existing = active_job(project_id, "stem.import_suno", stem_id)
        if existing is not None:
            return existing.model_dump(mode="json")
        job = queue.enqueue(
            "stem.import_suno",
            {
                "project_id": project_id,
                "stem_id": stem_id,
                "clip_id": clip_id,
            },
            priority=10,
        )
        return job.model_dump(mode="json")

    @app.patch("/api/projects/{project_id}/stems/{stem_id}/alignment")
    def update_alignment(
        request: Request, project_id: str, stem_id: str, body: AlignmentPatch
    ) -> dict[str, Any]:
        project = project_or_404(project_id)
        stem = next((item for item in project.stems if item.id == stem_id), None)
        if stem is None:
            raise HTTPException(status_code=404, detail="stem not found")
        selected = next((take for take in stem.takes if take.selected), None)
        takes_by_id = {take.id: take for take in stem.takes}
        if selected is None and any(cut.take_id is None for cut in body.cuts):
            raise HTTPException(status_code=409, detail="stem has no selected take")

        directory = store.project_dir(project_id)

        from .audio import read_mono, slice_mono, write_mono
        from .cuts import safe_filename, validate_cut
        from .models import AlignedLine, AlignmentResult

        from .cuts import segment_filename

        # Ownership validation before touching any audio. Multiple cuts per
        # line (segments) are allowed; segment ids must be distinct.
        seen_ids: set[str] = set()
        for cut in body.cuts:
            if cut.line_id not in stem.line_ids:
                raise HTTPException(
                    status_code=400,
                    detail=f"line {cut.line_id} does not belong to stem {stem_id}",
                )

        alignment = project.alignments.get(stem_id)
        if alignment is None:
            alignment = AlignmentResult(stem_id=stem_id, lines=[], aligner="manual")
        by_id = {line.line_id: line for line in alignment.lines}

        take_audio: dict[str, tuple[Any, int, float]] = {}

        def _take_for(cut: CutUpdate):
            take = takes_by_id.get(cut.take_id) if cut.take_id else selected
            if take is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"take {cut.take_id} not found on stem",
                )
            return take

        def _audio_for(take):
            cached = take_audio.get(take.id)
            if cached is not None:
                return cached
            stem_path = directory / take.file
            if not stem_path.exists():
                raise HTTPException(
                    status_code=404,
                    detail=f"take {take.id} audio missing",
                )
            mono, sr = read_mono(stem_path)
            cached = (mono, sr, len(mono) / sr)
            take_audio[take.id] = cached
            return cached

        # Validate everything before writing anything (atomic batches).
        staged: list[tuple[CutUpdate, Any, int, Any]] = []
        for cut in body.cuts:
            take = _take_for(cut)
            mono, sr, duration = _audio_for(take)
            try:
                validate_cut(cut.line_id, take, cut.start_s, cut.end_s, duration)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            segment = slice_mono(mono, sr, cut.start_s, cut.end_s)
            if segment.size == 0:
                raise HTTPException(
                    status_code=400, detail=f"empty cut for {cut.line_id}"
                )
            staged.append((cut, take, sr, segment))

        out_dir = directory / "lines"
        out_dir.mkdir(parents=True, exist_ok=True)
        # Rebuild the alignment's segment list from the submitted cuts so
        # deleted segments don't linger.
        new_lines: list[AlignedLine] = []
        for cut, take, sr, segment in staged:
            seg_id = (cut.id or "").strip() or f"{cut.line_id}"
            # Ensure uniqueness within this batch.
            existing_ids = {e.id for e in new_lines}
            if seg_id in existing_ids:
                i = 2
                while f"{seg_id}__{i}" in existing_ids:
                    i += 1
                seg_id = f"{seg_id}__{i}"
            fname = safe_filename(seg_id)
            write_mono(out_dir / f"{fname}.wav", segment, sr)
            pos = cut.position_s
            if pos is None:
                # Seed from cut durations cumulatively is done by caller;
                # default 0 here.
                pos = 0.0
            new_lines.append(
                AlignedLine(
                    id=seg_id,
                    line_id=cut.line_id,
                    take_id=take.id,
                    start_s=round(cut.start_s, 3),
                    end_s=round(cut.end_s, 3),
                    position_s=round(float(pos), 3),
                    confidence=1.0,
                    fallback=False,
                )
            )
        alignment.lines = new_lines
        alignment.aligner = "manual"
        project.alignments[stem_id] = alignment
        project.mix_stale = True
        project = store.save(project)

        if body.rebuild_mix:
            from .mixer import build_mix

            sfx = request.app.state.sfx
            mix_result = build_mix(project, directory, sfx.get)
            project.mix = mix_result
            project.mix_stale = False
            project = store.save(project)

        return {
            "project": project.model_dump(mode="json"),
            "alignment": alignment.model_dump(mode="json"),
        }

    @app.patch("/api/projects/{project_id}/alignments")
    def update_alignments_batch(
        request: Request, project_id: str, body: BatchAlignmentPatch
    ) -> dict[str, Any]:
        """Save cuts for several stems, rebuilding the mix once.

        The Cut board's Save-all uses this so N dirty stems cost one mix
        rebuild instead of N. Validation is per-stem (same rules as the
        single-stem endpoint); if any stem fails nothing is written.
        """
        from .audio import read_mono, slice_mono, write_mono
        from .cuts import safe_filename, validate_cut
        from .models import AlignedLine, AlignmentResult

        project = project_or_404(project_id)
        directory = store.project_dir(project_id)
        stems_by_id = {stem.id: stem for stem in project.stems}

        # Validate all stems first for atomicity.
        plans: dict[str, Any] = {}
        for stem_id, cuts in body.stems.items():
            stem = stems_by_id.get(stem_id)
            if stem is None:
                raise HTTPException(status_code=404, detail=f"stem {stem_id} not found")
            selected = next((take for take in stem.takes if take.selected), None)
            takes_by_id = {take.id: take for take in stem.takes}
            if selected is None and any(cut.take_id is None for cut in cuts):
                raise HTTPException(
                    status_code=409, detail=f"stem {stem_id} has no selected take"
                )
            seen: set[str] = set()
            for cut in cuts:
                if cut.line_id in seen:
                    raise HTTPException(
                        status_code=400, detail=f"duplicate cut for {cut.line_id}"
                    )
                seen.add(cut.line_id)
                if cut.line_id not in stem.line_ids:
                    raise HTTPException(
                        status_code=400,
                        detail=f"line {cut.line_id} does not belong to stem {stem_id}",
                    )
            take_audio: dict[str, tuple[Any, int, float]] = {}

            def _take_for(cut: CutUpdate):
                take = takes_by_id.get(cut.take_id) if cut.take_id else selected
                if take is None:
                    raise HTTPException(
                        status_code=404,
                        detail=f"take {cut.take_id} not found on stem",
                    )
                return take

            def _audio_for(take):
                cached = take_audio.get(take.id)
                if cached is not None:
                    return cached
                stem_path = directory / take.file
                if not stem_path.exists():
                    raise HTTPException(
                        status_code=404, detail=f"take {take.id} audio missing"
                    )
                mono, sr = read_mono(stem_path)
                cached = (mono, sr, len(mono) / sr)
                take_audio[take.id] = cached
                return cached

            staged: list[tuple[CutUpdate, Any, int, Any]] = []
            for cut in cuts:
                take = _take_for(cut)
                mono, sr, duration = _audio_for(take)
                try:
                    validate_cut(cut.line_id, take, cut.start_s, cut.end_s, duration)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
                segment = slice_mono(mono, sr, cut.start_s, cut.end_s)
                if segment.size == 0:
                    raise HTTPException(
                        status_code=400, detail=f"empty cut for {cut.line_id}"
                    )
                staged.append((cut, take, sr, segment))
            plans[stem_id] = staged

        out_dir = directory / "lines"
        out_dir.mkdir(parents=True, exist_ok=True)
        alignments: dict[str, Any] = {}
        for stem_id, staged in plans.items():
            alignment = project.alignments.get(stem_id)
            if alignment is None:
                alignment = AlignmentResult(stem_id=stem_id, lines=[], aligner="manual")
            new_lines: list[AlignedLine] = []
            for cut, take, sr, segment in staged:
                seg_id = (cut.id or "").strip() or f"{cut.line_id}"
                existing_ids = {e.id for e in new_lines}
                if seg_id in existing_ids:
                    i = 2
                    while f"{seg_id}__{i}" in existing_ids:
                        i += 1
                    seg_id = f"{seg_id}__{i}"
                write_mono(out_dir / f"{safe_filename(seg_id)}.wav", segment, sr)
                pos = cut.position_s if cut.position_s is not None else 0.0
                new_lines.append(
                    AlignedLine(
                        id=seg_id,
                        line_id=cut.line_id,
                        take_id=take.id,
                        start_s=round(cut.start_s, 3),
                        end_s=round(cut.end_s, 3),
                        position_s=round(float(pos), 3),
                        confidence=1.0,
                        fallback=False,
                    )
                )
            alignment.lines = new_lines
            alignment.aligner = "manual"
            project.alignments[stem_id] = alignment
            alignments[stem_id] = alignment
        project.mix_stale = True
        project = store.save(project)

        if body.rebuild_mix:
            from .mixer import build_mix

            sfx = request.app.state.sfx
            mix_result = build_mix(project, directory, sfx.get)
            project.mix = mix_result
            project.mix_stale = False
            project = store.save(project)

        return {
            "project": project.model_dump(mode="json"),
            "alignments": {
                stem_id: alignment.model_dump(mode="json")
                for stem_id, alignment in alignments.items()
            },
        }

    @app.post("/api/projects/{project_id}/characters/{speaker}/reference", status_code=202)
    def design_character_reference(
        project_id: str, speaker: str, body: ReferenceBody
    ) -> dict[str, Any]:
        project = project_or_404(project_id)
        character = project.script.characters.get(speaker)
        if character is None:
            raise HTTPException(status_code=404, detail="character not found")
        existing = active_job(project_id, "reference.design")
        if existing is not None:
            return existing.model_dump(mode="json")
        job = queue.enqueue(
            "reference.design",
            {
                "project_id": project_id,
                "speaker": speaker,
                "ref_text": body.ref_text or character.ref_text,
                "ref_instruction": body.ref_instruction or character.ref_instruction,
                "cfg_scale": body.cfg_scale,
                "seed": body.seed,
                "name": body.name,
            },
            priority=5,
        )
        return job.model_dump(mode="json")

    @app.post("/api/projects/{project_id}/stems/{stem_id}/render", status_code=202)
    def render_project_stem(
        project_id: str, stem_id: str, body: RenderBody
    ) -> dict[str, Any]:
        project = project_or_404(project_id)
        stem = next((item for item in project.stems if item.id == stem_id), None)
        if stem is None:
            raise HTTPException(status_code=404, detail="stem not found")
        character = project.script.characters.get(stem.speaker)
        if character is None:
            raise HTTPException(status_code=404, detail="character not found")
        if body.engine is not None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "per-stem engine selection was removed; "
                    "set Project.default_engine instead"
                ),
            )
        engine_name = project.default_engine or (
            "breeze" if project.language == "en" else "chatterbox"
        )
        if engine_name in {"breeze", "breeze-de"}:
            voice_ok = bool(character.ref_file)
            if not voice_ok and (body.ref_voice_id or stem.ref_voice_id):
                voice_ok = any(
                    item.id == (body.ref_voice_id or stem.ref_voice_id)
                    for item in character.references
                )
            if not voice_ok:
                raise HTTPException(
                    status_code=409,
                    detail=f"design a reference voice for {stem.speaker} first",
                )
        existing = active_job(project_id, "stem.render", stem_id)
        if existing is not None:
            return existing.model_dump(mode="json")
        job = queue.enqueue(
            "stem.render",
            {
                "project_id": project_id,
                "stem_id": stem_id,
                "candidates": body.candidates,
                "cfg_scale": body.cfg_scale,
                "seed": body.seed,
                "engine": engine_name,
                "suno_lyrics": body.suno_lyrics,
                "ref_voice_id": body.ref_voice_id,
                "suno_weirdness": body.suno_weirdness,
                "suno_style_weight": body.suno_style_weight,
                "suno_audio_weight": body.suno_audio_weight,
                "suno_mv": body.suno_mv,
                "suno_vocal_gender": body.suno_vocal_gender,
            },
            priority=10,
        )
        return job.model_dump(mode="json")

    @app.patch("/api/projects/{project_id}/stems/{stem_id}")
    def update_stem(
        project_id: str, stem_id: str, body: StemUpdate
    ) -> dict[str, Any]:
        project = project_or_404(project_id)
        stem = next((item for item in project.stems if item.id == stem_id), None)
        if stem is None:
            raise HTTPException(status_code=404, detail="stem not found")
        if body.engine is not None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "per-stem engine selection was removed; "
                    "set Project.default_engine instead"
                ),
            )
        if body.cfg_scale is not None:
            stem.cfg_scale = body.cfg_scale
        if body.seed is not None:
            stem.seed = body.seed
        if body.text is not None:
            new_text = body.text.strip()
            if not new_text:
                raise HTTPException(status_code=400, detail="stem text must not be blank")
            if new_text != stem.text:
                stem.text = new_text
                # Source text changed: invalidate rendered takes and cuts.
                stem.takes = []
                project.alignments.pop(stem.id, None)
                project.mix_stale = True
        if body.suno_lyrics is not None:
            stem.suno_lyrics = body.suno_lyrics.strip() or None
        if body.suno_title is not None:
            stem.suno_title = body.suno_title.strip() or None
        if body.suno_tags is not None:
            stem.suno_tags = body.suno_tags.strip() or None
        if body.suno_negative_tags is not None:
            stem.suno_negative_tags = body.suno_negative_tags.strip() or None
        if body.suno_weirdness is not None:
            stem.suno_weirdness = body.suno_weirdness
        if body.suno_style_weight is not None:
            stem.suno_style_weight = body.suno_style_weight
        if body.suno_audio_weight is not None:
            stem.suno_audio_weight = body.suno_audio_weight
        if body.suno_mv is not None:
            stem.suno_mv = body.suno_mv.strip() or None
        if body.suno_vocal_gender is not None:
            gender = body.suno_vocal_gender.strip()
            if gender and gender not in {"m", "f"}:
                raise HTTPException(
                    status_code=400,
                    detail="suno_vocal_gender must be m or f",
                )
            stem.suno_vocal_gender = gender or None
        if body.ref_voice_id is not None:
            stem.ref_voice_id = body.ref_voice_id.strip() or None
        project = store.save(project)
        return project.model_dump(mode="json")

    @app.delete("/api/projects/{project_id}/stems/{stem_id}/takes/{take_id}")
    def delete_take(project_id: str, stem_id: str, take_id: str) -> dict[str, Any]:
        project = project_or_404(project_id)
        stem = next((item for item in project.stems if item.id == stem_id), None)
        if stem is None:
            raise HTTPException(status_code=404, detail="stem not found")
        take = next((item for item in stem.takes if item.id == take_id), None)
        if take is None:
            raise HTTPException(status_code=404, detail="take not found")
        was_selected = take.selected
        stem.takes = [item for item in stem.takes if item.id != take_id]
        if was_selected and stem.takes:
            stem.takes[0].selected = True
        # Also drop alignment if it references this take
        alignment = project.alignments.get(stem_id)
        if alignment is not None:
            alignment.lines = [
                line for line in alignment.lines if line.take_id != take_id
            ]
            if not alignment.lines:
                project.alignments.pop(stem_id, None)
            else:
                project.alignments[stem_id] = alignment
        project = store.save(project)
        return project.model_dump(mode="json")

    @app.patch("/api/projects/{project_id}/stems/{stem_id}/take")
    def select_take(project_id: str, stem_id: str, body: TakeSelect) -> dict[str, Any]:
        project = project_or_404(project_id)
        stem = next((item for item in project.stems if item.id == stem_id), None)
        if stem is None:
            raise HTTPException(status_code=404, detail="stem not found")
        if not any(take.id == body.take_id for take in stem.takes):
            raise HTTPException(status_code=404, detail="take not found")
        previous = next((take for take in stem.takes if take.selected), None)
        alignment = project.alignments.get(stem_id)
        if previous is not None and alignment is not None:
            from .cuts import freeze_legacy_cuts

            freeze_legacy_cuts(alignment, previous)
        for take in stem.takes:
            take.selected = take.id == body.take_id

        # Don't re-align when switching takes: only align once per take and
        # store it. Realign only when there is no alignment yet for this stem.
        safe_realign = alignment is None or not alignment.lines
        project = store.save(project)
        job = (
            queue.enqueue(
                "stem.realign",
                {"project_id": project_id, "stem_id": stem_id},
                priority=5,
            )
            if safe_realign
            else None
        )
        return {
            "project": project.model_dump(mode="json"),
            "job": job.model_dump(mode="json") if job else None,
        }

    @app.get("/api/suno/status")
    def suno_status() -> dict[str, Any]:
        try:
            return SunoBridge().health()
        except SunoError as exc:
            return {"ok": False, "error": str(exc)}

    @app.post("/api/suno/captcha")
    def suno_captcha() -> dict[str, Any]:
        """Force the extension to mint a Turnstile token now.

        Lets the UI resolve a failed verification without waiting for another
        generation attempt. The token stays inside the bridge.
        """
        try:
            return SunoBridge().request_captcha()
        except SunoError as exc:
            raise HTTPException(status_code=502, detail=str(exc))

    @app.get("/api/suno/personas")
    def suno_personas() -> dict[str, Any]:
        """Own + followed Suno voice/style personas for the voice picker."""
        try:
            return SunoBridge().list_personas()
        except SunoError as exc:
            raise HTTPException(status_code=502, detail=str(exc))

    @app.get("/api/suno/personas/{persona_id}")
    def suno_persona(persona_id: str) -> dict[str, Any]:
        """Persona detail incl. root clip for voice preview."""
        try:
            return SunoBridge().get_persona(persona_id)
        except SunoError as exc:
            raise HTTPException(status_code=502, detail=str(exc))

    @app.get("/api/suno/library")
    def suno_library(limit: int = Query(default=500, ge=1, le=500)) -> dict[str, Any]:
        """Newest own Suno library clips for the import-from-Suno picker."""
        try:
            return SunoBridge().list_library(limit)
        except SunoError as exc:
            raise HTTPException(status_code=502, detail=str(exc))

    @app.get("/api/suno/preview/{clip_id}")
    def suno_preview(clip_id: str):
        """Streamable mp3 preview of a clip (transcoded on the fly, nothing saved).

        Persona catalog streams are AES-encrypted m4a-opus that Firefox cannot
        demux; the bridge decrypts + transcodes to mp3 for preview playback.
        """
        url = f"{SUNO_BRIDGE_URL.rstrip('/')}/preview/{clip_id}"
        try:
            upstream = httpx.stream("GET", url, timeout=180)
            response = upstream.__enter__()
            response.raise_for_status()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail=f"Suno preview failed: {exc}"
            )
        else:
            def generate():
                try:
                    for chunk in response.iter_bytes(65536):
                        yield chunk
                finally:
                    upstream.__exit__(None, None, None)

            return StreamingResponse(
                generate(),
                media_type="audio/mpeg",
                headers={"Cache-Control": "no-store"},
            )

    @app.post("/api/projects/{project_id}/suno/generate", status_code=202)
    def generate_suno(project_id: str, body: SunoGenerate) -> dict[str, Any]:
        project = project_or_404(project_id)
        if not body.prompt.strip() and not (body.lyrics or "").strip():
            raise HTTPException(status_code=400, detail="prompt or lyrics is required")
        existing = active_job(project_id, "suno.generate")
        if existing is not None:
            return existing.model_dump(mode="json")
        job = queue.enqueue(
            "suno.generate",
            {
                "project_id": project.id,
                "kind": body.kind,
                "prompt": body.prompt,
                "title": body.title or project.title,
                "tags": body.tags,
                "duration": body.duration,
                "lyrics": body.lyrics,
                "vocal_gender": body.vocal_gender,
            },
            priority=8,
        )
        return job.model_dump(mode="json")

    @app.post("/api/projects/{project_id}/mix", status_code=202)
    def render_mix(project_id: str) -> dict[str, Any]:
        project = project_or_404(project_id)
        existing = active_job(project_id, "mix.render")
        if existing is not None:
            return existing.model_dump(mode="json")
        job = queue.enqueue(
            "mix.render",
            {"project_id": project.id},
            priority=20,
        )
        return job.model_dump(mode="json")

    @app.get("/api/projects/{project_id}/files/{file_path:path}")
    def project_file(project_id: str, file_path: str) -> FileResponse:
        project_or_404(project_id)
        directory = store.project_dir(project_id).resolve()
        target = (directory / file_path).resolve()
        try:
            target.relative_to(directory)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="file not found") from exc
        if not target.is_file():
            raise HTTPException(status_code=404, detail="file not found")
        media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return FileResponse(target, media_type=media_type, filename=target.name)

    @app.get("/api/projects/{project_id}/jobs")
    def project_jobs(project_id: str, limit: int = Query(default=50, ge=1, le=200)):
        project_or_404(project_id)
        return [
            job.model_dump(mode="json")
            for job in queue.list_jobs(None, limit)
            if job.payload.get("project_id") == project_id
        ]

    @app.put("/api/projects/{project_id}/script")
    def update_script(project_id: str, body: ScriptUpdate) -> dict[str, Any]:
        project = project_or_404(project_id)
        if body.script.title:
            project.title = body.script.title
        project = store.reparse(
            project,
            method=body.report.method if body.report else "deterministic",
            script=body.script,
            report=body.report,
        )
        return project.model_dump(mode="json")

    @app.get("/api/sfx")
    def search_sfx(q: str = "", limit: int = Query(default=40, ge=1, le=200)):
        return [item.model_dump(mode="json") for item in sfx.search(q, limit)]

    @app.post("/api/sfx/scan")
    def scan_sfx() -> dict[str, Any]:
        return {"indexed": sfx.scan()}

    @app.post("/api/sfx/upload", status_code=201)
    def upload_sfx(file: UploadFile = File(...)) -> dict[str, Any]:
        upload_dir = Path(PROJECTS_DIR).parent / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        safe_name = Path(file.filename or "upload.wav").name
        temp = upload_dir / f"{uuid.uuid4().hex[:8]}-{safe_name}"
        with temp.open("wb") as handle:
            while chunk := file.file.read(1024 * 1024):
                handle.write(chunk)
        try:
            item = sfx.add_upload(temp, safe_name)
        finally:
            temp.unlink(missing_ok=True)
        return item.model_dump(mode="json")

    @app.get("/api/sfx/{sfx_id}/audio")
    def sfx_audio(sfx_id: str) -> FileResponse:
        path = sfx.audio_path(sfx_id)
        if path is None:
            raise HTTPException(status_code=404, detail="sfx not found")
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return FileResponse(path, media_type=media_type, filename=path.name)

    @app.post("/api/projects/{project_id}/cues", status_code=201)
    def create_cue(project_id: str, body: CueCreate) -> dict[str, Any]:
        project = project_or_404(project_id)
        line_ids = {line.id for line in project.script.iter_lines()}
        if body.anchor_line_id not in line_ids:
            raise HTTPException(status_code=400, detail="anchor line not found")
        if sfx.get(body.sfx_id) is None:
            raise HTTPException(status_code=404, detail="sfx not found")
        try:
            cue = Cue(
                id=uuid.uuid4().hex[:12],
                kind=body.kind,  # type: ignore[arg-type]
                sfx_id=body.sfx_id,
                name=body.name,
                anchor_line_id=body.anchor_line_id,
                offset_s=body.offset_s,
                gain_db=body.gain_db,
                fade_in_s=body.fade_in_s,
                fade_out_s=body.fade_out_s,
                loop=body.loop,
                duck=body.duck,
                filter=body.filter,  # type: ignore[arg-type]
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        project.cues.append(cue)
        project = store.save(project)
        return project.model_dump(mode="json")

    @app.delete("/api/projects/{project_id}/cues/{cue_id}", status_code=204)
    def delete_cue(project_id: str, cue_id: str) -> None:
        project = project_or_404(project_id)
        before = len(project.cues)
        project.cues = [cue for cue in project.cues if cue.id != cue_id]
        if len(project.cues) == before:
            raise HTTPException(status_code=404, detail="cue not found")
        store.save(project)

    @app.patch("/api/projects/{project_id}/characters/{speaker}")
    def update_character(
        project_id: str, speaker: str, body: CharacterUpdate
    ) -> dict[str, Any]:
        project = project_or_404(project_id)
        character = project.script.characters.get(speaker)
        if character is None:
            raise HTTPException(status_code=404, detail="character not found")
        if body.name is not None and body.name.strip():
            character.name = body.name.strip()
        if body.description is not None:
            character.description = body.description
        if body.ref_text is not None:
            character.ref_text = body.ref_text
        if body.ref_instruction is not None:
            character.ref_instruction = body.ref_instruction
        if body.baseline_instruct is not None:
            character.baseline_instruct = body.baseline_instruct
        if body.suno_persona_id is not None:
            character.suno_persona_id = body.suno_persona_id.strip() or None
            if character.suno_persona_id is None:
                character.suno_persona_model = None
        if body.suno_persona_model is not None:
            model = body.suno_persona_model.strip()
            if model and model not in {"voice_persona", "style_persona"}:
                raise HTTPException(
                    status_code=400,
                    detail="suno_persona_model must be voice_persona or style_persona",
                )
            character.suno_persona_model = model or None
        if body.suno_mv is not None and body.suno_mv.strip():
            character.suno_mv = body.suno_mv.strip()
        project = store.save(project)
        return project.model_dump(mode="json")

    @app.get("/api/jobs")
    def list_jobs(
        status: JobStatus | None = None,
        limit: int = Query(default=50, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        return [job.model_dump(mode="json") for job in queue.list_jobs(status, limit)]

    @app.post("/api/jobs", status_code=201)
    def create_job(body: JobCreate) -> dict[str, Any]:
        job = queue.enqueue(
            body.type,
            body.payload,
            priority=body.priority,
            max_attempts=body.max_attempts,
        )
        return job.model_dump(mode="json")

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        job = queue.find(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return job.model_dump(mode="json")

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict[str, Any]:
        try:
            job = queue.cancel(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="job not found") from exc
        return job.model_dump(mode="json")

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str, request: Request) -> StreamingResponse:
        if queue.find(job_id) is None:
            raise HTTPException(status_code=404, detail="job not found")

        async def stream() -> AsyncIterator[str]:
            last = ""
            while True:
                if await request.is_disconnected():
                    break
                job = queue.find(job_id)
                if job is None:
                    yield "event: error\ndata: {\"detail\":\"job not found\"}\n\n"
                    break
                payload = json.dumps(job.model_dump(mode="json"), ensure_ascii=False)
                if payload != last:
                    yield f"event: job\ndata: {payload}\n\n"
                    last = payload
                if job.terminal:
                    break
                await asyncio.sleep(0.35)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    if WEB_DIST.exists():
        assets = WEB_DIST / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa_fallback(full_path: str) -> FileResponse:
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="not found")
            candidate = WEB_DIST / full_path
            if candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(WEB_DIST / "index.html")

    return app
