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
  /** Anchor-line slot start + cue.offset_s, before clipping to the timeline. */
  anchor: number;
  /** Visible clip bounds on the global timeline. */
  start: number;
  end: number;
  /** Decoded source length (0 until the file is loaded or when it is missing). */
  sourceDuration: number;
  /** Beds/music repeat their source up to the scene end. */
  loops: boolean;
}

/** Placeholder width for a cue whose source has not decoded yet. */
const UNKNOWN_SOURCE_S = 1.5;

export function cueLaneOf(kind: Cue["kind"]): CueLane {
  return kind === "music" ? "music" : "sfx";
}

/**
 * Mirror `mixer.py::build_mix` cue placement.
 *
 * The anchor is the line's global slot start plus `offset_s`. A one-shot runs
 * for its source length. Beds and music run to the end of the anchored scene;
 * without `loop` they stop at the source length, and with `loop` the mixer
 * repeats the source. Everything is clipped to the dialogue-derived timeline,
 * exactly like `_place()` clips into the effects buffer.
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
    const anchorBase =
      lineStarts?.get(cue.anchor_line_id) ??
      (() => {
        let first: number | null = null;
        for (const slot of slots.values()) {
          if (slot.lineId !== cue.anchor_line_id) continue;
          if (first == null || slot.start < first) first = slot.start;
        }
        return first ?? 0;
      })();
    const anchor = anchorBase + cue.offset_s;
    const sourceDuration = Math.max(0, sourceDurations[cue.sfx_id] ?? 0);
    const unknown = sourceDuration <= 0;
    const fallback = unknown ? UNKNOWN_SOURCE_S : sourceDuration;
    let end = anchor + fallback;

    if (cue.kind !== "one_shot") {
      const scene = sceneByLine.get(cue.anchor_line_id);
      const sceneLimit = scene != null ? (sceneEnd.get(scene) ?? total) : total;
      if (cue.loop) {
        end = sceneLimit;
      } else {
        // Non-loop cue: shorter of source and the rest of the scene.
        end = anchor + Math.min(fallback, Math.max(0, sceneLimit - anchor));
      }
    }

    const start = Math.max(0, anchor);
    end = Math.min(total, Math.max(start, end));
    if (end - start <= 0.001) continue;
    spans.push({
      cue,
      lane: cueLaneOf(cue.kind),
      anchor,
      start,
      end,
      sourceDuration,
      loops: cue.kind !== "one_shot" && cue.loop,
    });
  }

  // Stable stacking: earlier cues draw first, later cues sit on top.
  spans.sort((a, b) => a.start - b.start);
  return spans;
}
