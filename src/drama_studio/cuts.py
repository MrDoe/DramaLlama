"""Shared cut-sheet domain logic (take-aware cuts).

Centralizes what was previously scattered across handlers.py and api.py:
- safe filenames for line clips,
- freezing legacy cuts (take_id=None) onto the previously selected take,
- ratio-scaled remap when a take disappears,
- validation of a cut window against its take.

The module is pure (no job queue, no store) so both handlers and the API
can share it. Callers translate ValueError/FileNotFoundError into
PermanentJobError / HTTPException.
"""
from __future__ import annotations

import re
from pathlib import Path

from .models import AlignedLine, AlignmentResult, Take


def safe_filename(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", value).strip("_") or "line"


def segment_filename(seg: AlignedLine) -> str:
    """Filename (without extension) for a segment's line clip."""
    base = seg.id.strip() if seg.id else ""
    if not base:
        base = seg.line_id
    return safe_filename(base)


def freeze_legacy_cuts(
    alignment: AlignmentResult | None, fallback: Take | None
) -> None:
    """Stamp take ids saved before multi-track cuts existed.

    Runs *before* the selected take changes: a legacy cut (take_id is None)
    must keep pointing at the audio it was measured on.
    """
    if alignment is None or fallback is None:
        return
    for line in alignment.lines:
        if line.take_id is None:
            line.take_id = fallback.id


def scale_window(
    start_s: float, end_s: float, old_duration: float, new_duration: float
) -> tuple[float, float]:
    """Scale a cut window by the duration ratio, clamped to the new take."""
    if old_duration <= 1e-6 or new_duration <= 0:
        return round(max(0.0, start_s), 3), round(max(0.0, end_s), 3)
    ratio = new_duration / old_duration
    start = max(0.0, min(new_duration, start_s * ratio))
    end = max(0.0, min(new_duration, end_s * ratio))
    return round(start, 3), round(end, 3)


def validate_cut(
    line_id: str, take: Take, start_s: float, end_s: float, duration_s: float
) -> None:
    """Raise ValueError when a cut window is invalid for its take."""
    if not (0.0 <= start_s < end_s <= duration_s + 1e-3):
        raise ValueError(
            f"cut for {line_id} out of range for take "
            f"{take.id} (0..{duration_s:.2f}s)"
        )


def remap_removed_cuts(
    alignment: AlignmentResult | None,
    removed: dict[str, Take],
    new_take: Take,
    directory: Path,
) -> None:
    """Re-point cuts whose take disappeared at ``new_take``.

    Windows are scaled by the duration ratio so trims survive a re-render,
    then the affected line clips are rewritten from the new take.
    Raises FileNotFoundError when the replacement audio is missing instead
    of silently leaving dead take ids behind.
    """
    if alignment is None or not removed:
        return
    if not any(line.take_id in removed for line in alignment.lines):
        return
    source = directory / new_take.file
    if not source.exists():
        raise FileNotFoundError(f"replacement take audio missing: {source}")

    # Local import to keep module load light (numpy/soundfile only on use).
    from .audio import read_mono, slice_mono, write_mono

    mono, sr = read_mono(source)
    new_duration = len(mono) / sr if sr else float(new_take.duration_s)
    out_dir = directory / "lines"
    out_dir.mkdir(parents=True, exist_ok=True)

    for line in alignment.lines:
        old = removed.get(line.take_id) if line.take_id else None
        if old is None:
            continue
        start, end = scale_window(
            line.start_s, line.end_s, float(old.duration_s), new_duration
        )
        line.take_id = new_take.id
        line.start_s = start
        line.end_s = end
        if end <= start:
            continue
        segment = slice_mono(mono, sr, start, end)
        if segment.size == 0:
            continue
        write_mono(out_dir / f"{segment_filename(line)}.wav", segment, sr)


def normalize_alignment(
    alignment: AlignmentResult | None, takes: list[Take]
) -> AlignmentResult | None:
    """Fill legacy take_id=None / id / position_s with sensible values.

    Makes old projects readable without mutating audio: pure in-memory fixup
    applied on load. Callers persist via store.save when they mutate further.
    """
    if alignment is None:
        return alignment
    fallback = next((take for take in takes if take.selected), None)
    if fallback is None and takes:
        fallback = takes[0]
    if fallback is None:
        return alignment
    PAUSE = 0.5
    cursor = 0.0
    for entry in alignment.lines:
        if entry.take_id is None:
            entry.take_id = fallback.id
        if not entry.id:
            entry.id = entry.line_id
        if entry.position_s is None:
            entry.position_s = round(cursor, 3)
        dur = max(0.0, (entry.end_s or 0) - (entry.start_s or 0))
        cursor += dur + PAUSE
    return alignment


def seed_positions(alignment: AlignmentResult, pause_s: float = 0.5) -> None:
    """Set position_s cumulatively from cut durations (legacy seed)."""
    cursor = 0.0
    for entry in alignment.lines:
        if entry.id is None or not entry.id:
            entry.id = entry.line_id
        if entry.position_s is None:
            entry.position_s = round(cursor, 3)
        dur = max(0.0, (entry.end_s or 0) - (entry.start_s or 0))
        cursor = entry.position_s + dur + pause_s


def pick_selected_take(takes: list[Take], selected_take_id: str | None) -> Take | None:
    """Honor StemRenderResult.selected_take_id, falling back to flags/order."""
    if not takes:
        return None
    if selected_take_id:
        match = next((take for take in takes if take.id == selected_take_id), None)
        if match is not None:
            return match
    flagged = next((take for take in takes if take.selected), None)
    if flagged is not None:
        return flagged
    return takes[0]
