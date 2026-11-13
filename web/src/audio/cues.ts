import type { Cue, Project } from "../types";
import type { LineSlot } from "./timeline";

/**
 * Cue lanes on the Cut board. Cues stay anchored to a script line; the lane
 * only decides which row draws them.
 */
export type CueLane = "music" | "sfx";

export interface CueSpan {
  cue: Cue;
  lane: CueLane;
  /** Intended start — `position_s`, or the anchor slot + offset — before clipping. */
  anchor: number;
  /** Visible clip bounds on the global timeline. */
  start: number;
  end: number;
  /** Decoded source length (0 until the file is loaded or when it is missing). */
  sourceDuration: number;
  /** Beds/music repeat their source until their end. */
  loops: boolean;
  /** True when the cue carries its own `position_s` instead of a line anchor. */
  freeform: boolean;
  /** True when it sits outside the dialogue-derived timeline entirely. */
  offTimeline: boolean;
}

/** Placeholder width for a cue whose source has not decoded yet. */
const UNKNOWN_SOURCE_S = 1.5;

/**
 * Width of the pinned stub an off-the-play cue is drawn as. Deliberately a few
 * seconds rather than the placeholder 1.5: at a whole-play zoom that is under ten
 * pixels, and a block you cannot click is a cue you cannot bring back.
 */
const OFF_PLAY_STUB_S = 3;

export function cueLaneOf(kind: Cue["kind"]): CueLane {
  return kind === "music" ? "music" : "sfx";
}

/**
 * Mirror `mixer.py::build_mix` cue placement.
 *
 * A cue with `position_s` is freeform: it starts there, a bed or score runs to
 * `end_s` or the end of the timeline, and no scene has anything to say about it.
 * A cue without one is anchored: the line's global slot start plus `offset_s`,
 * and beds and music clipped to the end of the anchored scene. A one-shot runs
 * for its source length either way.
 *
 * Spans are clamped to the timeline rather than dropped. A cue the user pushed
 * past the end still has to show up, or they cannot drag it back — the mixer
 * says it skipped such a cue, and the board should agree about the reason.
 */
export function computeCueSpans(
  project: Project,
  cues: Cue[],
  slots: Map<string, LineSlot>,
  total: number,
  sourceDurations: Record<string, number>,
  lineStarts?: Map<string, number>,
): CueSpan[] {
  if (total <= 0) return [];

  const sceneByLine = new Map<string, number>();
  for (const scene of project.script.scenes) {
    for (const line of scene.lines) sceneByLine.set(line.id, line.scene);
  }
  const sceneEnd = new Map<number, number>();
  for (const scene of project.script.scenes) {
    let end = 0;
    for (const line of scene.lines) {
      // Segments of one line stagger; the scene runs to the last sub-segment.
      for (const slot of slots.values()) {
        if (slot.lineId === line.id && slot.end > end) end = slot.end;
      }
      const ls = lineStarts?.get(line.id);
      if (ls != null && ls > end) end = ls;
    }
    sceneEnd.set(scene.id, end);
  }

  const spans: CueSpan[] = [];
  for (const cue of cues) {
    const sourceDuration = Math.max(0, sourceDurations[cue.sfx_id] ?? 0);
    const fallback = sourceDuration <= 0 ? UNKNOWN_SOURCE_S : sourceDuration;
    const freeform = cue.position_s != null;

    let start: number;
    let end: number;
    if (freeform) {
      start = cue.position_s ?? 0;
      if (cue.kind === "one_shot") {
        end = start + fallback;
      } else {
        // A score that owns its slot keeps it: no scene boundary clips it, and
        // without an explicit end it underlines the rest of the play.
        end = cue.end_s ?? total;
      }
    } else {
      start =
        (lineStarts?.get(cue.anchor_line_id ?? "") ??
          (() => {
            let first: number | null = null;
            for (const slot of slots.values()) {
              if (slot.lineId !== cue.anchor_line_id) continue;
              if (first == null || slot.start < first) first = slot.start;
            }
            return first ?? 0;
          })()) + cue.offset_s;
      end = start + fallback;
      if (cue.kind !== "one_shot") {
        const scene = cue.anchor_line_id != null ? sceneByLine.get(cue.anchor_line_id) : undefined;
        const sceneLimit = scene != null ? (sceneEnd.get(scene) ?? total) : total;
        end = cue.loop
          ? sceneLimit
          : start + Math.min(fallback, Math.max(0, sceneLimit - start));
      }
    }

    // Two ways a cue has nowhere to play, drawn differently because they are in
    // different places: past the end of the play it is pinned to the right edge
    // (its own slot would be off-canvas), while a cue squeezed out by its scene
    // limit sits where the user put it, short. Either way it is drawn at a fixed
    // stub width and marked, never hidden: a block you cannot see is a cue you
    // cannot recover, and a full-width block implies it is playing when it is not.
    const pastEnd = start > total - 0.05 || end <= 0;
    const noRoom = end - start <= 0.001;
    const offTimeline = pastEnd || noRoom;
    let drawn = Math.max(0, start);
    if (pastEnd) drawn = Math.max(0, total - OFF_PLAY_STUB_S);
    const width = offTimeline ? OFF_PLAY_STUB_S : end - start;
    const clippedEnd = Math.min(total, drawn + width);
    if (clippedEnd - drawn <= 0.001) continue;
    spans.push({
      cue,
      lane: cueLaneOf(cue.kind),
      anchor: start,
      start: drawn,
      end: clippedEnd,
      sourceDuration,
      loops: cue.kind !== "one_shot" && cue.loop,
      freeform,
      offTimeline,
    });
  }

  // Stable stacking: earlier cues draw first, later cues sit on top.
  spans.sort((a, b) => a.start - b.start);
  return spans;
}
