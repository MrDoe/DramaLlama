import type { Project } from "../types";

export interface Cut {
  id?: string;
  line_id: string;
  /** Which stem take this cut window lives in (None = selected take). */
  take_id?: string | null;
  start_s: number;
  end_s: number;
  /** Freeform global timeline start. */
  position_s?: number;
}

/** Mixer tail, mirrors `TAIL_S` in `src/drama_studio/mixer.py`. */
const TAIL_S = 2.0;

export interface LineSlot {
  lineId: string;
  stemId: string | null;
  takeId: string | null;
  /** Global timeline position of this line's clip (source-script order). */
  start: number;
  end: number;
  /** Cut window inside the stem take. */
  localStart: number;
  localEnd: number;
}

export interface Timeline {
  slots: Map<string, LineSlot>;
  total: number;
}

/**
 * Lay every line at its source-script position on the shared timeline.
 *
 * Mirrors `mixer.py::build_mix`: script order, `pause_ms` between lines,
 * `scene_gap_ms` on scene change, clip duration = cut length, plus `TAIL_S`.
 * The mixer reads the durations from the cut line clips on disk; the editor
 * uses the same cut lengths before they are saved, so trimming updates the
 * layout exactly like the next mix rebuild will.
 */
export function computeTimeline(
  project: Project,
  cutsByStem: Record<string, Cut[]>,
): Timeline {
  const cutByLine = new Map<string, { stemId: string; cut: Cut }>();
  for (const stem of project.stems) {
    for (const cut of cutsByStem[stem.id] ?? []) {
      cutByLine.set(cut.line_id, { stemId: stem.id, cut });
    }
  }

  const pause = project.settings.pause_ms / 1000.0;
  const gap = project.settings.scene_gap_ms / 1000.0;
  const slots = new Map<string, LineSlot>();
  let t = 0;
  let prevScene: number | null = null;

  for (const scene of project.script.scenes) {
    for (const line of scene.lines) {
      if (prevScene !== null && line.scene !== prevScene) {
        // scene gap only in legacy mode; in freeform positions are explicit
        if (![...cutByLine.values()].some((e) => e.cut.position_s != null)) {
          t += gap;
        }
      }
      const entry = cutByLine.get(line.id);
      const localStart =
        entry && Number.isFinite(entry.cut.start_s) ? Math.max(0, entry.cut.start_s) : 0;
      const localEnd =
        entry && Number.isFinite(entry.cut.end_s) ? Math.max(0, entry.cut.end_s) : 0;
      const duration = entry && localEnd > localStart ? localEnd - localStart : 0;
      const freeform =
        entry != null && entry.cut.position_s != null;
      const start = freeform
        ? Math.max(0, entry!.cut.position_s as number)
        : t;
      slots.set(line.id, {
        lineId: line.id,
        stemId: entry?.stemId ?? null,
        takeId: entry?.cut.take_id ?? null,
        start,
        end: start + duration,
        localStart,
        localEnd,
      });
      if (freeform) {
        t = Math.max(t, start + duration + pause);
      } else {
        t += duration + pause;
      }
      prevScene = line.scene;
    }
  }

  // Total: max end + tail, but at least legacy tail.
  let maxEnd = 0;
  for (const slot of slots.values()) {
    if (slot.end > maxEnd) maxEnd = slot.end;
  }
  const total = Math.max(maxEnd, t) + TAIL_S;
  return { slots, total };
}
