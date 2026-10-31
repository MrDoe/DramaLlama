from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

Language = Literal["en", "de"]
EngineName = Literal["breeze", "breeze-de", "chatterbox", "fake", "suno"]
JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
VoiceMode = Literal["reference", "preset"]


def utcnow() -> datetime:
    return datetime.now(UTC)


class RefVoice(BaseModel):
    """One named reference voice of a character.

    The legacy single-reference fields on Character (ref_file/ref_text/...)
    stay the default voice; entries here are additional takes of the same
    speaker selectable per stem.
    """

    id: str
    name: str
    file: str
    ref_text: str | None = None
    ref_instruction: str | None = None
    f0_hz: float | None = None


class Character(BaseModel):
    id: str
    name: str
    description: str = ""
    ref_text: str | None = None
    ref_instruction: str | None = None
    ref_file: str | None = None
    suno_persona_id: str | None = None
    suno_persona_model: str | None = None
    suno_mv: str = "chirp-hawk"
    references: list[RefVoice] = Field(default_factory=list)
    baseline_instruct: str = ""
    locked: bool = False
    f0_hz: float | None = None


class Line(BaseModel):
    id: str
    scene: int
    speaker: str
    text: str
    direction: str = ""
    parenthetical: str | None = None
    sfx: list[str] = Field(default_factory=list)
    emotion: str = ""
    intensity: int = Field(default=3, ge=1, le=5)
    source_index: int | None = None

    @field_validator("text")
    @classmethod
    def text_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("line text must not be blank")
        return value


class Scene(BaseModel):
    id: int
    heading: str = ""
    setting: str = ""
    stage_directions: list[str] = Field(default_factory=list)
    lines: list[Line] = Field(default_factory=list)


class Script(BaseModel):
    title: str
    language: Language = "en"
    characters: dict[str, Character] = Field(default_factory=dict)
    scenes: list[Scene] = Field(default_factory=list)

    def iter_lines(self) -> list[Line]:
        return [line for scene in self.scenes for line in scene.lines]


class ParseWarning(BaseModel):
    source_line: int
    message: str
    text: str = ""


class ParseReport(BaseModel):
    method: Literal["deterministic", "llm"] = "deterministic"
    source_lines: int = 0
    scene_headings: int = 0
    dialogue_lines: int = 0
    stage_directions: int = 0
    ignored_lines: int = 0
    unknown_lines: int = 0
    coverage: float = 0.0
    runtime_estimate_s: float = 0.0
    warnings: list[ParseWarning] = Field(default_factory=list)


class ParseResult(BaseModel):
    script: Script
    report: ParseReport


class Cue(BaseModel):
    id: str
    kind: Literal["bed", "one_shot", "music"] = "one_shot"
    sfx_id: str
    name: str
    anchor_line_id: str
    offset_s: float = 0.0
    gain_db: float = -12.0
    fade_in_s: float = 0.0
    fade_out_s: float = 0.0
    loop: bool = False
    duck: bool = True
    filter: Literal["none", "telephone", "radio"] = "none"


class SunoTrack(BaseModel):
    clip_id: str
    title: str
    file: str
    sfx_id: str
    duration_s: float | None = None
    prompt: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class MixResult(BaseModel):
    file: str
    mp3: str | None = None
    duration_s: float
    loudness_target: str
    line_count: int
    cue_count: int
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)


class ProjectSettings(BaseModel):
    pause_ms: int = 500
    scene_gap_ms: int = 1200
    loudness_target: Literal["podcast", "broadcast"] = "podcast"
    sfx_library_roots: list[str] = Field(default_factory=list)
    exclude_sfx_dirs: list[str] = Field(
        default_factory=lambda: ["juli", "miller", "projects", "snippets"]
    )


class Project(BaseModel):
    id: str
    title: str
    language: Language
    default_engine: EngineName | None = None
    raw_script: str = ""
    script: Script
    report: ParseReport | None = None
    stems: list[Stem] = Field(default_factory=list)
    alignments: dict[str, AlignmentResult] = Field(default_factory=dict)
    cues: list[Cue] = Field(default_factory=list)
    suno: list[SunoTrack] = Field(default_factory=list)
    mix: MixResult | None = None
    # True when cuts/takes changed since the last mix render.
    mix_stale: bool = False
    settings: ProjectSettings = Field(default_factory=ProjectSettings)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ProjectSummary(BaseModel):
    id: str
    title: str
    language: Language
    scenes: int
    lines: int
    stems: int = 0
    runtime_estimate_s: float
    coverage: float
    updated_at: datetime


class ReferenceRequest(BaseModel):
    character_id: str
    text: str
    instruction: str
    output_path: str
    cfg_scale: float = 4.0
    seed: int = 42


class ReferenceResult(BaseModel):
    file: str
    duration_s: float
    sample_rate: int
    f0_hz: float | None = None


class Take(BaseModel):
    id: str
    file: str
    engine: str = "unknown"
    duration_s: float
    sample_rate: int
    seed: int
    f0_hz: float | None = None
    mfcc_sim: float | None = None
    selected: bool = False
    clip_id: str | None = None
    preview_url: str | None = None
    # True when the take is a vocals-only stem rather than the full mix.
    # Sung full mixes align badly with Whisper, so the aligner is skipped and
    # timing is estimated instead.
    vocal_isolated: bool = False
    created_at: datetime = Field(default_factory=utcnow)


class Stem(BaseModel):
    id: str
    scene: int
    speaker: str
    line_ids: list[str]
    text: str
    instruction: str
    # Suno lyric sheet for this stem: dialogue verbatim with [bracket] delivery
    # tags. Deterministic at build time, upgradeable via the LLM lyrics pass.
    suno_lyrics: str | None = None
    # Per-stem Suno song title / style tags shown and edited in Render.
    # None means the engine default (dialogue <stem_id> / audio drama radio play).
    suno_title: str | None = None
    suno_tags: str | None = None
    # Per-stem Suno generation params (0-100 sliders, model, vocal gender).
    # None means the engine/character default.
    suno_weirdness: float | None = Field(default=None, ge=0, le=100)
    suno_style_weight: float | None = Field(default=None, ge=0, le=100)
    suno_audio_weight: float | None = Field(default=None, ge=0, le=100)
    suno_mv: str | None = None
    suno_vocal_gender: str | None = None
    # Reference voice override for this stem (a RefVoice id of the speaker).
    # None means the character's default reference.
    ref_voice_id: str | None = None
    cfg_scale: float = 2.5
    seed: int = 42
    takes: list[Take] = Field(default_factory=list)

    @property
    def selected_take(self) -> Take | None:
        for take in self.takes:
            if take.selected:
                return take
        return None


class StemRenderRequest(BaseModel):
    project_id: str
    stem_id: str
    speaker: str
    text: str
    instruction: str
    output_dir: str
    ref_audio_path: str | None = None
    ref_text: str | None = None
    language: str = "en"
    cfg_scale: float = 2.5
    seed: int = 42
    candidates: int = Field(default=1, ge=1, le=5)
    persona_id: str | None = None
    persona_model: str | None = None
    mv: str = "chirp-hawk"
    # Per-stem song title / style tags. None falls back to the engine default.
    suno_title: str | None = None
    suno_tags: str | None = None
    # Per-stem generation params (None = engine/character default).
    suno_weirdness: float | None = None
    suno_style_weight: float | None = None
    suno_audio_weight: float | None = None
    suno_mv: str | None = None
    suno_vocal_gender: str | None = None
    # Suno lyric sheet override. When None the stored stem sheet (or the stem
    # text as a last resort) is sent to Suno.
    suno_lyrics: str | None = None


class StemRenderResult(BaseModel):
    stem_id: str
    engine: str
    sample_rate: int
    takes: list[Take]
    selected_take_id: str
    text_sha256: str
    created_at: datetime = Field(default_factory=utcnow)


class AlignedLine(BaseModel):
    line_id: str
    # Which stem take this cut window lives in. None means "the stem's
    # selected take" for projects saved before multi-track cuts existed.
    take_id: str | None = None
    start_s: float
    end_s: float
    confidence: float | None = None
    fallback: bool = False


class AlignmentResult(BaseModel):
    stem_id: str
    lines: list[AlignedLine]
    wer: float | None = None
    aligner: str = "whisper"
    created_at: datetime = Field(default_factory=utcnow)


class Job(BaseModel):
    id: str
    type: str
    payload: dict[str, Any]
    status: JobStatus = "queued"
    priority: int = 0
    progress: float = 0.0
    message: str = ""
    result: dict[str, Any] | None = None
    error: str | None = None
    attempts: int = 0
    max_attempts: int = 1
    worker_id: str | None = None
    cancel_requested: bool = False
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def terminal(self) -> bool:
        return self.status in {"succeeded", "failed", "cancelled"}
