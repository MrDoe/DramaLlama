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

/** Stable segment key: explicit id, else the line id (one segment per line). */
export function segIdOf(cut: Pick<Cut, "id" | "line_id">): string {
  const id = (cut.id ?? "").trim();
  return id || cut.line_id;
}

/** Mixer tail, mirrors `TAIL_S` in `src/drama_studio/mixer.py`. */
const TAIL_S = 2.0;

export interface LineSlot {
  /** Segment key (`cut.id ?? cut.line_id`). One slot per segment. */
  segId: string;
  lineId: string;
  stemId: string | null;
  takeId: string | null;
  /** Global timeline position of this segment. */
  start: number;
  end: number;
  /** Cut window inside the stem take. */
  localStart: number;
  localEnd: number;
}

export interface Timeline {
  /** One slot per segment, keyed by `segIdOf(cut)`. */
  slots: Map<string, LineSlot>;
  /** First segment start per script line (cue anchor = mixer `line_starts`). */
  lineStarts: Map<string, number>;
  total: number;
}

/**
 * Lay every segment at its source-script position on the shared timeline.
 *
 * Mirrors `mixer.py::build_mix` + `cuts.layout_script_order`: script order
 * (Viktoria 1, Andreas 1, Grandmother 1, Viktoria 2, ...), `pause_ms` between
 * lines, `scene_gap_ms` on a scene change, segment duration = cut length
 * (speed-adjusted), plus `TAIL_S`. Explicit `position_s` (user-moved sections)
 * always wins — segments of the same line stagger left-to-right so a split
 * clip (cutting a marked section out leaves two adjacent segments) never
 * overlaps itself, exactly like the mixer.
 */
export function computeTimeline(
  project: Project,
  cutsByStem: Record<string, Cut[]>,
): Timeline {
  const segsByLine = new Map<string, { stemId: string; cut: Cut }[]>();
  for (const stem of project.stems) {
    for (const cut of cutsByStem[stem.id] ?? []) {
      const list = segsByLine.get(cut.line_id);
      const entry = { stemId: stem.id, cut };
      if (list) list.push(entry);
      else segsByLine.set(cut.line_id, [entry]);
    }
  }

  const pause = project.settings.pause_ms / 1000.0;
  const gap = project.settings.scene_gap_ms / 1000.0;
  const slots = new Map<string, LineSlot>();
  const lineStarts = new Map<string, number>();
  let t = 0;
  let prevScene: number | null = null;

  const segDuration = (cut: Cut): number => {
    const localStart = Number.isFinite(cut.start_s) ? Math.max(0, cut.start_s) : 0;
    const localEnd = Number.isFinite(cut.end_s) ? Math.max(0, cut.end_s) : 0;
    const raw = localEnd > localStart ? localEnd - localStart : 0;
    const speed = cut.speed && cut.speed > 0 ? cut.speed : 1;
    return raw / speed;
  };

  for (const scene of project.script.scenes) {
    for (const line of scene.lines) {
      if (prevScene !== null && line.scene !== prevScene) {
        t += gap;
      }
      const entries = segsByLine.get(line.id) ?? [];
      if (entries.length === 0) {
        // No cut: placeholder start for cue anchoring; pause still applies
        // (matches the pre-split behaviour of a zero-length slot).
        if (!lineStarts.has(line.id)) lineStarts.set(line.id, t);
        t += pause;
        prevScene = line.scene;
        continue;
      }
      const allScript = entries.every((e) => e.cut.position_s == null);
      if (allScript) {
        lineStarts.set(line.id, t);
        let cursor = t;
        for (const { stemId, cut } of entries) {
          const duration = segDuration(cut);
          const segId = segIdOf(cut);
          const localStart = Number.isFinite(cut.start_s) ? Math.max(0, cut.start_s) : 0;
          const localEnd = Number.isFinite(cut.end_s) ? Math.max(0, cut.end_s) : 0;
          slots.set(segId, {
            segId,
            lineId: line.id,
            stemId,
            takeId: cut.take_id ?? null,
            start: cursor,
            end: cursor + duration,
            localStart,
            localEnd,
          });
          cursor += duration;
        }
        t = cursor + pause;
      } else {
        // At least one explicit position: stagger same-line segments
        // left-to-right (mixer `_seen_lines`), script cursor follows the end.
        let prevEnd: number | null = null;
        let first = Infinity;
        for (const { stemId, cut } of entries) {
          const duration = segDuration(cut);
          const base: number = cut.position_s ?? prevEnd ?? t;
          const start: number = prevEnd == null ? Math.max(0, base) : Math.max(base, prevEnd);
          const segId = segIdOf(cut);
          const localStart = Number.isFinite(cut.start_s) ? Math.max(0, cut.start_s) : 0;
          const localEnd = Number.isFinite(cut.end_s) ? Math.max(0, cut.end_s) : 0;
          slots.set(segId, {
            segId,
            lineId: line.id,
            stemId,
            takeId: cut.take_id ?? null,
            start,
            end: start + duration,
            localStart,
            localEnd,
          });
          if (start < first) first = start;
          prevEnd = start + duration;
        }
        lineStarts.set(line.id, first === Infinity ? t : first);
        t = Math.max(t, (prevEnd ?? t) + pause);
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
  return { slots, lineStarts, total };
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
