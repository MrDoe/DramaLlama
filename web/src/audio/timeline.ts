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
  gain_db?: number;
  fade_in_s?: number;
  fade_out_s?: number;
  speed?: number;
  pitch_cents?: number;
  reverb_mix?: number;
  dereverb?: boolean;
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
 * Mirrors `mixer.py::build_mix` + `cuts.layout_script_order`: script order
 * (Viktoria 1, Andreas 1, Grandmother 1, Viktoria 2, ...), `pause_ms` between
 * lines, `scene_gap_ms` on scene change, clip duration = cut length, plus
 * `TAIL_S`. Explicit `position_s` (user-moved sections) always wins — the
 * legacy "any freeform position suppresses all scene gaps" global flag is
 * gone, so moving one clip no longer drops every gap.
 */
export function computeTimeline(
  project: Project,
  cutsByStem: Record<string, Cut[]>,
): Timeline {
  const cutByLine = new Map<string, { stemId: string; cut: Cut }>();
  for (const stem of project.stems) {
    for (const cut of cutsByStem[stem.id] ?? []) {
      // Last segment wins for the line slot; segments stack below would
      // overlap, so the editor draws only the primary segment per line.
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
        t += gap;
      }
      const entry = cutByLine.get(line.id);
      const localStart =
        entry && Number.isFinite(entry.cut.start_s) ? Math.max(0, entry.cut.start_s) : 0;
      const localEnd =
        entry && Number.isFinite(entry.cut.end_s) ? Math.max(0, entry.cut.end_s) : 0;
      const rawDuration = entry && localEnd > localStart ? localEnd - localStart : 0;
      const speed = entry?.cut.speed && entry.cut.speed > 0 ? entry.cut.speed : 1;
      const duration = rawDuration / speed;
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

/**
 * Speed-adjusted slot length for a cut (mirrors mixer resample pair:
 * speed compresses duration, pitch keeps duration).
 */
export function effectDuration(rawDuration: number, cut: Partial<Cut>): number {
  const speed = cut.speed && cut.speed > 0 ? cut.speed : 1;
  return rawDuration / speed;
}

/** Timeline slot end after per-region speed (pitch preserves duration). */
export function slotEndWithEffects(start: number, rawDuration: number, cut: Partial<Cut>): number {
  return start + effectDuration(rawDuration, cut);
}
