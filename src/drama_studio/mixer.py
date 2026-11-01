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


def _highpass(samples: np.ndarray, cutoff: float = 80.0) -> np.ndarray:
    sos = butter(2, cutoff / (SR / 2), btype="highpass", output="sos")
    return sosfilt(sos, samples).astype(np.float32)


def _telephone(samples: np.ndarray) -> np.ndarray:
    sos = butter(4, [300 / (SR / 2), 3400 / (SR / 2)], btype="bandpass", output="sos")
    return np.tanh(sosfilt(sos, samples) * 1.5).astype(np.float32) * 0.85


def _place(buffer: np.ndarray, clip: np.ndarray, start_s: float, gain: float = 1.0) -> None:
    start = max(0, round(start_s * SR))
    if start >= len(buffer) or clip.size == 0:
        return
    length = min(len(clip), len(buffer) - start)
    buffer[start : start + length] += clip[:length] * gain


def _speech_envelope(dialogue: np.ndarray) -> np.ndarray:
    if dialogue.size == 0:
        return np.ones(0, dtype=np.float32)
    frame = max(1, int(0.05 * SR))
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


def build_mix(project: Project, project_dir: Path, sfx_lookup) -> MixResult:
    warnings: list[str] = []
    pause = project.settings.pause_ms / 1000.0
    scene_gap = project.settings.scene_gap_ms / 1000.0
    lines = project.script.iter_lines()
    line_by_id = {line.id: line for line in lines}

    line_starts: dict[str, float] = {}
    scene_end: dict[int, float] = {}
    # Freeform: placement comes from alignment segments when available.
    segments: list[tuple[str, str, Path, float]] = []  # (seg_id, line_id, path, position)
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
                pos = seg.position_s if seg.position_s is not None else 0.0
                segments.append((seg_id, seg.line_id, clip_path, float(pos)))
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
            segments.append((line.id, line.id, clip_path, timeline))
            timeline += duration + pause
            previous_scene = line.scene

    # line_starts: first segment position per line; scene_end: max end per scene.
    for seg_id, line_id, clip_path, pos in segments:
        if line_id not in line_starts or pos < line_starts[line_id]:
            line_starts[line_id] = pos
        line = line_by_id.get(line_id)
        scene = line.scene if line else None
        dur = 0.0
        if clip_path.exists():
            try:
                info = sf.info(str(clip_path))
                dur = float(info.frames) / float(info.samplerate)
            except Exception:
                dur = 0.0
        else:
            warnings.append(f"missing line clip for {line_id} ({line.speaker if line else ''})")
        if scene is not None:
            scene_end[scene] = max(scene_end.get(scene, 0.0), pos + dur)

    max_end = 0.0
    for seg_id, line_id, clip_path, pos in segments:
        dur = 0.0
        if clip_path.exists():
            try:
                info = sf.info(str(clip_path))
                dur = float(info.frames) / float(info.samplerate)
            except Exception:
                dur = 0.0
        max_end = max(max_end, pos + dur)

    total_s = max_end + TAIL_S if segments else (0.0 + TAIL_S)
    total = max(1, int(total_s * SR))
    dialogue = np.zeros(total, dtype=np.float32)

    # Place every segment clip at its (freeform) position.
    seen_paths: set[Path] = set()
    for seg_id, line_id, clip_path, pos in segments:
        if not clip_path.exists():
            continue
        clip = _load_audio(clip_path)
        _place(dialogue, clip, pos)
    dialogue = _highpass(dialogue, 80.0)

    speech = _speech_envelope(dialogue)
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
            source = _load_audio(path)
        except Exception as exc:
            warnings.append(f"could not decode {cue.name}: {exc}")
            continue

        anchor = line_starts.get(cue.anchor_line_id, 0.0) + cue.offset_s
        if cue.kind == "one_shot":
            needed = len(source)
        else:
            end = scene_end.get(
                next(
                    (line.scene for line in lines if line.id == cue.anchor_line_id),
                    project.script.scenes[-1].id if project.script.scenes else 0,
                ),
                total_s,
            )
            needed = max(0, int((min(total_s, end) - anchor) * SR))
        if needed <= 0:
            continue
        if cue.kind != "one_shot":
            if cue.loop:
                source = np.tile(source, int(np.ceil(needed / max(1, len(source)))))
            source = source[:needed]
        if cue.filter == "telephone":
            source = _telephone(source)
        elif cue.filter == "radio":
            source = _telephone(source) * 0.8
        gain = _db(cue.gain_db)
        if cue.duck and cue.kind != "one_shot":
            start = max(0, int(anchor * SR))
            segment = duck[start : start + len(source)]
            if len(segment) < len(source):
                segment = np.pad(segment, (0, len(source) - len(segment)), constant_values=1.0)
            source = source * segment
        _place(effects, source, anchor, gain)
        cue_count += 1

    mix = dialogue + effects
    peak = float(np.max(np.abs(mix))) if mix.size else 0.0
    if peak > 0:
        mix = mix * (0.89 / peak)

    mix_dir = project_dir / "mix"
    mix_dir.mkdir(parents=True, exist_ok=True)
    pre_path = mix_dir / "master_24k.wav"
    sf.write(str(pre_path), mix, SR, subtype="PCM_24")

    target = "-16" if project.settings.loudness_target == "podcast" else "-23"
    master = mix_dir / "master.wav"
    mp3 = mix_dir / "master.mp3"
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
        warnings.append("ffmpeg loudnorm failed; peak-normalised 24 kHz master kept")
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
        line_count=len({lid for (_sid, lid, cp, _p) in segments if cp.exists()}),
        cue_count=cue_count,
        warnings=warnings,
    )
