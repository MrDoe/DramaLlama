from __future__ import annotations

import asyncio
import json
import mimetypes
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .config import DB_PATH, PROJECTS_DIR, WEB_DIST
from .engines import all_engine_info
from .jobs import JobQueue
from .llm import LLMError, LLMParseError, OllamaClient, llm_parse_script
from .models import (
    Cue,
    JobStatus,
    Language,
    ParseReport,
    ParseWarning,
    Project,
    ProjectSettings,
    Script,
)
from .sfx import SfxLibrary
from .stems import build_stems
from .store import ProjectStore
from .suno import SunoBridge, SunoError


class JobCreate(BaseModel):
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    priority: int = 0
    max_attempts: int = 1


class ProjectCreate(BaseModel):
    title: str = ""
    language: Language = "en"
    script_text: str = ""


class ProjectUpdate(BaseModel):
    title: str | None = None
    language: Language | None = None
    raw_script: str | None = None
    settings: ProjectSettings | None = None


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


class RenderBody(BaseModel):
    candidates: int = Field(default=2, ge=1, le=3)
    cfg_scale: float | None = None
    seed: int | None = None
    engine: str | None = None


class StemUpdate(BaseModel):
    engine: str | None = None
    cfg_scale: float | None = None
    seed: int | None = None


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


class CharacterUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    ref_text: str | None = None
    ref_instruction: str | None = None
    baseline_instruct: str | None = None


def create_app(
    db_path: str | Path | None = None,
    projects_dir: str | Path | None = None,
    llm_client: OllamaClient | None = None,
    sfx_library: SfxLibrary | None = None,
) -> FastAPI:
    queue = JobQueue(db_path or DB_PATH)
    store = ProjectStore(Path(projects_dir) if projects_dir else PROJECTS_DIR)
    llm = llm_client or OllamaClient()
    sfx = sfx_library or SfxLibrary()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        queue.close()

    app = FastAPI(title="Drama Studio", version=__version__, lifespan=lifespan)
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
                {**info.__dict__, "languages": sorted(info.languages)}
                for info in all_engine_info()
            ],
        }

    @app.get("/api/engines")
    def engines() -> list[dict[str, Any]]:
        return [
            {
                **info.__dict__,
                "languages": sorted(info.languages),
            }
            for info in all_engine_info()
        ]

    def project_or_404(project_id: str) -> Project:
        project = store.find(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        return project

    @app.get("/api/projects")
    def list_projects() -> list[dict[str, Any]]:
        return [summary.model_dump(mode="json") for summary in store.list()]

    @app.post("/api/projects", status_code=201)
    def create_project(body: ProjectCreate) -> dict[str, Any]:
        project = store.create(body.title, body.language, body.script_text)
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

    @app.post("/api/projects/{project_id}/stems")
    def build_project_stems(project_id: str) -> dict[str, Any]:
        project = project_or_404(project_id)
        project.stems = build_stems(project)
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

    @app.post(
        "/api/projects/{project_id}/characters/{speaker}/voice-sample",
        status_code=201,
    )
    def upload_voice_sample(
        project_id: str, speaker: str, file: UploadFile = File(...)
    ) -> dict[str, Any]:
        project = project_or_404(project_id)
        character = project.script.characters.get(speaker)
        if character is None:
            raise HTTPException(status_code=404, detail="character not found")
        suffix = Path(file.filename or "sample.wav").suffix.lower()
        if suffix not in {".wav", ".mp3", ".m4a", ".flac", ".ogg"}:
            raise HTTPException(status_code=400, detail="unsupported audio format")
        safe = "".join(
            ch if ch.isalnum() else "_" for ch in speaker.lower()
        ).strip("_") or "voice"
        directory = store.project_dir(project.id) / "refs"
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"{safe}{suffix}"
        with destination.open("wb") as handle:
            while chunk := file.file.read(1024 * 1024):
                handle.write(chunk)
        character.ref_file = str(destination.relative_to(store.project_dir(project.id)))
        project = store.save(project)
        return project.model_dump(mode="json")

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
        engine_name = body.engine or stem.engine or (
            "breeze" if project.language == "en" else "chatterbox"
        )
        if engine_name == "breeze" and not character.ref_file:
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
                "engine": body.engine or stem.engine,
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
            try:
                info = next(
                    item for item in all_engine_info() if item.name == body.engine
                )
            except StopIteration as exc:
                raise HTTPException(status_code=400, detail="unknown engine") from exc
            if project.language not in info.languages:
                raise HTTPException(
                    status_code=400,
                    detail=f"{body.engine} does not support {project.language}",
                )
            stem.engine = body.engine
        if body.cfg_scale is not None:
            stem.cfg_scale = body.cfg_scale
        if body.seed is not None:
            stem.seed = body.seed
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
        for take in stem.takes:
            take.selected = take.id == body.take_id
        project = store.save(project)
        job = queue.enqueue(
            "stem.realign",
            {"project_id": project_id, "stem_id": stem_id},
            priority=5,
        )
        return {"project": project.model_dump(mode="json"), "job": job.model_dump(mode="json")}

    @app.get("/api/suno/status")
    def suno_status() -> dict[str, Any]:
        try:
            return SunoBridge().health()
        except SunoError as exc:
            return {"ok": False, "error": str(exc)}

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
