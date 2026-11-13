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
    # Advisory only: median pitch plus the p10/p90 range of voiced frames.
    f0_range_hz: tuple[float, float] | None = None
    source: Literal["unknown", "designed", "generated", "uploaded"] = "unknown"
    duration_s: float | None = None
    # Mean Whisper word probability. None for design/upload/legacy refs and
    # after a manual transcript edit.
    transcript_confidence: float | None = None
    transcript_source: Literal["whisper", "edited"] | None = None
    # Which seconds of the uploaded clip were used (generated refs only).
    window_start_s: float | None = None
    window_end_s: float | None = None
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime | None = None


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


class StageRoom(BaseModel):
    """Shoebox room geometry for one scene, in metres.

    Coordinates match SPARTA AmbiRoomSim: x is width (0..W), y is depth
    (0..D, the front wall is at y=0), z is height (0..H). +x is the
    listener's left at the default facing.
    """

    dims_m: tuple[float, float, float] = (6.0, 5.0, 3.0)
    # Per-wall absorption, in the order SAF's room sim wants it:
    # +x, -x, +y, -y, +z, -z.
    absorption: tuple[float, float, float, float, float, float] = (0.25,) * 6
    # Image-source reflection order. 0 renders the direct path only.
    reflections: int = Field(default=2, ge=0, le=4)

    @field_validator("dims_m")
    @classmethod
    def dims_in_range(cls, value: tuple[float, float, float]) -> tuple[float, float, float]:
        for axis, limit in zip(value, (20.0, 20.0, 6.0)):
            if axis < 0.5 or axis > limit:
                raise ValueError("room dimensions must be within the room sim range")
        return value

    @field_validator("absorption")
    @classmethod
    def absorption_in_range(
        cls, value: tuple[float, float, float, float, float, float]
    ) -> tuple[float, float, float, float, float, float]:
        if any(coeff < 0.02 or coeff > 0.98 for coeff in value):
            raise ValueError("wall absorption must be between 0.02 and 0.98")
        return value


class StageListener(BaseModel):
    """Where the microphone (the listener's head) sits, and which way it faces.

    facing_deg is a standard azimuth: 0 faces the front wall (-y), +90 faces
    +x. This is the convention SPARTA's studio draws and the one the renderer
    rotates the Ambisonic field by.
    """

    position_m: tuple[float, float, float] = (3.0, 4.0, 1.6)
    facing_deg: float = Field(default=0.0, ge=-180.0, le=180.0)


class StageActor(BaseModel):
    """One speaker's position in a scene.

    `direct` blends the spatialised render against a centred dry copy of the
    same clip: 1.0 is fully in the room, 0.0 is dry and centred. Narration
    usually wants a low value so the voice stays in front of the scene.
    """

    speaker: str
    position_m: tuple[float, float, float]
    facing_deg: float = Field(default=0.0, ge=-180.0, le=180.0)
    gain_db: float = Field(default=0.0, ge=-48.0, le=12.0)
    direct: float = Field(default=1.0, ge=0.0, le=1.0)


class StagePath(BaseModel):
    """Motion for one speaker: absolute-time keyframes, interpolated linearly.

    Times are seconds on the project timeline (the same clock the Cut board
    uses), not bars.
    """

    speaker: str
    keys: list[tuple[float, float, float, float]] = Field(default_factory=list)

    @field_validator("keys")
    @classmethod
    def keys_sorted(cls, value: list[tuple[float, float, float, float]]) -> list[tuple[float, float, float, float]]:
        times = [key[0] for key in value]
        if times != sorted(times):
            raise ValueError("path keyframes must be in ascending time order")
        return value


class SceneStage(BaseModel):
    """The full staging of one scene: room, listener, cast and motion."""

    room: StageRoom = Field(default_factory=StageRoom)
    listener: StageListener = Field(default_factory=StageListener)
    actors: list[StageActor] = Field(default_factory=list)
    paths: list[StagePath] = Field(default_factory=list)

    @field_validator("actors")
    @classmethod
    def actors_valid(cls, value: list[StageActor], info) -> list[StageActor]:
        if len(value) > 16:
            # ROOM_SIM_MAX_NUM_SOURCES in the DSP.
            raise ValueError("a scene supports at most 16 staged sources")
        speakers = [actor.speaker for actor in value]
        if len(set(speakers)) != len(speakers):
            raise ValueError("a speaker may occupy only one position per scene")
        room = info.data.get("room")
        for actor in value:
            limits = room.dims_m if room is not None else (20.0, 20.0, 6.0)
            for axis, limit in zip(actor.position_m, limits):
                if axis < 0.0 or axis > limit:
                    raise ValueError(f"{actor.speaker}: position must be inside the room")
        return value

    @field_validator("listener")
    @classmethod
    def listener_inside_room(cls, value: StageListener, info) -> StageListener:
        room = info.data.get("room")
        if room is not None:
            for axis, limit in zip(value.position_m, room.dims_m):
                if axis < 0.0 or axis > limit:
                    raise ValueError("listener must be inside the room")
        return value


class StageProject(BaseModel):
    """Spatial staging of a project: one SceneStage per scene id.

    Scenes with no entry render dry, so enabling spatial mixing on a project
    never changes the output until a scene is actually staged.
    """

    version: int = 1
    enabled: bool = False
    # Ambisonic order of the render: 1 = 4 channels, 3 = 16 channels.
    order: Literal[1, 3] = 3
    # Write mix/master_ambisonic.wav alongside the stereo master. This is a
    # large file (16ch 48k PCM-24 is ~2.3 MB per second), so it is opt-in.
    export_ambisonic: bool = False
    # Seconds of silence appended to each scene so the room tail rings out.
    # Capped by the renderer's circular buffer (see spatial.MAX_TAIL_S).
    tail_s: float = Field(default=0.6, ge=0.0, le=2.0)
    default_room: StageRoom = Field(default_factory=StageRoom)
    scenes: dict[str, SceneStage] = Field(default_factory=dict)


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
    # True when the master was rendered through the spatial stage rather than
    # the dry mono sum. Absent on mixes saved before staging existed.
    spatial: bool = False
    # mix/master_ambisonic.wav when an Ambisonic master was exported.
    ambisonic: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class ProjectSettings(BaseModel):
    pause_ms: int = 500
    scene_gap_ms: int = 1200
    loudness_target: Literal["podcast", "broadcast"] = "podcast"
    sfx_library_roots: list[str] = Field(default_factory=list)
    exclude_sfx_dirs: list[str] = Field(
        default_factory=lambda: ["juli", "miller", "projects", "snippets"]
    )
    # Spatial rendering runs on the CPU at ~8x faster than real time, so it
    # never needs the GPU serialisation the TTS engines do.
    spatial_sample_rate: Literal[24000, 48000] = 48000


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
    # Per-scene spatial staging. None or enabled=False keeps the dry mono mix.
    stage: StageProject | None = None
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
    f0_range_hz: tuple[float, float] | None = None
    warnings: list[str] = Field(default_factory=list)


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
    suno_negative_tags: str | None = None
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
    suno_negative_tags: str | None = None
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
    # Unique segment id (also the line-clip filename base). Empty/None is
    # normalised to a derived id on load/save so legacy files keep working.
    id: str = ""
    line_id: str
    # Which stem take this cut window lives in. None means "the stem's
    # selected take" for projects saved before multi-track cuts existed.
    take_id: str | None = None
    # Local cut window inside the take.
    start_s: float
    end_s: float
    # Freeform global timeline start. None => derive from script order
    # (legacy); normalised to a concrete value on load.
    position_s: float | None = None
    # Per-region effects (Cut board). All default to neutral so old projects
    # keep rendering exactly as before.
    gain_db: float = 0.0  # -48..+12 dB
    fade_in_s: float = 0.0  # seconds, linear ramp up from 0
    fade_out_s: float = 0.0  # seconds, linear ramp down to 0
    speed: float = 1.0  # 0.5..2.0 playback rate (resampled, informal)
    pitch_cents: int = 0  # -1200..+1200, phase-vocoder-ish via resample pair
    reverb_mix: float = 0.0  # 0..1 wet amount (convolver with noise IR)
    dereverb: bool = False  # STFT spectral-subtraction dereverb via audio.apply_region_effects
    # False = clip disabled (dim + silent in the editor, keeps its slot timing).
    enabled: bool = True
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
