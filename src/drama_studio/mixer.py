from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import butter, resample_poly, sosfilt

from .models import MixResult, Project

SR = 24000
TAIL_S = 2.0


def _load_audio(path: Path, sample_rate: int = SR) -> np.ndarray:
    try:
        data, original = sf.read(str(path), always_2d=True, dtype="float32")
        mono = data.mean(axis=1)
        if int(original) != sample_rate:
            mono = resample_poly(mono, sample_rate, int(original)).astype(np.float32)
        return mono.astype(np.float32)
    except Exception:
        result = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(path),
                "-f",
                "f32le",
                "-ac",
                "1",
                "-ar",
                str(sample_rate),
                "-",
            ],
            capture_output=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode("utf-8", errors="replace")[:300])
        return np.frombuffer(result.stdout, dtype=np.float32).copy()


def _db(value: float) -> float:
    return float(10 ** (value / 20.0))


def _highpass(samples: np.ndarray, cutoff: float = 80.0, sample_rate: int = SR) -> np.ndarray:
    sos = butter(2, cutoff / (sample_rate / 2), btype="highpass", output="sos")
    return sosfilt(sos, samples).astype(np.float32)


def _telephone(samples: np.ndarray, sample_rate: int = SR) -> np.ndarray:
    sos = butter(4, [300 / (sample_rate / 2), 3400 / (sample_rate / 2)], btype="bandpass", output="sos")
    return np.tanh(sosfilt(sos, samples) * 1.5).astype(np.float32) * 0.85


def _place(
    buffer: np.ndarray,
    clip: np.ndarray,
    start_s: float,
    gain: float = 1.0,
    sample_rate: int = SR,
) -> None:
    start = max(0, round(start_s * sample_rate))
    if start >= len(buffer) or clip.size == 0:
        return
    length = min(len(clip), len(buffer) - start)
    buffer[start : start + length] += clip[:length] * gain


def _place_stereo(
    buffer: np.ndarray,
    clip: np.ndarray,
    start_s: float,
    gain: float = 1.0,
    sample_rate: int = SR,
) -> None:
    """Additively place a (2, n) or (n,) clip into a (2, N) buffer."""
    start = max(0, round(start_s * sample_rate))
    if start >= buffer.shape[1] or clip.size == 0:
        return
    if clip.ndim == 1:
        clip = np.stack([clip, clip])
    length = min(clip.shape[1], buffer.shape[1] - start)
    buffer[:, start : start + length] += clip[:, :length] * gain


def _speech_envelope(dialogue: np.ndarray, sample_rate: int = SR) -> np.ndarray:
    if dialogue.size == 0:
        return np.ones(0, dtype=np.float32)
    frame = max(1, int(0.05 * sample_rate))
    padded = np.pad(dialogue, (0, (-len(dialogue)) % frame))
    rms = np.sqrt(np.mean(padded.reshape(-1, frame) ** 2, axis=1))
    threshold = max(0.0025, float(rms.max()) * 0.02)
    mask = (rms > threshold).astype(np.float32)
    smooth = np.convolve(mask, np.ones(5) / 5, mode="same")
    expanded = np.repeat(smooth, frame)[: len(dialogue)]
    return expanded.astype(np.float32)


def _duck_envelope(speech: np.ndarray, depth_db: float = 4.5) -> np.ndarray:
    duck = _db(-depth_db)
    return (1.0 - (1.0 - duck) * speech).astype(np.float32)


# ------------------------------------------------------------------- spatial


def _scene_bounds(line_by_id, segments, durations) -> dict[int, tuple[float, float]]:
    """First start and last end of every scene that has placed segments."""
    bounds: dict[int, tuple[float, float]] = {}
    for seg_id, line_id, clip_path, pos, _fx in segments:
        line = line_by_id.get(line_id)
        if line is None:
            continue
        end = pos + durations.get(seg_id, 0.0)
        current = bounds.get(line.scene)
        if current is None:
            bounds[line.scene] = (pos, end)
        else:
            bounds[line.scene] = (min(current[0], pos), max(current[1], end))
    return bounds


def _first_stage(stage):
    """Any staged scene: enough to build a decoder-only room instance."""
    for key in sorted(stage.scenes):
        scene = stage.scenes[key]
        if scene.actors:
            return scene
    return next(iter(stage.scenes.values()), None)


def _render_staged_scenes(stage, tracks, scene_bounds, sr, total, spatial_render):
    """Render every staged scene into one Ambisonic timeline.

    Returns (bed, extra) where bed is (n_sh, total) ACN/N3D and extra is the
    centred dry contribution of actors blended out of the room. The bed is also
    what the Ambisonic export writes: rebuilding it per scene for the export
    instead would put each scene back at the end of the previous one whenever
    two scenes' tails overlap.
    """
    order = int(stage.order)
    bed = np.zeros((spatial_render.sh_channels(order), total), dtype=np.float32)
    extra = np.zeros(total, dtype=np.float32)

    for scene_id in spatial_render.staged_scenes(stage):
        scene = stage.scenes.get(str(scene_id))
        span = scene_bounds.get(scene_id)
        if scene is None or span is None:
            continue  # staged but nothing was placed in it

        speakers = [actor.speaker for actor in scene.actors]
        rows = [tracks.get((scene_id, speaker)) for speaker in speakers]
        if not any(row is not None for row in rows):
            continue

        length = max(row.size for row in rows if row is not None)
        sources = np.zeros((len(speakers), length), dtype=np.float32)
        for index, row in enumerate(rows):
            if row is not None:
                sources[index, : row.size] = row

        with spatial_render.RoomSimulator(scene, sr, order) as sim:
            scene_bed, scene_dry = spatial_render.render_scene(
                sim, scene, sources, speakers, span[0]
            )

        start = round(span[0] * sr)
        take = min(scene_bed.shape[1], total - start)
        if take <= 0:
            continue
        bed[:, start : start + take] += scene_bed[:, :take]
        extra[start : start + take] += scene_dry[:take]

    return bed, extra


def _write_ambisonic_master(path, bed, stage, sr, spatial_render, block: int = 1 << 15):
    """Write the summed Ambisonic timeline out as one ACN/SN3D (AmbiX) master.

    Takes the very array the stereo decode used, so the file is the length of
    the mix by construction and every scene sits where the mix puts it. The
    earlier version re-stitched the per-scene beds here and got both wrong:
    appended end to end they landed late in the timeline whenever a tail reached
    into the next scene, and a two-scene corridor mix came out 1.98 s longer than
    the stereo master.

    Converted and written in blocks, because N3D to SN3D needs a scratch copy and
    a 10-minute third-order master is well over a gigabyte. Unstaged scenes and
    the SFX bed have no Ambisonic representation and read as silence.
    """
    n_sh = spatial_render.sh_channels(stage.order)
    scene = _first_stage(stage)
    if scene is None or bed.shape[0] != n_sh or bed.shape[1] == 0:
        return None

    total = bed.shape[1]
    try:
        with (
            spatial_render.RoomSimulator(scene, sr, int(stage.order)) as sim,
            sf.SoundFile(
                str(path), "w", samplerate=sr, channels=n_sh, subtype="PCM_24", format="WAV"
            ) as handle,
        ):
            for left in range(0, total, block):
                # to_sn3d is a static per-channel scaling, so block boundaries
                # cannot change the result.
                handle.write(sim.to_sn3d(bed[:, left : left + block]).T)
    except Exception:
        path.unlink(missing_ok=True)
        return None
    # MixResult paths are project-relative, like the stereo master's.
    return f"mix/{path.name}"


def build_mix(
    project: Project,
    project_dir: Path,
    sfx_lookup,
    name: str = "master",
    only_scene: int | None = None,
) -> MixResult:
    """Assemble the master mix.

    `name` selects the output filenames inside `mix/`; the Stage step uses a
    per-scene name so a preview never overwrites the real master.

    `only_scene` keeps one scene's segments and rebases them to start at 0.
    That is what a scene audition needs: the Stage board samples motion paths
    on a scene-relative clock, so a full-length file would make every scene
    after the first move at the wrong time.
    """
    warnings: list[str] = []
    pause = project.settings.pause_ms / 1000.0
    scene_gap = project.settings.scene_gap_ms / 1000.0
    lines = project.script.iter_lines()
    line_by_id = {line.id: line for line in lines}

    # Legacy alignments store position_s=None for "script order". Older
    # builds seeded a per-stem cursor from 0 so every stem's first line sat
    # at 0 (overlap). Derive the correct global script-order cursor here:
    # Viktoria 1, Andreas 1, Grandmother 1, Viktoria 2, etc.
    _script_positions: dict[str, float] = {}
    if project.alignments:
        from .cuts import layout_script_order, repair_per_stem_positions

        # A cut sheet whose positions are per-stem cursors would stack the scenes,
        # so repair the view first: `None` means script order, and that is what the
        # legacy numbers were standing in for.
        import copy

        _project_view = copy.deepcopy(project)
        _repaired = repair_per_stem_positions(_project_view)
        if _repaired or any(
            seg.position_s is None
            for alignment in _project_view.alignments.values()
            for seg in alignment.lines
        ):
            layout_script_order(_project_view, overwrite_positions=False)
            for alignment in _project_view.alignments.values():
                for seg in alignment.lines:
                    if seg.position_s is not None:
                        _script_positions.setdefault(seg.line_id, seg.position_s)
        project = _project_view

    line_starts: dict[str, float] = {}
    scene_end: dict[int, float] = {}
    # Freeform: placement comes from alignment segments when available.
    # Each entry: (seg_id, line_id, path, position, effects-dict).
    # Effects carry per-region fade/volume/speed/pitch/reverb/dereverb.
    from .audio import apply_region_effects
    segments: list[tuple[str, str, Path, float, dict | None]] = []
    if project.alignments:
        for alignment in project.alignments.values():
            for seg in alignment.lines:
                seg_id = seg.id or seg.line_id
                clip_path = project_dir / "lines" / f"{seg_id}.wav"
                if not clip_path.exists():
                    # Fall back to legacy line_id filename.
                    alt = project_dir / "lines" / f"{seg.line_id}.wav"
                    if alt.exists():
                        clip_path = alt
                pos = seg.position_s
                if pos is None:
                    pos = _script_positions.get(seg.line_id)
                if pos is None:
                    pos = 0.0
                fx = {
                    "gain_db": float(seg.gain_db or 0.0),
                    "fade_in_s": float(seg.fade_in_s or 0.0),
                    "fade_out_s": float(seg.fade_out_s or 0.0),
                    "speed": float(seg.speed or 1.0),
                    "pitch_cents": int(seg.pitch_cents or 0),
                    "reverb_mix": float(seg.reverb_mix or 0.0),
                    "dereverb": bool(seg.dereverb),
                    "enabled": bool(getattr(seg, "enabled", True)),
                }
                segments.append((seg_id, seg.line_id, clip_path, float(pos), fx))
    else:
        # Legacy: script order + cut durations.
        timeline = 0.0
        previous_scene: int | None = None
        for line in lines:
            if previous_scene is not None and line.scene != previous_scene:
                timeline += scene_gap
            clip_path = project_dir / "lines" / f"{line.id}.wav"
            if clip_path.exists():
                info = sf.info(str(clip_path))
                duration = float(info.frames) / float(info.samplerate)
            else:
                duration = 0.0
                warnings.append(f"missing line clip for {line.id} ({line.speaker})")
            segments.append((line.id, line.id, clip_path, timeline, None))
            timeline += duration + pause
            previous_scene = line.scene

    if only_scene is not None:
        # A scene audition is the scene, not the play it sits in. Keep its
        # segments and rebase them so sample 0 is the scene's first sound;
        # the Stage transport's playhead is scene-relative.
        scene_line_ids = {line.id for line in lines if line.scene == only_scene}
        kept = [item for item in segments if item[1] in scene_line_ids]
        if kept:
            offset = min(item[3] for item in kept)
            segments = [
                (seg_id, line_id, clip_path, pos - offset, fx)
                for seg_id, line_id, clip_path, pos, fx in kept
            ]
        else:
            segments = []

    # line_starts: first segment position per line; scene_end: max end per scene.
    # Stagger same-line segments left-to-right so sub-clips don't overlap
    # (legacy aligned lines for one line share a position).
    _seen_lines: dict[str, float] = {}
    placed: list[tuple[str, str, Path, float, dict | None]] = []
    for seg_id, line_id, clip_path, pos, fx in segments:
        line = line_by_id.get(line_id)
        rate = max(0.1, float((fx or {}).get("speed", 1.0) or 1.0))
        dur = 0.0
        if clip_path.exists():
            try:
                info = sf.info(str(clip_path))
                dur = (float(info.frames) / float(info.samplerate)) / rate
            except Exception:
                dur = 0.0
        else:
            warnings.append(f"missing line clip for {line_id} ({line.speaker if line else ''})")
        # For segments beyond the first for this line, shift by the running
        # cursor of previous segments (they belong adjacent, not overlapped).
        seg_pos = pos
        if line_id in _seen_lines:
            seg_pos = max(pos, _seen_lines[line_id])
        _seen_lines[line_id] = seg_pos + dur
        placed.append((seg_id, line_id, clip_path, seg_pos, fx))
        if line_id not in line_starts or seg_pos < line_starts[line_id]:
            line_starts[line_id] = seg_pos
        scene = line.scene if line else None
        if scene is not None:
            scene_end[scene] = max(scene_end.get(scene, 0.0), seg_pos + dur)
    segments = placed

    max_end = 0.0
    durations: dict[str, float] = {}
    for seg_id, line_id, clip_path, pos, fx in segments:
        rate = max(0.1, float((fx or {}).get("speed", 1.0) or 1.0))
        dur = 0.0
        if clip_path.exists():
            try:
                info = sf.info(str(clip_path))
                dur = (float(info.frames) / float(info.samplerate)) / rate
            except Exception:
                dur = 0.0
        durations[seg_id] = dur
        max_end = max(max_end, pos + dur)

    total_s = max_end + TAIL_S if segments else (0.0 + TAIL_S)

    # Spatial staging is opt-in per project and per scene. A project with
    # staging enabled but no native renderer falls back to the dry mono mix and
    # says so, rather than failing the job.
    from . import spatial as spatial_render

    stage = project.stage
    staged = set(spatial_render.staged_scenes(stage)) if stage and stage.enabled else set()
    spatial_on = bool(staged) and spatial_render.available()
    if staged and not spatial_on:
        warnings.append("spatial staging enabled but the renderer library is missing; mixed dry")
        staged = set()

    sr = int(project.settings.spatial_sample_rate) if spatial_on else SR
    total = max(1, int(total_s * sr))
    tail_pad = min(stage.tail_s, spatial_render.max_tail_s(sr)) if spatial_on else 0.0

    # A staged scene renders as a unit: its own room, its own source list, its
    # own zero-padded tail. Everything is gathered relative to the scene start
    # and written back at the scene's timeline offset.
    scene_bounds = _scene_bounds(line_by_id, segments, durations)
    bed: np.ndarray | None = None
    dialogue = np.zeros(total, dtype=np.float32)

    if spatial_on:
        # Per (scene, speaker) timelines, in samples relative to scene start.
        # Only speakers that actually have an actor slot get a track; anything
        # else stays in the centre dialogue so nothing silently disappears.
        cast: dict[int, set[str]] = {
            scene_id: {actor.speaker for actor in stage.scenes[str(scene_id)].actors}
            for scene_id in staged
            if str(scene_id) in stage.scenes
        }
        tracks: dict[tuple[int, str], np.ndarray] = {}
        pending: list[tuple[int, str, np.ndarray, float]] = []
    else:
        cast = {}
        tracks = {}
        pending = []

    # Place every enabled segment clip at its (freeform) position.
    # Disabled segments keep their slot (timing) but stay silent.
    for seg_id, line_id, clip_path, pos, fx in segments:
        if not clip_path.exists():
            continue
        if fx is not None and not fx.get("enabled", True):
            continue
        clip = _load_audio(clip_path, sr)
        if fx:
            try:
                clip = apply_region_effects(
                    clip, sr,
                    gain_db=float(fx.get("gain_db", 0.0)),
                    fade_in_s=float(fx.get("fade_in_s", 0.0)),
                    fade_out_s=float(fx.get("fade_out_s", 0.0)),
                    speed=float(fx.get("speed", 1.0)),
                    pitch_cents=int(fx.get("pitch_cents", 0)),
                    reverb_mix=float(fx.get("reverb_mix", 0.0)),
                    dereverb=bool(fx.get("dereverb", False)),
                )
            except Exception as exc:
                warnings.append(f"region effects failed for {line_id}: {exc}")

        line = line_by_id.get(line_id)
        scene = line.scene if line is not None else None
        if scene is not None and scene in staged and line.speaker in cast.get(scene, set()):
            # Staged speech joins its scene's per-speaker track instead of the
            # mono sum; the room renderer replaces the panning it used to get.
            key = (scene, line.speaker)
            if key not in tracks:
                span = scene_bounds[scene]
                size = round((span[1] - span[0] + tail_pad) * sr)
                tracks[key] = np.zeros(max(1, size), dtype=np.float32)
            pending.append((scene, line.speaker, clip, pos))
        else:
            _place(dialogue, clip, pos, sample_rate=sr)

    if spatial_on:
        for scene, speaker, clip, pos in pending:
            span = scene_bounds[scene]
            _place(tracks[(scene, speaker)], clip, pos - span[0], sample_rate=sr)
        dialogue = _highpass(dialogue, 80.0, sr)
        try:
            bed, extra = _render_staged_scenes(
                stage, tracks, scene_bounds, sr, total, spatial_render
            )
        except spatial_render.SpatialUnavailable as exc:
            # Never lose lines to a renderer failure: fold the staged tracks back
            # into the centre and mix the project dry.
            warnings.append(f"spatial render failed ({exc}); mixed dry")
            bed = None
            for (scene, _speaker), buffer in tracks.items():
                span = scene_bounds[scene]
                _place(dialogue, buffer, span[0], sample_rate=sr)
        else:
            dialogue += extra

    # Duck under everything the listener hears, spatial bed included. The W
    # channel is the omni component, so it is the right thing to envelope.
    speech_source = dialogue if bed is None else dialogue + bed[0]
    speech = _speech_envelope(speech_source, sr)
    duck = _duck_envelope(speech)

    effects = np.zeros(total, dtype=np.float32)
    cue_count = 0
    for cue in project.cues:
        item = sfx_lookup(cue.sfx_id)
        if item is None:
            warnings.append(f"missing SFX for cue {cue.name} ({cue.sfx_id})")
            continue
        path = Path(item.path)
        if not path.exists():
            warnings.append(f"SFX file missing for cue {cue.name}: {path}")
            continue
        try:
            source = _load_audio(path, sr)
        except Exception as exc:
            warnings.append(f"could not decode {cue.name}: {exc}")
            continue

        if cue.position_s is not None:
            # Freeform: the cue's own slot, bounded by its own end or the
            # timeline. No scene is involved, so a score can span the play and a
            # bed dragged to the last scene stays where it was put.
            anchor = float(cue.position_s)
            limit = total_s if cue.end_s is None else min(total_s, float(cue.end_s))
        else:
            anchor = line_starts.get(cue.anchor_line_id, 0.0) + cue.offset_s
            limit = scene_end.get(
                next(
                    (line.scene for line in lines if line.id == cue.anchor_line_id),
                    project.script.scenes[-1].id if project.script.scenes else 0,
                ),
                total_s,
            )
        if cue.kind == "one_shot":
            needed = len(source)
        else:
            needed = max(0, int((limit - anchor) * sr))
        if needed <= 0 or anchor >= total_s:
            # Say so, and say why: a cue that cannot be heard is a mistake the
            # user made, and this used to report a successful mix. The two cases
            # read differently — past the end of the play, versus past the end of
            # the range the cue is allowed to occupy (its scene, when anchored).
            if anchor >= total_s:
                reason = f"it starts at {anchor:.1f}s, after the end of the mix ({total_s:.1f}s)"
            else:
                reason = (
                    f"it starts at {anchor:.1f}s, past the end of the "
                    f"{'scene it is anchored to' if cue.position_s is None else 'range it was given'}"
                    f" ({limit:.1f}s)"
                )
            warnings.append(f"cue {cue.name} is not in the mix: {reason}")
            continue
        if cue.kind != "one_shot":
            if cue.loop:
                source = np.tile(source, int(np.ceil(needed / max(1, len(source)))))
            source = source[:needed]
        if cue.filter == "telephone":
            source = _telephone(source, sr)
        elif cue.filter == "radio":
            source = _telephone(source, sr) * 0.8
        gain = _db(cue.gain_db)
        if cue.duck and cue.kind != "one_shot":
            start = max(0, int(anchor * sr))
            segment = duck[start : start + len(source)]
            if len(segment) < len(source):
                segment = np.pad(segment, (0, len(source) - len(segment)), constant_values=1.0)
            source = source * segment
        _place(effects, source, anchor, gain, sr)
        cue_count += 1

    if bed is None:
        mix = dialogue + effects
    else:
        # Decode the summed Ambisonic timeline once, then fold in the centre
        # material and the (still non-spatial) effects bed.
        with spatial_render.RoomSimulator(_first_stage(stage), sr, int(stage.order)) as decoder:
            dialogue_stereo = decoder.decode_stereo(bed)
        mix = np.zeros((2, total), dtype=np.float32)
        mix[0] = dialogue_stereo[0] + dialogue + effects
        mix[1] = dialogue_stereo[1] + dialogue + effects

    peak = float(np.max(np.abs(mix))) if mix.size else 0.0
    if peak > 0:
        mix = mix * (0.89 / peak)

    mix_dir = project_dir / "mix"
    mix_dir.mkdir(parents=True, exist_ok=True)
    pre_path = mix_dir / (f"{name}_pre.wav" if bed is not None else f"{name}_24k.wav")
    # soundfile wants (frames, channels); the mix is (channels, frames).
    sf.write(str(pre_path), mix.T if mix.ndim == 2 else mix, sr, subtype="PCM_24")

    ambisonic_path: str | None = None
    if bed is not None and stage is not None and stage.export_ambisonic:
        ambisonic_path = _write_ambisonic_master(
            mix_dir / f"{name}_ambisonic.wav", bed, stage, sr, spatial_render
        )
        if ambisonic_path is None:
            warnings.append("could not write the Ambisonic master")

    target = "-16" if project.settings.loudness_target == "podcast" else "-23"
    master = mix_dir / f"{name}.wav"
    mp3 = mix_dir / f"{name}.mp3"
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(pre_path),
            "-af",
            f"loudnorm=I={target}:TP=-1.5:LRA=11",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(master),
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        warnings.append(f"ffmpeg loudnorm failed; peak-normalised {sr // 1000} kHz master kept")
        master.write_bytes(pre_path.read_bytes())
        mp3_path = None
    else:
        result = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(master), "-b:a", "320k", str(mp3)],
            capture_output=True,
        )
        mp3_path = str(mp3.relative_to(project_dir)) if result.returncode == 0 else None

    return MixResult(
        file=str(master.relative_to(project_dir)),
        mp3=mp3_path,
        duration_s=round(total_s, 2),
        loudness_target=f"{target} LUFS",
        line_count=len({lid for (_sid, lid, cp, _p, _fx) in segments if cp.exists()}),
        cue_count=cue_count,
        spatial=bed is not None,
        ambisonic=ambisonic_path,
        warnings=warnings,
    )
