"""Spatial stage: room acoustics and Ambisonic rendering for Drama Llama.

Wraps the ISC-licensed Spatial Audio Framework `ambi_roomsim` algorithm (a
shoebox room, image-source method) through a small C shim loaded with ctypes.
This is the same DSP that SPARTA's AmbiRoomSim plug-in wraps; the plug-in
itself is GPLv3 JUCE code and is neither used nor copied here.

Everything degrades gracefully: when the native library is missing,
`available()` is False and the mixer keeps rendering the dry mono master it
always produced.

Coordinate convention, shared with SPARTA's studio:
    x        width,  0..W   +x is the listener's left at the default facing
    y        depth,  0..D   the front wall is at y = 0
    z        height, 0..H
    facing   azimuth in degrees: 0 faces the front wall (-y), +90 faces +x
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path
from typing import Any, Self

import numpy as np

from .models import (
    SceneStage,
    StageActor,
    StageListener,
    StagePath,
    StageProject,
    StageRoom,
)

# The room sim's source ceiling (ROOM_SIM_MAX_NUM_SOURCES in the DSP).
MAX_SOURCES = 16

# SAF keeps 4*8192 samples of source history per band (IMS_CIRC_BUFFER_LENGTH),
# which bounds the longest echo it can render.
_CIRC_BUFFER_SAMPLES = 4 * 8192

# Motion is re-evaluated on a grid this coarse. Every position change makes SAF
# rebuild the image-source echograms, so per-sample motion is pointless work;
# 100 ms is far below the perceptual threshold for a walking actor.
MOTION_GRID_S = 0.1

_LIB_NAMES = ("libdrama_llama_spatial.so", "libdrama_llama_spatial.dylib", "drama_llama_spatial.dll")

_F32P = ctypes.POINTER(ctypes.c_float)


class SpatialUnavailable(RuntimeError):
    """The native renderer is missing, wrong, or failed mid-render."""


# --------------------------------------------------------------- library load


def _install_prefixes() -> list[Path]:
    """Per-user install roots, mirroring ``installer.core.paths.default_prefix``.

    Kept as a local copy on purpose: the installer core must not import torch
    or the app, and the app must not import the installer, so the two agree by
    convention. AGENTS.md treats these locations as fixed for that reason.
    """
    out: list[Path] = []
    override = os.environ.get("DRAMA_LLAMA_PREFIX")
    if override:
        out.append(Path(override).expanduser())
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        out.append(base / "DramaLlama")
    elif sys.platform == "darwin":
        out.append(Path.home() / "Library" / "Application Support" / "DramaLlama")
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        base = Path(xdg) if xdg else Path.home() / ".local" / "share"
        out.append(base / "drama-llama")
    return out


def _candidate_paths() -> list[Path]:
    """Places the shim library may live, most explicit first."""
    paths: list[Path] = []

    env = os.environ.get("DRAMA_LLAMA_SPATIAL_LIB")
    if env:
        candidate = Path(env).expanduser()
        if candidate.is_dir():
            paths.extend(candidate / name for name in _LIB_NAMES)
        else:
            paths.append(candidate)

    package_dir = Path(__file__).resolve().parent
    project_root = package_dir.parent.parent
    # Development tree: scripts/build_spatial.py puts it here.
    paths.extend(project_root / "build" / "spatial" / name for name in _LIB_NAMES)
    # Next to the C sources, for an in-tree cmake build.
    paths.extend(package_dir / "spatial" / "build" / name for name in _LIB_NAMES)
    # Virtualenv install (the installer puts engines beside the package).
    for base in (Path(sys.prefix), Path(sys.base_prefix)):
        paths.extend(base / "lib" / "drama-llama" / name for name in _LIB_NAMES)
    # Installed by the installer: <prefix>/lib/, see installer.core.paths.
    for prefix in _install_prefixes():
        paths.extend(prefix / "lib" / name for name in _LIB_NAMES)
    return paths


def library_path() -> Path | None:
    """The shim library that would be loaded, or None when absent."""
    for path in _candidate_paths():
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


_LOADED: ctypes.CDLL | None = None
_LOAD_FAILED = False


def _load() -> ctypes.CDLL | None:
    global _LOADED, _LOAD_FAILED
    if _LOADED is not None or _LOAD_FAILED:
        return _LOADED

    path = library_path()
    if path is None:
        _LOAD_FAILED = True
        return None
    try:
        lib = ctypes.CDLL(str(path))
        lib.dl_abi_version.restype = ctypes.c_int
        if lib.dl_abi_version() != 1:
            _LOAD_FAILED = True
            return None
    except (OSError, AttributeError):
        _LOAD_FAILED = True
        return None

    lib.dl_create.restype = ctypes.c_void_p
    lib.dl_create.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, _F32P, _F32P]
    lib.dl_destroy.argtypes = [ctypes.c_void_p]
    lib.dl_set_sources.argtypes = [ctypes.c_void_p, ctypes.c_int, _F32P, _F32P]
    lib.dl_set_listener.argtypes = [ctypes.c_void_p, _F32P, ctypes.c_float]
    lib.dl_process.argtypes = [ctypes.c_void_p, ctypes.c_int, _F32P, _F32P]
    lib.dl_decode_stereo.argtypes = [ctypes.c_void_p, ctypes.c_int, _F32P, _F32P]
    lib.dl_to_sn3d.argtypes = [ctypes.c_void_p, ctypes.c_int, _F32P]
    lib.dl_get_n_sh.restype = ctypes.c_int
    lib.dl_get_n_sh.argtypes = [ctypes.c_void_p]
    lib.dl_get_frame_size.restype = ctypes.c_int
    lib.dl_get_frame_size.argtypes = [ctypes.c_void_p]
    lib.dl_last_error.restype = ctypes.c_char_p
    lib.dl_last_error.argtypes = [ctypes.c_void_p]

    _LOADED = lib
    return lib


def available() -> bool:
    """True when the native spatial renderer can be used."""
    return _load() is not None


def max_tail_s(sample_rate: int) -> float:
    """Longest room tail the renderer can hold, in seconds."""
    return _CIRC_BUFFER_SAMPLES / float(sample_rate)


def sh_channels(order: int) -> int:
    """Ambisonic channel count for an encoding order."""
    return (order + 1) * (order + 1)


# Room presets for the Stage UI. Absorption is per wall in the order the room
# sim wants: +x, -x, +y, -y, +z, -z. Values follow common shoebox-design
# practice: carpet and sofas are lossy, tile and glass are live, and the floor
# is usually the least absorptive surface.
ROOM_PRESETS: tuple[dict[str, Any], ...] = (
    {
        "key": "living_room",
        "label": "Living room",
        "dims_m": (5.5, 4.5, 2.6),
        "absorption": (0.45, 0.45, 0.45, 0.45, 0.55, 0.15),
        "reflections": 2,
    },
    {
        "key": "study",
        "label": "Study / library",
        "dims_m": (4.0, 3.5, 2.6),
        "absorption": (0.55, 0.55, 0.55, 0.55, 0.60, 0.20),
        "reflections": 2,
    },
    {
        "key": "kitchen",
        "label": "Kitchen / tiled room",
        "dims_m": (4.0, 3.5, 2.6),
        "absorption": (0.10, 0.10, 0.10, 0.10, 0.18, 0.10),
        "reflections": 3,
    },
    {
        "key": "corridor",
        "label": "Narrow corridor",
        "dims_m": (2.0, 9.0, 2.4),
        "absorption": (0.20, 0.20, 0.25, 0.25, 0.30, 0.15),
        "reflections": 3,
    },
    {
        "key": "hall",
        "label": "Large hall",
        "dims_m": (14.0, 10.0, 5.0),
        "absorption": (0.25, 0.25, 0.25, 0.25, 0.35, 0.12),
        "reflections": 4,
    },
    {
        "key": "car",
        "label": "Car interior",
        "dims_m": (1.8, 3.4, 1.4),
        "absorption": (0.75, 0.75, 0.75, 0.75, 0.85, 0.60),
        "reflections": 1,
    },
    {
        "key": "stairwell",
        "label": "Concrete stairwell",
        "dims_m": (3.5, 3.5, 6.0),
        "absorption": (0.05, 0.05, 0.05, 0.05, 0.05, 0.04),
        "reflections": 4,
    },
    {
        "key": "outdoor",
        "label": "Outdoors (direct only)",
        "dims_m": (6.0, 5.0, 3.0),
        "absorption": (0.90, 0.90, 0.90, 0.90, 0.90, 0.90),
        "reflections": 0,
    },
)


def preset_room(key: str) -> StageRoom | None:
    """Build a StageRoom from a ROOM_PRESETS key."""
    for preset in ROOM_PRESETS:
        if preset["key"] == key:
            return StageRoom(
                dims_m=tuple(preset["dims_m"]),
                absorption=tuple(preset["absorption"]),
                reflections=preset["reflections"],
            )
    return None


def default_stage_room(stage: StageProject | None) -> StageRoom:
    return stage.default_room if stage is not None else StageRoom()


# Stage directions that imply the speaker moves. Matched against the
# parenthetical/direction/emotion text the parser already extracted, in the
# order below, so the first cue a speaker's lines carry wins.
MOTION_CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("approach", ("approach", "closer", "steps closer", "coming closer", "leans in")),
    ("retreat", ("retreat", "backs away", "steps back", "backing", "recoils")),
    ("leave", ("leaves", "leaving", "at the door", "as he goes", "on his way out")),
    ("enter", ("through the hatch", "at the hatch", "from the doorway", "enters", "comes in", "outside")),
    ("pace", ("pacing", "paces", "walks the deck", "prowls", "circles")),
)


def _cue_of(texts: list[str]) -> str | None:
    """The first motion cue a speaker's stage directions carry."""
    blob = " ".join(text.lower() for text in texts if text)
    for cue, needles in MOTION_CUES:
        if any(needle in blob for needle in needles):
            return cue
    return None


def _arc_slot(index: int, count: int, room: StageRoom) -> tuple[float, float]:
    """Where a static speaker stands: evenly spread on a shallow arc."""
    width, depth = room.dims_m[0], room.dims_m[1]
    share = (index + 1) / (max(1, count) + 1)
    return width * share, depth * (0.30 + 0.10 * (index % 2))


def _towards(
    from_point: tuple[float, float], towards: tuple[float, float], distance_m: float
) -> tuple[float, float]:
    """A point `distance_m` short of `towards`, on the line from `from_point`."""
    dx, dy = towards[0] - from_point[0], towards[1] - from_point[1]
    length = float(np.hypot(dx, dy))
    if length <= 1e-6:
        return from_point
    ratio = max(0.0, length - distance_m) / length
    return from_point[0] + dx * ratio, from_point[1] + dy * ratio


def _door_on_front_wall(x: float, room: StageRoom) -> tuple[float, float]:
    """The threshold: front wall is y = 0, clamped so the point stays inside."""
    return float(np.clip(x, 0.15, room.dims_m[0] - 0.15)), 0.2


def plan_path(
    speaker: str,
    cue: str,
    slot: tuple[float, float],
    listener: tuple[float, float, float],
    room: StageRoom,
    window: tuple[float, float],
) -> tuple[StagePath, tuple[float, float]] | None:
    """One speaker's move, and where they end up standing.

    The resting position is the *end* of the move for everyone who arrives
    somewhere, and the start for everyone who departs: a speaker who walks in
    should be shown where they now are, not hovering at the door.

    Returns None when the cue is unknown or the window is not a real span — a
    scene without alignments gets no paths rather than invented timings.
    """
    if len(window) != 2:
        return None  # speaker has not been aligned yet: no invented timing
    t0, t1 = float(window[0]), float(window[1])
    if t1 <= t0:
        return None

    # A negative "short of" distance lands beyond the slot: away from the
    # listener, which is where a walk-in starts and a walk-off ends.
    away = lambda distance: _towards(listener, slot, -distance)
    if cue == "approach":
        begin, end, rest = away(1.8), slot, slot
    elif cue == "enter":
        begin, end, rest = _door_on_front_wall(slot[0], room), slot, slot
    elif cue == "retreat":
        begin, end, rest = slot, away(1.6), slot
    elif cue == "leave":
        begin, end, rest = slot, _door_on_front_wall(slot[0], room), slot
    elif cue == "pace":
        width = room.dims_m[0]
        begin = (float(np.clip(slot[0] - 0.8, 0.2, width - 0.2)), slot[1])
        end = (float(np.clip(slot[0] + 0.8, 0.2, width - 0.2)), slot[1])
        rest = slot
    else:
        return None

    # Same ceiling rule as `layout_scene`: a source cannot stand taller than the
    # room it walks through, or the path fails validation on save.
    mouth_z = min(1.5, max(0.5, room.dims_m[2] * 0.75))

    def key(time_s: float, point: tuple[float, float]):
        # 2-D points here; the standing height is the same for every source.
        return (
            round(time_s, 3),
            round(float(np.clip(point[0], 0.05, room.dims_m[0] - 0.05)), 2),
            round(float(np.clip(point[1], 0.05, room.dims_m[1] - 0.05)), 2),
            round(mouth_z, 2),
        )

    path = StagePath(speaker=speaker, keys=[key(t0, begin), key(t1, end)])
    return path, (float(rest[0]), float(rest[1]))


def speaker_cues(lines: list[Any] | None, speakers: list[str]) -> dict[str, str]:
    """Motion cue per speaker, read from their stage directions."""
    if not lines:
        return {}
    cues: dict[str, str] = {}
    for speaker in speakers:
        texts = [
            value
            for line in lines
            if getattr(line, "speaker", None) == speaker
            for value in (
                getattr(line, "direction", "") or "",
                getattr(line, "parenthetical", "") or "",
                getattr(line, "emotion", "") or "",
            )
        ]
        cue = _cue_of(texts)
        if cue:
            cues[speaker] = cue
    return cues


def layout_scene(
    stage: StageProject,
    scene_id: int,
    speakers: list[str],
    *,
    lines: list[Any] | None = None,
    timing: dict[str, tuple[float, float]] | None = None,
    room: StageRoom | None = None,
) -> SceneStage:
    """A first-pass staging for a scene: cast spread on an arc, facing inward.

    Used when a scene is newly staged so the user always starts from something
    sensible instead of everything at the origin.

    When `lines` (the scene's script lines) are supplied, each speaker's stage
    directions are read for motion cues — "(approaching)", "(through the
    hatch)", "(pacing)" — and a speaker who moves gets a two-key path over the
    window in `timing` in which they actually speak. A mover's resting position
    is where the move ends, so the board shows the resolved spot, not the start.
    Without `lines` the layout is exactly the old geometric arc.

    A scene that is already staged keeps its room: the button re-places the
    cast, and resetting a 2 m corridor to the default library would edit the
    architecture behind the user's back. `room` overrides even that — the UI
    passes the room on screen, which may be an unsaved preset the user picked a
    second ago and the server has never heard of.
    """
    existing = stage.scenes.get(str(scene_id))
    room = room or (existing.room if existing is not None else None) or stage.default_room
    width, depth, height = room.dims_m
    # Ear and mouth height come from the room. 1.6 m is a standing adult in a
    # living room; a 1.4 m car roof makes that illegal and the scene model
    # rejects a listener outside its own walls, which used to surface as a 500
    # from the layout endpoint every time someone staged the car preset.
    ear_z = min(1.6, max(0.6, height * 0.8))
    mouth_z = min(1.5, max(0.5, height * 0.75))
    listener = StageListener(position_m=(width / 2.0, depth * 0.82, ear_z), facing_deg=0.0)

    chosen = speakers[:MAX_SOURCES]
    cues = speaker_cues(lines, chosen)
    windows = timing or {}

    actors: list[StageActor] = []
    paths: list[StagePath] = []
    for index, speaker in enumerate(chosen):
        x, y = _arc_slot(index, len(chosen), room)
        cue = cues.get(speaker)
        rest: tuple[float, float] = (x, y)
        path = None
        if cue:
            planned = plan_path(speaker, cue, (x, y), listener.position_m, room, windows.get(speaker, ()))
            if planned is not None:
                path, rest = planned
        # Face the listener, except someone walking off: they speak where they
        # are going, which is the whole point of staging the exit.
        if path is not None and cue in ("retreat", "leave"):
            travel = (path.keys[1][1] - path.keys[0][1], path.keys[1][2] - path.keys[0][2])
            facing = float(np.degrees(np.arctan2(travel[0], -travel[1])))
        else:
            dx = listener.position_m[0] - rest[0]
            dy = listener.position_m[1] - rest[1]
            # Stage azimuth: 0 faces -y, +90 faces +x.
            facing = float(np.degrees(np.arctan2(dx, -dy)))
        actors.append(
            StageActor(
                speaker=speaker,
                position_m=(round(rest[0], 2), round(rest[1], 2), round(mouth_z, 2)),
                facing_deg=round(float(np.clip(facing, -180.0, 180.0)), 1),
            )
        )
        if path is not None:
            paths.append(path)
    return SceneStage(room=room, listener=listener, actors=actors, paths=paths)


def _f32p(array: np.ndarray):
    return np.ascontiguousarray(array, dtype=np.float32).ctypes.data_as(_F32P)


# -------------------------------------------------------------------- project


def is_enabled(stage: StageProject | None) -> bool:
    """Whether a project should render spatially at all."""
    return bool(stage and stage.enabled and staged_scenes(stage))


def staged_scenes(stage: StageProject | None) -> list[int]:
    """Scene ids that carry at least one staged actor, sorted."""
    if stage is None:
        return []
    ids = []
    for key, scene_stage in stage.scenes.items():
        try:
            scene_id = int(key)
        except (TypeError, ValueError):
            continue
        if scene_stage and scene_stage.actors:
            ids.append(scene_id)
    return sorted(ids)


def scene_stage(stage: StageProject | None, scene_id: int) -> SceneStage | None:
    if stage is None:
        return None
    return stage.scenes.get(str(scene_id))


def scene_speakers(stage: StageProject | None, scene_id: int) -> list[str]:
    """Staged speakers of a scene, in the source order the renderer expects."""
    scene = scene_stage(stage, scene_id)
    if scene is None:
        return []
    return [actor.speaker for actor in scene.actors]


def all_wet(scene_stage_value: SceneStage | None) -> bool:
    """True when every actor is fully wet, so no dry blend is needed.

    `direct` blends against a centred dry copy, which costs an extra pass over
    the clips; with the default 1.0 everywhere that work is skipped.
    """
    if scene_stage_value is None:
        return True
    return all(actor.direct >= 1.0 for actor in scene_stage_value.actors)


# ------------------------------------------------------------------ geometry


def sample_path(keys: list[tuple[float, float, float, float]], time_s: float):
    """Interpolate a motion path at an absolute timeline time.

    None when there are no keyframes. Times outside the key range hold the
    first/last position, so an actor that walks in stays put afterwards
    instead of snapping to the origin.
    """
    if not keys:
        return None
    if time_s <= keys[0][0]:
        return keys[0][1], keys[0][2], keys[0][3]
    if time_s >= keys[-1][0]:
        return keys[-1][1], keys[-1][2], keys[-1][3]

    for index in range(len(keys) - 1):
        t0, x0, y0, z0 = keys[index]
        t1, x1, y1, z1 = keys[index + 1]
        if t0 <= time_s <= t1:
            span = t1 - t0
            ratio = 0.0 if span <= 0 else (time_s - t0) / span
            return (x0 + (x1 - x0) * ratio, y0 + (y1 - y0) * ratio, z0 + (z1 - z0) * ratio)
    return keys[-1][1], keys[-1][2], keys[-1][3]


def clamp_to_room(point, room: SceneStage) -> tuple[float, float, float]:
    """Keep a position inside the shoebox (a path key may fall outside)."""
    return (
        float(np.clip(point[0], 0.0, room.dims_m[0])),
        float(np.clip(point[1], 0.0, room.dims_m[1])),
        float(np.clip(point[2], 0.0, room.dims_m[2])),
    )


def actor_state(
    scene: SceneStage, actor: StageActor, time_s: float
) -> tuple[tuple[float, float, float], float, float]:
    """Position, linear gain and dry/wet of one actor at a point in time."""
    keys = next((p.keys for p in scene.paths if p.speaker == actor.speaker and p.keys), None)
    point = sample_path(keys, time_s) if keys else None
    if point is None:
        point = actor.position_m
    gain = 10.0 ** (actor.gain_db / 20.0)
    return clamp_to_room(point, scene.room), gain, actor.direct


def source_state_at(scene: SceneStage, time_s: float):
    """Positions (n,3), gains (n,) and direct values (n,) in actor order."""
    states = [actor_state(scene, actor, time_s) for actor in scene.actors]
    positions = np.array([state[0] for state in states], dtype=np.float32).reshape(-1, 3)
    gains = np.array([state[1] for state in states], dtype=np.float32)
    direct = np.array([state[2] for state in states], dtype=np.float32)
    return positions, gains, direct


# ------------------------------------------------------------------- renderer


class RoomSimulator:
    """One native ambi_roomsim instance; use as a context manager.

    Room geometry and Ambisonic order are fixed at construction because SAF
    rebuilds its whole image-source state when they change. Listener pose and
    source positions are cheap to move and set per render block.
    """

    def __init__(
        self,
        scene: SceneStage,
        sample_rate: int = 48000,
        order: int = 3,
    ) -> None:
        lib = _load()
        if lib is None:
            raise SpatialUnavailable(
                "spatial renderer not available; build it with scripts/build_spatial.py"
            )
        self._lib = lib
        self._handle = None
        self.sample_rate = int(sample_rate)
        self.order = int(order)
        self.scene = scene

        room = (ctypes.c_float * 3)(*scene.room.dims_m)
        absorption = (ctypes.c_float * 6)(*scene.room.absorption)
        enable = 1 if scene.room.reflections > 0 else 0
        handle = lib.dl_create(
            self.sample_rate, self.order, max(0, scene.room.reflections), enable, room, absorption
        )
        if not handle:
            raise SpatialUnavailable("could not create a room simulator instance")

        self._handle = handle
        self.n_sh = int(lib.dl_get_n_sh(handle))
        self.frame_size = int(lib.dl_get_frame_size(handle)) or 128

    # -- lifecycle

    def close(self) -> None:
        if self._handle is not None:
            self._lib.dl_destroy(self._handle)
            self._handle = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _require(self) -> int:
        if self._handle is None:
            raise SpatialUnavailable("room simulator is closed")
        return self._handle

    def _error(self) -> str:
        raw = self._lib.dl_last_error(self._handle)
        return raw.decode("utf-8", "replace") if raw else "unknown room-sim error"

    # -- configuration

    def set_listener(self, position, facing_deg: float) -> None:
        handle = self._require()
        array = np.array(position, dtype=np.float32).reshape(3)
        if self._lib.dl_set_listener(handle, _f32p(array), ctypes.c_float(float(facing_deg))) != 0:
            raise SpatialUnavailable(self._error())

    def apply_scene_state(self, scene: SceneStage, time_s: float = 0.0) -> None:
        """Push a scene's listener and (possibly moved) cast onto the instance."""
        self.set_listener(scene.listener.position_m, scene.listener.facing_deg)
        positions, gains, _direct = source_state_at(scene, time_s)
        self.set_sources(positions, gains)

    def set_sources(self, positions: np.ndarray, gains) -> None:
        handle = self._require()
        count = int(positions.shape[0])
        if count > MAX_SOURCES:
            raise SpatialUnavailable(f"a scene supports at most {MAX_SOURCES} staged sources")
        gain_array = None if gains is None else np.asarray(gains, dtype=np.float32)
        if self._lib.dl_set_sources(handle, count, _f32p(positions), None if gain_array is None else _f32p(gain_array)) != 0:
            raise SpatialUnavailable(self._error())

    # -- rendering

    def process(self, sources: np.ndarray) -> np.ndarray:
        """Render (n_sources, n_samples) mono to (n_sh, n_samples) ACN/N3D.

        The shim buffers SAF's fixed internal frame size itself, so any block
        length is accepted.
        """
        handle = self._require()
        data = np.ascontiguousarray(sources, dtype=np.float32)
        if data.ndim == 1:
            data = data.reshape(1, -1)
        if data.shape[1] == 0:
            return np.zeros((self.n_sh, 0), dtype=np.float32)

        n_sources, n_samples = data.shape
        out = np.zeros((self.n_sh, n_samples), dtype=np.float32)
        if n_sources == 0:
            return out
        if self._lib.dl_process(handle, n_samples, _f32p(data), _f32p(out)) != 0:
            raise SpatialUnavailable(self._error())
        return out

    def decode_stereo(self, sh: np.ndarray) -> np.ndarray:
        """Decode an ACN/N3D bed to stereo, shape (2, n_samples)."""
        handle = self._require()
        data = np.ascontiguousarray(sh, dtype=np.float32)
        if data.ndim != 2 or data.shape[1] == 0:
            return np.zeros((2, 0), dtype=np.float32)
        out = np.zeros((2, data.shape[1]), dtype=np.float32)
        if self._lib.dl_decode_stereo(handle, data.shape[1], _f32p(data), _f32p(out)) != 0:
            raise SpatialUnavailable(self._error())
        return out

    def to_sn3d(self, sh: np.ndarray) -> np.ndarray:
        """ACN/N3D to ACN/SN3D (AmbiX). Returns a copy."""
        handle = self._require()
        data = np.ascontiguousarray(sh, dtype=np.float32).copy()
        if data.ndim != 2 or data.shape[1] == 0:
            return data
        if self._lib.dl_to_sn3d(handle, data.shape[1], _f32p(data)) != 0:
            raise SpatialUnavailable(self._error())
        return data


# --------------------------------------------------------------- scene render


def render_scene(
    sim: RoomSimulator,
    scene: SceneStage,
    sources: np.ndarray,
    speakers: list[str],
    start_s: float,
    tail_s: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Spatialise one scene.

    `sources` is (len(speakers), n_samples) mono at the simulator's sample
    rate, laid out so sample 0 is `start_s` on the project timeline. Motion
    paths are re-evaluated every MOTION_GRID_S seconds.

    Path keyframe times are **scene-relative**, not project-absolute: a stem is
    one speaker's continuous performance inside one scene, and the mixer places
    every stem of a scene at that scene's first sound, so the position of a line
    inside its stem *is* its position inside the scene. Keyframe `t = 0` is
    therefore sample 0 of `sources`, which is why the lookup below subtracts
    `start_s`. Querying the keys at project time instead made every staged
    scene after the first one sit still at its last keyframe — and the first
    scene only ever looked correct by coincidence.

    Returns (ambisonic bed, dry centred mono):
      bed  (n_sh, n_samples + tail)  ACN/N3D
      dry  (n_samples + tail,)       the `direct` portion, pre-summed
    """
    data = np.ascontiguousarray(sources, dtype=np.float32)
    if data.ndim == 1:
        data = data.reshape(1, -1)
    if data.shape[0] != len(speakers):
        raise SpatialUnavailable("source count does not match the staged speaker list")

    tail = round(max(0.0, min(tail_s, max_tail_s(sim.sample_rate))) * sim.sample_rate)
    if tail:
        data = np.pad(data, ((0, 0), (0, tail)))
    n_samples = data.shape[1]

    # Map actors -> rows of the source matrix. Unstaged speakers stay silent.
    slots = {speaker: index for index, speaker in enumerate(speakers)}
    actors = list(scene.actors)
    bed = np.zeros((sim.n_sh, n_samples), dtype=np.float32)
    dry = np.zeros(n_samples, dtype=np.float32)

    sim.set_listener(scene.listener.position_m, scene.listener.facing_deg)

    block = max(sim.frame_size, int(MOTION_GRID_S * sim.sample_rate) // sim.frame_size * sim.frame_size)
    split = not all_wet(scene)

    for start in range(0, n_samples, block):
        stop = min(start + block, n_samples)
        mid_s = ((start + stop) * 0.5) / sim.sample_rate

        positions, gains, direct = source_state_at(scene, mid_s)
        window = np.zeros((len(actors), stop - start), dtype=np.float32)
        for index, actor in enumerate(actors):
            slot = slots.get(actor.speaker)
            if slot is None:
                continue
            window[index] = data[slot, start:stop]

        if positions.shape[0] == 0:
            continue
        sim.set_sources(positions, gains)

        # The dry portion is taken before the wet render so a fully-wet scene
        # (the default) never spends time on it.
        if split:
            for index, actor in enumerate(actors):
                slot = slots.get(actor.speaker)
                if slot is None or direct[index] >= 1.0:
                    continue
                portion = 1.0 - float(direct[index])
                dry[start:stop] += window[index] * portion
            # Zero the dry part of each actor so the room gets the wet share.
            for index, actor in enumerate(actors):
                if direct[index] >= 1.0:
                    continue
                window[index] *= float(direct[index])

        bed[:, start:stop] += sim.process(window)

    return bed, dry
