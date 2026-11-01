import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CaretLeft,
  CaretRight,
  FloppyDisk,
  MagnifyingGlassMinus,
  MagnifyingGlassPlus,
  Pause,
  Play,
  Scissors,
} from "@phosphor-icons/react";
import { api } from "../api";
import { SyncEngine, type ScheduleSegment } from "../audio/syncEngine";
import { computeTimeline, type Cut } from "../audio/timeline";
import type { Project, Stem, Take } from "../types";
import { formatDuration, speakerStyle } from "../utils";

interface Props {
  project: Project;
  onSaveCuts: (
    stem: Stem,
    cuts: {
      line_id: string;
      take_id?: string | null;
      start_s: number;
      end_s: number;
    }[],
    rebuildMix?: boolean,
  ) => void | Promise<void>;
  onSaveAll?: (
    stems: Record<
      string,
      { line_id: string; take_id?: string | null; start_s: number; end_s: number }[]
    >,
  ) => void | Promise<void>;
  onRefresh: () => void;
}

const COLORS = [
  "rgba(99, 102, 241, 0.25)",
  "rgba(16, 185, 129, 0.25)",
  "rgba(245, 158, 11, 0.25)",
  "rgba(236, 72, 153, 0.25)",
];

/** Minimum waveform height; grows to match the track card on the left. */
const TRACK_H = 120;
const RULER_H = 26;
const EDGE_PX = 10;

const round2 = (value: number) => Math.round(value * 100) / 100;
const clamp = (lo: number, hi: number, v: number) => Math.max(lo, Math.min(hi, v));

interface DragState {
  stemId: string;
  idx: number;
  mode: "trim-start" | "trim-end" | "move";
  /** Global slot start when the drag began (stable while trimming). */
  anchorGlobal: number;
  /** Local take time under the cursor when the drag began. */
  grabLocal: number;
  origStart: number;
  origEnd: number;
}

export function MultiTrackEditor({ project, onSaveCuts, onSaveAll, onRefresh }: Props) {
  const allLines = useMemo(
    () => project.script.scenes.flatMap((s) => s.lines),
    [project],
  );
  const lineById = useMemo(() => new Map(allLines.map((l) => [l.id, l])), [allLines]);
  const lineNo = useMemo(() => {
    const m = new Map<string, number>();
    let i = 0;
    for (const scene of project.script.scenes) {
      for (const line of scene.lines) m.set(line.id, ++i);
    }
    return m;
  }, [project]);

  const tracks = useMemo(
    () =>
      project.stems.map((stem) => {
        const take = stem.takes.find((t) => t.selected) ?? stem.takes[0] ?? null;
        return { stem, take };
      }),
    [project],
  );

  // Saved cuts (stable) vs in-progress edits.
  const savedCuts = useMemo(() => {
    const m: Record<string, Cut[]> = {};
    for (const stem of project.stems) {
      const al = project.alignments[stem.id];
      m[stem.id] = al
        ? al.lines.map((l) => ({
            id: l.id,
            line_id: l.line_id,
            take_id: l.take_id ?? null,
            start_s: l.start_s,
            end_s: l.end_s,
            position_s: l.position_s ?? undefined,
          }))
        : stem.line_ids.map((id) => ({
            line_id: id,
            take_id: null,
            start_s: 0,
            end_s: 0,
          }));
    }
    return m;
  }, [project]);

  const [pending, setPending] = useState<Record<string, Cut[]>>(savedCuts);
  const [dirty, setDirty] = useState<Record<string, boolean>>({});
  const [loaded, setLoaded] = useState<Record<string, { duration: number; peaks: number[] }>>({});
  const [loadError, setLoadError] = useState<string | null>(null);
  const [view, setView] = useState<{ start: number; end: number } | null>(null);
  const [playhead, setPlayhead] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [selected, setSelected] = useState<{ stemId: string; lineId: string } | null>(null);
  const [selection, setSelection] = useState<{
    stemId: string;
    lineId: string;
    segId: string;
    start: number;
    end: number;
  } | null>(null);
  const [muted, setMuted] = useState<Set<string>>(new Set());
  const [solo, setSolo] = useState<Set<string>>(new Set());
  const [volumes, setVolumes] = useState<Record<string, number>>({});
  const [saving, setSaving] = useState<Record<string, boolean>>({});
  const [savingAll, setSavingAll] = useState(false);
  const [trackH, setTrackH] = useState(TRACK_H);

  const timelineRef = useRef<HTMLDivElement>(null);
  const canvasRefs = useRef(new Map<string, HTMLCanvasElement>());
  const headRefs = useRef(new Map<string, HTMLDivElement>());
  const loadedUrl = useRef<Record<string, string>>({});
  const dragRef = useRef<DragState | null>(null);
  const selectionRef = useRef<{
    stemId: string;
    lineId: string;
    segId: string;
    anchorGlobal: number;
    clipStart: number;
    clipEnd: number;
  } | null>(null);
  const auditionRef = useRef<{ stemId: string; lineId: string; endGlobal: number } | null>(null);
  const viewRef = useRef(view);
  viewRef.current = view;
  const playheadRef = useRef(playhead);
  playheadRef.current = playhead;

  const engineRef = useRef<SyncEngine | null>(null);
  if (!engineRef.current) engineRef.current = new SyncEngine();
  const engine = engineRef.current;

  const stemById = useMemo(
    () => new Map(project.stems.map((stem) => [stem.id, stem])),
    [project],
  );

  /** Cut take when set, else the stem's selected take (legacy projects).
   * Returns null for an explicit but missing take id so dead references
   * surface as a warning instead of silently falling back. */
  const takeByIdFor = useCallback(
    (stem: Stem | null | undefined, takeId?: string | null): Take | null => {
      if (!stem) return null;
      if (takeId) {
        return stem.takes.find((take) => take.id === takeId) ?? null;
      }
      return stem.takes.find((take) => take.selected) ?? stem.takes[0] ?? null;
    },
    [],
  );

  /** Cuts whose take_id names a take that no longer exists. */
  const missingTakes = useMemo(() => {
    const missing: { stemId: string; lineId: string; takeId: string }[] = [];
    for (const stem of project.stems) {
      for (const cut of pending[stem.id] ?? []) {
        if (
          cut.take_id &&
          !stem.takes.some((take) => take.id === cut.take_id)
        ) {
          missing.push({ stemId: stem.id, lineId: cut.line_id, takeId: cut.take_id });
        }
      }
    }
    return missing;
  }, [project, pending]);

  // Warn on unload while edits are unsaved.
  useEffect(() => {
    if (Object.values(dirty).every((value) => !value)) return;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);

  const takeDurationOf = useCallback(
    (take: Take | null) =>
      take ? (loaded[take.id]?.duration ?? take.duration_s) : 0,
    [loaded],
  );

  // Sync non-dirty pending cuts from saved alignments.
  useEffect(() => {
    setPending((prev) => {
      const next: Record<string, Cut[]> = { ...prev };
      for (const stem of project.stems) {
        if (dirty[stem.id]) continue;
        next[stem.id] = savedCuts[stem.id] ?? [];
      }
      return next;
    });
  }, [project, savedCuts, dirty]);

  // Source-script layout: every line sits at its mixer-compatible slot.
  const timeline = useMemo(() => computeTimeline(project, pending), [project, pending]);
  const slots = timeline.slots;
  const total = timeline.total;

  const schedule = useMemo<ScheduleSegment[]>(() => {
    const segments: ScheduleSegment[] = [];
    for (const stem of project.stems) {
      for (const cut of pending[stem.id] ?? []) {
        const slot = slots.get(cut.line_id);
        if (!slot || slot.end <= slot.start) continue;
        const take = takeByIdFor(stem, cut.take_id);
        if (!take) continue;
        segments.push({
          stemId: stem.id,
          takeId: take.id,
          localStart: cut.start_s,
          localEnd: cut.end_s,
          start: slot.start,
          end: slot.end,
        });
      }
    }
    return segments;
  }, [project, pending, slots, takeByIdFor]);

  const audibleEnd = useMemo(
    () => schedule.reduce((max, seg) => Math.max(max, seg.end), 0),
    [schedule],
  );

  // Only decode takes the board actually draws: each stem's selected take
  // plus every take referenced by a cut. Historical generations stay
  // undecoded until a clip picks them.
  const neededTakes = useMemo(() => {
    const needed: { stem: Stem; take: Take }[] = [];
    const seen = new Set<string>();
    for (const stem of project.stems) {
      const selectedTake =
        stem.takes.find((take) => take.selected) ?? stem.takes[0] ?? null;
      if (selectedTake && !seen.has(selectedTake.id)) {
        seen.add(selectedTake.id);
        needed.push({ stem, take: selectedTake });
      }
      for (const cut of pending[stem.id] ?? []) {
        const take = takeByIdFor(stem, cut.take_id);
        if (take && !seen.has(take.id)) {
          seen.add(take.id);
          needed.push({ stem, take });
        }
      }
    }
    return needed;
  }, [project, pending, takeByIdFor]);

  // Load + decode each needed take once per file.
  useEffect(() => {
    let cancelled = false;
    // Evict decoded takes the board no longer draws (LRU in the engine caps
    // memory; prune drops ids that left the needed set entirely).
    engine.prune(neededTakes.map(({ take }) => take.id));
    (async () => {
      for (const { stem, take } of neededTakes) {
        if (!take.file) continue;
        if (loadedUrl.current[take.id] === take.file && engine.hasTrack(take.id)) continue;
        const url = api.fileUrl(project.id, take.file);
        try {
          const info = await engine.loadTrack(stem.id, take.id, url);
          if (cancelled) return;
          loadedUrl.current[take.id] = take.file;
          setLoaded((prev) => ({
            ...prev,
            [take.id]: { duration: info.duration, peaks: info.peaks },
          }));
        } catch (e) {
          if (!cancelled) {
            setLoadError(e instanceof Error ? e.message : "audio decode failed");
          }
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [engine, neededTakes, project.id]);

  // Evenly split stems that have a take but no alignment yet.
  useEffect(() => {
    setPending((prev) => {
      const next = { ...prev };
      let changed = false;
      for (const stem of project.stems) {
        const cuts = next[stem.id];
        const take =
          stem.takes.find((item) => item.selected) ?? stem.takes[0] ?? null;
        const dur = take ? (loaded[take.id]?.duration ?? take.duration_s) : 0;
        if (!take || dur <= 0 || !cuts || cuts.some((c) => c.end_s > 0)) continue;
        const step = dur / Math.max(1, stem.line_ids.length);
        next[stem.id] = stem.line_ids.map((id, i) => ({
          line_id: id,
          take_id: take.id,
          start_s: round2(i * step),
          end_s: round2((i + 1) * step),
        }));
        changed = true;
      }
      return changed ? next : prev;
    });
  }, [project, loaded]);

  // Mirror mute/solo/volume into live gains.
  useEffect(() => {
    engine.muted = muted;
    engine.solo = solo;
    engine.volumes = new Map(Object.entries(volumes));
    engine.applyGains();
  }, [engine, muted, solo, volumes]);

  useEffect(() => () => engine.pause(), [engine]);

  // Grow the waveform rows to the natural height of the track cards.
  useEffect(() => {
    const measure = () => {
      let max = TRACK_H;
      for (const el of headRefs.current.values()) {
        const rect = el.getBoundingClientRect();
        const style = getComputedStyle(el);
        const padding = parseFloat(style.paddingBottom) || 0;
        const borders =
          (parseFloat(style.borderTopWidth) || 0) +
          (parseFloat(style.borderBottomWidth) || 0);
        const last = el.lastElementChild?.getBoundingClientRect().bottom ?? rect.top;
        const height = Math.ceil(last - rect.top + padding + borders);
        if (height > max) max = height;
      }
      setTrackH(max);
    };
    measure();
    const observer = new ResizeObserver(measure);
    for (const el of headRefs.current.values()) observer.observe(el);
    window.addEventListener("resize", measure);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [tracks]);

  const viewStart = view?.start ?? 0;
  const viewEnd = view?.end ?? total;

  // Keep the playhead and zoom window inside the timeline when edits shorten it.
  useEffect(() => {
    if (playheadRef.current > total) {
      engine.setFrozen(total);
      setPlayhead(total);
    }
    const v = viewRef.current;
    if (v && v.end > total) {
      setView({ start: Math.max(0, Math.min(v.start, total - 0.001)), end: total });
    }
  }, [total, engine]);

  const seek = useCallback(
    (t: number) => {
      const c = clamp(0, total, t);
      auditionRef.current = null;
      if (engine.playing) {
        engine.seek(c, schedule);
      } else {
        engine.setFrozen(c);
        setPlayhead(c);
      }
    },
    [engine, total, schedule],
  );

  const togglePlay = useCallback(async () => {
    if (engine.playing) {
      engine.pause();
      auditionRef.current = null;
      setPlaying(false);
      setPlayhead(engine.getTime());
    } else {
      await engine.resume();
      const t =
        playheadRef.current >= Math.max(0, audibleEnd - 0.05) ? 0 : playheadRef.current;
      engine.play(t, schedule);
      setPlaying(engine.playing);
      if (!engine.playing) setPlayhead(t);
    }
  }, [engine, audibleEnd, schedule]);

  // rAF loop: mirror transport clock, stop at end/audition boundary, follow view.
  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    const tick = () => {
      let t = engine.getTime();
      const aud = auditionRef.current;
      if (aud && t >= aud.endGlobal) {
        engine.pause();
        auditionRef.current = null;
        setPlaying(false);
        setPlayhead(aud.endGlobal);
        return;
      }
      if (t >= total) {
        engine.pause();
        setPlaying(false);
        setPlayhead(total);
        return;
      }
      setPlayhead(t);
      const v = viewRef.current;
      if (v) {
        const span = v.end - v.start;
        if (t < v.start || t > v.end) {
          const s = clamp(0, Math.max(0, total - span), t - span / 2);
          setView({ start: s, end: s + span });
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, engine, total]);

  const zoomIn = useCallback(() => {
    const v = viewRef.current;
    const start = v?.start ?? 0;
    const end = v?.end ?? total;
    const center = (() => {
      if (!selected) return (start + end) / 2;
      const slot = slots.get(selected.lineId);
      if (!slot) return (start + end) / 2;
      return (slot.start + slot.end) / 2;
    })();
    const span = Math.max(0.5, (end - start) / 2);
    const s = Math.max(0, center - span / 2);
    const e = Math.min(total, s + span);
    setView({ start: Math.max(0, e - span), end: e });
  }, [total, selected, slots]);

  const zoomOut = useCallback(() => {
    const v = viewRef.current;
    const start = v?.start ?? 0;
    const end = v?.end ?? total;
    const center = (start + end) / 2;
    const span = Math.min(total, (end - start) * 2);
    if (span >= total) {
      setView(null);
      return;
    }
    const s = clamp(0, total - span, center - span / 2);
    setView({ start: s, end: s + span });
  }, [total]);

  const zoomToSelection = useCallback(() => {
    if (!selected) return;
    const slot = slots.get(selected.lineId);
    if (!slot) return;
    const pad = Math.max(0.5, (slot.end - slot.start) * 0.5);
    setView({
      start: Math.max(0, slot.start - pad),
      end: Math.min(total, slot.end + pad),
    });
  }, [selected, slots, total]);

  const pan = useCallback(
    (direction: -1 | 1) => {
      const v = viewRef.current;
      if (!v) return;
      const span = v.end - v.start;
      const s = clamp(0, total - span, v.start + span * 0.25 * direction);
      setView({ start: s, end: s + span });
    },
    [total],
  );

  // Shared wheel: pan zoomed view, Ctrl/Meta+wheel zooms at cursor,
  // plain wheel-up at fit zooms in at the cursor.
  useEffect(() => {
    const root = timelineRef.current;
    if (!root || total <= 0) return;
    const onWheel = (e: WheelEvent) => {
      const canvas = (e.target as HTMLElement).closest("canvas[data-seek]");
      if (!canvas) return;
      e.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const w = Math.max(1, rect.width);
      const v = viewRef.current;
      const start = v?.start ?? 0;
      const end = v?.end ?? total;
      const span = Math.max(0.001, end - start);
      const cursorT = start + ((e.clientX - rect.left) / w) * span;
      if (e.ctrlKey || e.metaKey) {
        const factor = e.deltaY > 0 ? 1.3 : 1 / 1.3;
        const newSpan = clamp(0.5, total, span * factor);
        if (newSpan >= total) {
          setView(null);
          return;
        }
        const ratio = span > 0 ? (cursorT - start) / span : 0.5;
        const s = cursorT - ratio * newSpan;
        setView({ start: clamp(0, total - newSpan, s), end: clamp(0, total, clamp(0, total - newSpan, s) + newSpan) });
        return;
      }
      if (v == null) {
        if (e.deltaY >= 0) return;
        const newSpan = Math.max(0.5, span / 2);
        const ratio = (cursorT - start) / span;
        const s = cursorT - ratio * newSpan;
        setView({ start: clamp(0, total - newSpan, s), end: clamp(0, total, clamp(0, total - newSpan, s) + newSpan) });
        return;
      }
      const deltaT =
        Math.abs(e.deltaX) > Math.abs(e.deltaY)
          ? (e.deltaX / w) * span
          : Math.sign(e.deltaY) * span * 0.15;
      const s = clamp(0, total - span, start + deltaT);
      setView({ start: s, end: s + span });
    };
    root.addEventListener("wheel", onWheel, { passive: false });
    return () => root.removeEventListener("wheel", onWheel);
  }, [total]);

  // ---- drawing -----------------------------------------------------------

  const drawOne = useCallback(
    (
      canvas: HTMLCanvasElement,
      kind: { ruler: true } | { ruler: false; stemId: string; trackIdx: number },
    ) => {
      const root = timelineRef.current;
      if (!root) return;
      const dpr = window.devicePixelRatio || 1;
      const w = Math.max(50, root.clientWidth - 196);
      const h = kind.ruler ? RULER_H : trackH;
      canvas.width = w * dpr;
      canvas.height = h * dpr;
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.scale(dpr, dpr);
      ctx.fillStyle = "#0f0f1a";
      ctx.fillRect(0, 0, w, h);

      const v = viewRef.current;
      const start = v?.start ?? 0;
      const end = v?.end ?? total;
      const span = Math.max(0.001, end - start);
      const toX = (t: number) => ((t - start) / span) * w;

      // adaptive ticks
      const targetTicks = 8;
      const rawStep = span / targetTicks;
      const mag = Math.pow(10, Math.floor(Math.log10(rawStep)));
      const norm = rawStep / mag;
      const nice = norm >= 5 ? 5 : norm >= 2 ? 2 : 1;
      const tickStep = nice * mag;
      ctx.fillStyle = "rgba(255,255,255,0.55)";
      ctx.font = "10px sans-serif";
      const firstTick = Math.ceil(start / tickStep) * tickStep;
      for (let t = firstTick; t <= end; t += tickStep) {
        const x = toX(t);
        ctx.fillRect(x, kind.ruler ? 14 : h - 12, 1, kind.ruler ? 8 : 6);
        ctx.fillText(`${t.toFixed(tickStep < 1 ? 1 : 0)}s`, x + 3, kind.ruler ? 12 : h - 14);
      }

      if (kind.ruler) {
        const px = toX(playheadRef.current);
        if (px >= 0 && px <= w) {
          ctx.strokeStyle = "#fb7185";
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(Math.round(px) + 0.5, 0);
          ctx.lineTo(Math.round(px) + 0.5, h);
          ctx.stroke();
        }
        return;
      }

      const { stemId, trackIdx } = kind;
      const stem = stemById.get(stemId) ?? null;
      const mid = h / 2;

      // Regions live at their source-script slots; waveform inside each region
      // is the cut window of its own take, mapped 1:1 into that slot.
      const cuts = pending[stemId] ?? [];
      cuts.forEach((cut, idx) => {
        const slot = slots.get(cut.line_id);
        if (!slot || slot.end <= slot.start) return;
        if (slot.end < start || slot.start > end) return;
        const take = takeByIdFor(stem, cut.take_id);
        const info = take ? loaded[take.id] : undefined;
        const takeDur = takeDurationOf(take);
        if (!take || !info || takeDur <= 0) return;
        const peaks = info.peaks;
        const x0 = toX(slot.start);
        const x1 = toX(slot.end);
        const wpx = Math.max(1, x1 - x0);
        const isSel = selected?.stemId === stemId && selected.lineId === cut.line_id;

        ctx.fillStyle = COLORS[(trackIdx + idx) % COLORS.length];
        ctx.fillRect(x0, 0, wpx, h);

        const len = cut.end_s - cut.start_s;
        if (len > 0 && peaks.length > 0) {
          const local0 = Math.max(0, cut.start_s);
          const local1 = Math.min(takeDur, cut.end_s);
          if (local1 > local0) {
            const i0 = Math.max(0, Math.floor((local0 / takeDur) * peaks.length));
            const i1 = Math.min(peaks.length, Math.ceil((local1 / takeDur) * peaks.length));
            ctx.strokeStyle = isSel ? "rgba(254, 243, 199, 0.85)" : "rgba(226, 232, 240, 0.55)";
            ctx.lineWidth = 1;
            ctx.beginPath();
            for (let i = i0; i < i1; i++) {
              const localT = ((i + 0.5) / peaks.length) * takeDur;
              const x = x0 + ((localT - cut.start_s) / len) * wpx;
              if (x < x0 || x > x1) continue;
              const amp = peaks[i] * (h * 0.42);
              ctx.moveTo(x, mid - amp);
              ctx.lineTo(x, mid + amp);
            }
            ctx.stroke();
          }
        }

        ctx.strokeStyle = isSel ? "#fbbf24" : "rgba(255,255,255,0.9)";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(Math.round(x0) + 0.5, 0);
        ctx.lineTo(Math.round(x0) + 0.5, h);
        ctx.moveTo(Math.round(x1) + 0.5, 0);
        ctx.lineTo(Math.round(x1) + 0.5, h);
        ctx.stroke();
        if (isSel) {
          ctx.strokeStyle = "rgba(251, 191, 36, 0.9)";
          ctx.strokeRect(x0 + 0.5, 0.5, wpx, h - 1);
        }
        // In-clip selection highlight.
        if (
          selection &&
          selection.stemId === stemId &&
          selection.lineId === cut.line_id
        ) {
          const sx0 = toX(Math.max(slot.start, selection.start));
          const sx1 = toX(Math.min(slot.end, selection.end));
          if (sx1 > sx0) {
            ctx.fillStyle = "rgba(251, 191, 36, 0.35)";
            ctx.fillRect(sx0, 0, sx1 - sx0, h);
            ctx.strokeStyle = "rgba(251, 191, 36, 0.9)";
            ctx.strokeRect(sx0 + 0.5, 0.5, sx1 - sx0 - 1, h - 1);
          }
        }
        const takeNo = stem
          ? stem.takes.findIndex((item) => item.id === take.id) + 1
          : 0;
        const showTake = (stem?.takes.length ?? 0) > 1 && takeNo > 0;
        const label = `${lineNo.get(cut.line_id) ?? "?"} ${cut.line_id}${
          showTake ? ` · T${takeNo}` : ""
        }`;
        ctx.font = isSel ? "600 12px sans-serif" : "12px sans-serif";
        const tw = ctx.measureText(label).width;
        const row = idx % 2;
        const ly = 14 + row * 18;
        const lx = Math.min(Math.max(4, x0 + 6), Math.max(4, w - tw - 10));
        ctx.fillStyle = isSel ? "rgba(251,191,36,0.9)" : "rgba(0,0,0,0.65)";
        const pad = 3;
        ctx.fillRect(lx - pad, ly - 12, tw + pad * 2, 16);
        ctx.fillStyle = isSel ? "#000" : "#fff";
        ctx.fillText(label, lx, ly);
      });

      // playhead: 1px rose line
      const px = toX(playheadRef.current);
      if (px >= 0 && px <= w) {
        ctx.strokeStyle = "#fb7185";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(Math.round(px) + 0.5, 0);
        ctx.lineTo(Math.round(px) + 0.5, h);
        ctx.stroke();
      }
    },
    [
      total,
      loaded,
      pending,
      selected,
      selection,
      slots,
      trackH,
      lineNo,
      stemById,
      takeByIdFor,
      takeDurationOf,
    ],
  );

  useEffect(() => {
    const ruler = canvasRefs.current.get("__ruler__");
    if (ruler) drawOne(ruler, { ruler: true });
    tracks.forEach(({ stem }, i) => {
      const c = canvasRefs.current.get(stem.id);
      if (c) drawOne(c, { ruler: false, stemId: stem.id, trackIdx: i });
    });
    const onResize = () => {
      const r = canvasRefs.current.get("__ruler__");
      if (r) drawOne(r, { ruler: true });
      tracks.forEach(({ stem }, i) => {
        const c = canvasRefs.current.get(stem.id);
        if (c) drawOne(c, { ruler: false, stemId: stem.id, trackIdx: i });
      });
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [drawOne, tracks, view, playhead]);

  // ---- interaction -------------------------------------------------------

  const toGlobal = useCallback(
    (canvas: HTMLCanvasElement, clientX: number) => {
      const rect = canvas.getBoundingClientRect();
      const v = viewRef.current;
      const start = v?.start ?? 0;
      const end = v?.end ?? total;
      return start + ((clientX - rect.left) / Math.max(1, rect.width)) * (end - start);
    },
    [total],
  );

  const findEdge = useCallback(
    (stemId: string, canvas: HTMLCanvasElement, clientX: number) => {
      const rect = canvas.getBoundingClientRect();
      const w = Math.max(1, rect.width);
      const v = viewRef.current;
      const start = v?.start ?? 0;
      const end = v?.end ?? total;
      const span = Math.max(0.001, end - start);
      const toX = (g: number) => ((g - start) / span) * w;
      const cuts = pending[stemId] ?? [];
      const x = clientX - rect.left;
      for (let i = 0; i < cuts.length; i++) {
        const slot = slots.get(cuts[i].line_id);
        if (!slot || slot.end <= slot.start) continue;
        const xs = toX(slot.start);
        const xe = toX(slot.end);
        if (Math.abs(x - xs) <= EDGE_PX) return { idx: i, edge: "trim-start" as const };
        if (Math.abs(x - xe) <= EDGE_PX) return { idx: i, edge: "trim-end" as const };
      }
      return null;
    },
    [pending, slots, total],
  );

  const findRegion = useCallback(
    (stemId: string, globalT: number) => {
      const cuts = pending[stemId] ?? [];
      for (const c of cuts) {
        const slot = slots.get(c.line_id);
        if (!slot || slot.end <= slot.start) continue;
        if (globalT >= slot.start && globalT <= slot.end) return c.line_id;
      }
      return null;
    },
    [pending, slots],
  );

  const commitCuts = useCallback(
    (stemId: string, cuts: Cut[]) => {
      setPending((prev) => ({ ...prev, [stemId]: cuts }));
      setDirty((prev) => ({ ...prev, [stemId]: true }));
    },
    [],
  );

  /** Move one region to another take, scaling its window by the duration ratio. */
  const switchCutTake = useCallback(
    (stem: Stem, cut: Cut, takeId: string) => {
      const next = stem.takes.find((take) => take.id === takeId);
      if (!next) return;
      const prev = takeByIdFor(stem, cut.take_id);
      const prevDur = takeDurationOf(prev);
      const nextDur = takeDurationOf(next) || next.duration_s;
      const cuts = (pending[stem.id] ?? []).map((item) => ({ ...item }));
      const idx = cuts.findIndex((item) => item.line_id === cut.line_id);
      if (idx < 0) return;
      const cur = cuts[idx];
      let start = cur.start_s;
      let end = cur.end_s;
      if (prevDur > 0 && nextDur > 0) {
        const ratio = nextDur / prevDur;
        start = cur.start_s * ratio;
        end = cur.end_s * ratio;
      } else if (nextDur > 0) {
        // No usable source window: fall back to an even split for this line.
        const count = Math.max(1, stem.line_ids.length);
        const pos = Math.max(0, stem.line_ids.indexOf(cur.line_id));
        start = (nextDur / count) * pos;
        end = (nextDur / count) * (pos + 1);
      }
      start = clamp(0, Math.max(0, nextDur), start);
      end = clamp(0, Math.max(0, nextDur), end);
      if (nextDur > 0 && end - start < 0.05) {
        end = Math.min(nextDur, start + 0.05);
        start = Math.max(0, end - 0.05);
      }
      cuts[idx] = {
        ...cur,
        take_id: next.id,
        start_s: round2(start),
        end_s: round2(end),
      };
      commitCuts(stem.id, cuts);
    },
    [pending, takeByIdFor, takeDurationOf, commitCuts],
  );

  const onTrackMouseDown = (e: React.MouseEvent, stemId: string) => {
    if (e.button !== 0) return;
    const canvas = e.currentTarget as HTMLCanvasElement;
    const globalT = toGlobal(canvas, e.clientX);
    const edge = findEdge(stemId, canvas, e.clientX);
    if (edge) {
      const cuts = pending[stemId] ?? [];
      const cut = cuts[edge.idx];
      const slot = slots.get(cut.line_id);
      if (!slot) return;
      dragRef.current = {
        stemId,
        idx: edge.idx,
        mode: edge.edge,
        anchorGlobal: slot.start,
        grabLocal: cut.start_s + (globalT - slot.start),
        origStart: cut.start_s,
        origEnd: cut.end_s,
      };
      setSelected({ stemId, lineId: cut.line_id });
      return;
    }
    const region = findRegion(stemId, globalT);
    const cuts = pending[stemId] ?? [];
    const idx = region
      ? cuts.findIndex((c) => c.line_id === region)
      : -1;
    const cut = idx >= 0 ? cuts[idx] : null;
    const stem = stemById.get(stemId) ?? null;
    const dur = takeDurationOf(takeByIdFor(stem, cut?.take_id));
    const slot = region ? slots.get(region) : null;
    if (region && cut && dur > 0 && slot) {
      if (e.altKey) {
        // Alt-drag: slip the cut window inside the take (old behavior).
        dragRef.current = {
          stemId,
          idx,
          mode: "move",
          anchorGlobal: slot.start,
          grabLocal: cut.start_s + (globalT - slot.start),
          origStart: cut.start_s,
          origEnd: cut.end_s,
        };
        setSelected({ stemId, lineId: region });
        return;
      }
      // Plain LMB drag: select a sub-range inside the clip.
      selectionRef.current = {
        stemId,
        lineId: region,
        segId: cut.id ?? cut.line_id,
        anchorGlobal: globalT,
        clipStart: slot.start,
        clipEnd: slot.end,
      };
      setSelection({ stemId, lineId: region, segId: cut.id ?? cut.line_id, start: globalT, end: globalT });
      setSelected({ stemId, lineId: region });
      return;
    }
    // Empty space: LMB seeks the shared playhead.
    if (total > 0) seek(clamp(0, total, globalT));
    setSelected(region ? { stemId, lineId: region } : null);
  };

  const onTrackMouseMove = (e: React.MouseEvent, stemId: string) => {
    const canvas = e.currentTarget as HTMLCanvasElement;
    const sel = selectionRef.current;
    if (sel && sel.stemId === stemId) {
      const g = clamp(sel.clipStart, sel.clipEnd, toGlobal(canvas, e.clientX));
      const s = Math.min(sel.anchorGlobal, g);
      const en = Math.max(sel.anchorGlobal, g);
      setSelection({ stemId, lineId: sel.lineId, segId: sel.segId, start: s, end: en });
      canvas.style.cursor = "text";
      return;
    }
    const drag = dragRef.current;
    if (!drag || drag.stemId !== stemId) {
      if (!drag) {
        const edge = findEdge(stemId, canvas, e.clientX);
        if (edge) {
          canvas.style.cursor = "ew-resize";
          return;
        }
        const region = findRegion(stemId, toGlobal(canvas, e.clientX));
        const isSelBody =
          region && selected?.stemId === stemId && selected.lineId === region;
        canvas.style.cursor = isSelBody || region ? "move" : "default";
      }
      return;
    }
    const local = drag.origStart + (toGlobal(canvas, e.clientX) - drag.anchorGlobal);
    const cuts = (pending[stemId] ?? []).map((c) => ({ ...c }));
    const cut = cuts[drag.idx];
    if (!cut) return;
    const stem = stemById.get(stemId) ?? null;
    const dur = takeDurationOf(takeByIdFor(stem, cut.take_id));
    if (dur <= 0) return;
    if (drag.mode === "trim-start") {
      cut.start_s = round2(clamp(0, cut.end_s - 0.05, local));
      canvas.style.cursor = "ew-resize";
    } else if (drag.mode === "trim-end") {
      cut.end_s = round2(clamp(cut.start_s + 0.05, dur, local));
      canvas.style.cursor = "ew-resize";
    } else {
      const len = drag.origEnd - drag.origStart;
      const shiftMin = -drag.origStart;
      const shiftMax = dur - drag.origEnd;
      const shift = clamp(shiftMin, shiftMax, local - drag.grabLocal);
      cut.start_s = round2(drag.origStart + shift);
      cut.end_s = round2(drag.origStart + shift + len);
      canvas.style.cursor = "move";
    }
    commitCuts(stemId, cuts);
  };

  const onTrackMouseUp = () => {
    if (selectionRef.current) {
      const cur = selection;
      if (!cur || Math.abs(cur.end - cur.start) < 0.05) {
        setSelection(null);
      }
      selectionRef.current = null;
    }
    dragRef.current = null;
  };

  const onRulerMouseDown = (e: React.MouseEvent) => {
    if (e.button !== 0) return;
    seek(clamp(0, total, toGlobal(e.currentTarget as HTMLCanvasElement, e.clientX)));
  };

  const onCardKeyDown = (e: React.KeyboardEvent) => {
    const target = e.target as HTMLElement | null;
    if (target?.closest("input, select, textarea, button, a, [contenteditable='true']")) return;
    if (e.key === "Backspace" && selection) {
      e.preventDefault();
      // Delete selected sub-range: split clip into two adjacent segments.
      const { stemId, lineId, segId, start, end } = selection;
      const cuts = (pending[stemId] ?? []).map((c) => ({ ...c }));
      const idx = cuts.findIndex((c) => (c.id ?? c.line_id) === segId && c.line_id === lineId);
      if (idx < 0) {
        setSelection(null);
        return;
      }
      const cut = cuts[idx];
      const slot = slots.get(lineId);
      if (!slot || slot.end <= slot.start) {
        setSelection(null);
        return;
      }
      const dur = slot.end - slot.start;
      const len = cut.end_s - cut.start_s;
      if (len <= 0 || dur <= 0) {
        setSelection(null);
        return;
      }
      // Map global selection to local take times.
      const localSelStart = cut.start_s + ((start - slot.start) / dur) * len;
      const localSelEnd = cut.start_s + ((end - slot.start) / dur) * len;
      const ls = Math.max(cut.start_s, Math.min(cut.end_s, localSelStart));
      const le = Math.max(cut.start_s, Math.min(cut.end_s, localSelEnd));
      if (le - ls < 0.01) {
        setSelection(null);
        return;
      }
      const newCuts: Cut[] = [];
      // segment before selection
      if (ls - cut.start_s >= 0.05) {
        newCuts.push({
          ...cut,
          id: cut.id,
          start_s: round2(cut.start_s),
          end_s: round2(ls),
          position_s: cut.position_s,
        });
      }
      // segment after selection: position adjacent to previous (no gap)
      if (cut.end_s - le >= 0.05) {
        const seg: Cut = {
          ...cut,
          id: `${cut.id ?? cut.line_id}__${Date.now()}`,
          line_id: cut.line_id,
          take_id: cut.take_id,
          start_s: round2(le),
          end_s: round2(cut.end_s),
          position_s:
            (cut.position_s ?? slot.start) + (ls - cut.start_s),
        };
        newCuts.push(seg);
      }
      // Replace the original segment with the new ones.
      const replaced = [
        ...cuts.slice(0, idx),
        ...newCuts,
        ...cuts.slice(idx + 1),
      ];
      commitCuts(stemId, replaced);
      setSelection(null);
      return;
    }
    if (e.code !== "Space" && e.key !== " ") return;
    e.preventDefault();
    void togglePlay();
  };

  // ---- save --------------------------------------------------------------

  const invalidByStem = useMemo(() => {
    const m: Record<string, boolean> = {};
    for (const stem of project.stems) {
      m[stem.id] = (pending[stem.id] ?? []).some((cut) => {
        const take = takeByIdFor(stem, cut.take_id);
        const dur = takeDurationOf(take);
        return !(
          take &&
          Number.isFinite(cut.start_s) &&
          Number.isFinite(cut.end_s) &&
          cut.start_s >= 0 &&
          cut.start_s < cut.end_s &&
          (dur <= 0 || cut.end_s <= dur + 1e-3)
        );
      });
    }
    return m;
  }, [project, pending, takeByIdFor, takeDurationOf]);

  const saveStem = useCallback(
    async (stem: Stem) => {
      if (invalidByStem[stem.id] || saving[stem.id]) return;
      setSaving((p) => ({ ...p, [stem.id]: true }));
      try {
        await onSaveCuts(stem, pending[stem.id] ?? []);
        setDirty((p) => ({ ...p, [stem.id]: false }));
      } finally {
        setSaving((p) => ({ ...p, [stem.id]: false }));
      }
    },
    [invalidByStem, saving, onSaveCuts, pending],
  );

  const dirtyCount = tracks.filter((t) => dirty[t.stem.id] && !invalidByStem[t.stem.id]).length;

  const saveAll = useCallback(async () => {
    if (savingAll) return;
    setSavingAll(true);
    try {
      const dirtyStems = tracks.filter(
        (t) => dirty[t.stem.id] && !invalidByStem[t.stem.id],
      );
      if (dirtyStems.length === 0) return;
      if (onSaveAll) {
        // Single batch: one mix rebuild instead of one per stem.
        const payload: Record<string, Cut[]> = {};
        for (const t of dirtyStems) payload[t.stem.id] = pending[t.stem.id] ?? [];
        await onSaveAll(payload);
        setDirty((p) => {
          const next = { ...p };
          for (const t of dirtyStems) next[t.stem.id] = false;
          return next;
        });
      } else {
        for (const t of dirtyStems) {
          await onSaveCuts(t.stem, pending[t.stem.id] ?? []);
          setDirty((p) => ({ ...p, [t.stem.id]: false }));
        }
      }
    } finally {
      setSavingAll(false);
    }
  }, [savingAll, tracks, dirty, invalidByStem, onSaveCuts, onSaveAll, pending]);

  const updateCut = (stemId: string, index: number, field: "start_s" | "end_s", value: number) => {
    const cuts = (pending[stemId] ?? []).map((c) => ({ ...c }));
    cuts[index] = {
      ...cuts[index],
      [field]: Number.isFinite(value) ? value : cuts[index][field],
    };
    commitCuts(stemId, cuts);
  };

  const playRegion = useCallback(
    async (stemId: string, lineId: string) => {
      const cut = (pending[stemId] ?? []).find((c) => c.line_id === lineId);
      if (!cut) return;
      const aud = auditionRef.current;
      if (aud && aud.stemId === stemId && aud.lineId === lineId && engine.playing) {
        engine.pause();
        auditionRef.current = null;
        setPlaying(false);
        setPlayhead(engine.getTime());
        return;
      }
      await engine.resume();
      const slot = slots.get(cut.line_id);
      const take = takeByIdFor(stemById.get(stemId), cut.take_id);
      if (!slot || slot.end <= slot.start || !take) return;
      auditionRef.current = { stemId, lineId, endGlobal: slot.end };
      engine.play(
        slot.start,
        [
          {
            stemId,
            takeId: take.id,
            localStart: cut.start_s,
            localEnd: cut.end_s,
            start: slot.start,
            end: slot.end,
          },
        ],
        { onlyStemId: stemId },
      );
      setPlaying(engine.playing);
      setPlayhead(slot.start);
      if (!engine.playing) auditionRef.current = null;
    },
    [engine, pending, slots, stemById, takeByIdFor],
  );

  const toggleMute = (stemId: string) =>
    setMuted((p) => {
      const n = new Set(p);
      if (n.has(stemId)) n.delete(stemId);
      else n.add(stemId);
      return n;
    });
  const toggleSolo = (stemId: string) =>
    setSolo((p) => {
      const n = new Set(p);
      if (n.has(stemId)) n.delete(stemId);
      else n.add(stemId);
      return n;
    });

  if (project.stems.length === 0) {
    return <div className="empty-list">Build stems first, then cut them here.</div>;
  }

  const zoomed = view != null;
  const viewLabel = `${viewStart.toFixed(1)}–${viewEnd.toFixed(1)}s of ${total.toFixed(1)}s`;
  const selCut =
    selected != null
      ? (pending[selected.stemId] ?? []).find((c) => c.line_id === selected.lineId) ?? null
      : null;
  const selStem = selected != null ? tracks.find((t) => t.stem.id === selected.stemId)?.stem ?? null : null;
  const selLine = selected != null ? lineById.get(selected.lineId) : undefined;
  const selTake = selStem ? takeByIdFor(selStem, selCut?.take_id) : null;
  const auditioning =
    auditionRef.current != null &&
    playing &&
    selected != null &&
    auditionRef.current.stemId === selected.stemId &&
    auditionRef.current.lineId === selected.lineId;

  let lastScene: number | null = null;

  return (
    <div>
      <section className="panel stems-toolbar">
        <div>
          <h2 className="panel-title">
            <Scissors size={15} weight="fill" />
            Cut board
          </h2>
          <p className="panel-sub">
            Every line sits at its source-script position on the shared timeline — the same order
            and spacing the mix uses. Drag edges to trim, drag a region body to slip the audio
            inside it.
          </p>
        </div>
        <div className="panel-actions">
          <button type="button" className="button button-ghost button-sm" onClick={onRefresh}>
            Refresh
          </button>
          <button
            type="button"
            className="button button-primary button-sm"
            disabled={savingAll || dirtyCount === 0}
            onClick={() => void saveAll()}
          >
            {savingAll ? <span className="spinner" /> : <FloppyDisk size={14} />}
            {savingAll ? "Saving…" : `Save all (${dirtyCount})`}
          </button>
        </div>
      </section>

      {missingTakes.length > 0 ? (
        <div className="render-error" role="alert">
          {missingTakes.length} clip{missingTakes.length === 1 ? "" : "s"} point at a
          deleted take ({missingTakes.slice(0, 3).map((m) => m.takeId.slice(0, 8)).join(", ")}
          {missingTakes.length > 3 ? "…" : ""}). Reassign the track or re-render.
        </div>
      ) : null}

      <div
        className="panel mt-timeline"
        onKeyDown={onCardKeyDown}
        tabIndex={0}
        aria-label="Multitrack timeline. Left click sets the playhead, Space plays or pauses all tracks."
      >
        {/* transport */}
        <div className="mt-toolbar">
          <button
            type="button"
            className="button button-primary button-sm"
            onClick={() => void togglePlay()}
            title="Play or pause all tracks (Space)"
            aria-label={playing ? "Pause all tracks" : "Play all tracks"}
          >
            {playing ? <Pause size={14} /> : <Play size={14} />}
          </button>
          <span className="mt-time">
            {playhead.toFixed(1)}s / {total.toFixed(1)}s
          </span>
          <span className="mt-sep" />
          <span className="mt-zoom-label">Zoom:</span>
          <button
            type="button"
            className="button button-ghost button-sm"
            onClick={zoomIn}
            disabled={total <= 0}
            title="Zoom in (centers on the selected region)"
            aria-label="Zoom in"
          >
            <MagnifyingGlassPlus size={14} />
          </button>
          <button
            type="button"
            className="button button-ghost button-sm"
            onClick={zoomOut}
            disabled={total <= 0 || !zoomed}
            title="Zoom out"
            aria-label="Zoom out"
          >
            <MagnifyingGlassMinus size={14} />
          </button>
          <button
            type="button"
            className="button button-ghost button-sm"
            onClick={() => pan(-1)}
            disabled={!zoomed}
            title="Pan left"
            aria-label="Pan left"
          >
            <CaretLeft size={14} />
          </button>
          <button
            type="button"
            className="button button-ghost button-sm"
            onClick={() => pan(1)}
            disabled={!zoomed}
            title="Pan right"
            aria-label="Pan right"
          >
            <CaretRight size={14} />
          </button>
          <button
            type="button"
            className="button button-ghost button-sm"
            onClick={zoomToSelection}
            disabled={selected == null}
            title="Zoom to the selected region"
          >
            Selection
          </button>
          <button
            type="button"
            className="button button-ghost button-sm"
            onClick={() => setView(null)}
            disabled={!zoomed}
            title="Show the whole timeline"
          >
            Fit
          </button>
          <span className="mt-view-label">{viewLabel}</span>
        </div>
        {loadError ? <div className="render-error">waveform: {loadError}</div> : null}

        <div ref={timelineRef} className="mt-rows">
          {/* ruler */}
          <div className="mt-row mt-ruler-row">
            <div className="mt-track-head mt-ruler-head">time</div>
            <canvas
              data-seek="ruler"
              className="mt-canvas"
              ref={(el) => {
                if (el) canvasRefs.current.set("__ruler__", el);
                else canvasRefs.current.delete("__ruler__");
              }}
              onMouseDown={onRulerMouseDown}
            />
          </div>

          {tracks.map(({ stem, take }) => {
            const divider = lastScene !== null && lastScene !== stem.scene ? (
              <div className="mt-scene-divider" key={`scene-${stem.scene}`}>
                Scene {stem.scene}
              </div>
            ) : null;
            lastScene = stem.scene;
            const cuts = pending[stem.id] ?? [];
            const isMuted = muted.has(stem.id);
            const isSolo = solo.has(stem.id);
            return (
              <React.Fragment key={stem.id}>
                {divider}
                <div className="mt-row">
                  <div
                    className="mt-track-head"
                    ref={(el) => {
                      if (el) headRefs.current.set(stem.id, el);
                      else headRefs.current.delete(stem.id);
                    }}
                  >
                    <span className="speaker-badge" style={speakerStyle(stem.speaker)}>
                      {stem.speaker}
                    </span>
                    <span className="mt-track-sub">
                      {cuts.length}/{stem.line_ids.length} cuts ·{" "}
                      {take ? formatDuration(take.duration_s) : "no audio"}
                      {stem.takes.length > 1 ? ` · ${stem.takes.length} takes` : ""}
                    </span>
                    <div className="mt-track-controls">
                      <button
                        type="button"
                        className={`button button-ghost button-sm mt-toggle${isMuted ? " active" : ""}`}
                        onClick={() => toggleMute(stem.id)}
                        title={isMuted ? "Unmute track" : "Mute track"}
                        aria-pressed={isMuted}
                      >
                        M
                      </button>
                      <button
                        type="button"
                        className={`button button-ghost button-sm mt-toggle${isSolo ? " active" : ""}`}
                        onClick={() => toggleSolo(stem.id)}
                        title={isSolo ? "Unsolo track" : "Solo track"}
                        aria-pressed={isSolo}
                      >
                        S
                      </button>
                      <input
                        type="range"
                        min={0}
                        max={100}
                        value={Math.round((volumes[stem.id] ?? 1) * 100)}
                        onChange={(e) =>
                          setVolumes((p) => ({ ...p, [stem.id]: Number(e.target.value) / 100 }))
                        }
                        title="Track volume"
                        aria-label={`${stem.speaker} volume`}
                        className="mt-volume"
                      />
                      <button
                        type="button"
                        className="button button-ghost button-sm"
                        disabled={saving[stem.id] || !dirty[stem.id] || invalidByStem[stem.id]}
                        onClick={() => void saveStem(stem)}
                        title={dirty[stem.id] ? "Save this stem's cuts" : "No unsaved changes"}
                      >
                        {dirty[stem.id] ? <span className="mt-dirty-dot" /> : null}
                        {saving[stem.id] ? "Saving…" : "Save"}
                      </button>
                    </div>
                  </div>
                  {take ? (
                    <canvas
                      data-seek="track"
                      className="mt-canvas"
                      ref={(el) => {
                        if (el) canvasRefs.current.set(stem.id, el);
                        else canvasRefs.current.delete(stem.id);
                      }}
                      onMouseDown={(e) => onTrackMouseDown(e, stem.id)}
                      onMouseMove={(e) => onTrackMouseMove(e, stem.id)}
                      onMouseUp={onTrackMouseUp}
                      onMouseLeave={onTrackMouseUp}
                    />
                  ) : (
                    <div className="mt-noaudio">Render this stem first.</div>
                  )}
                </div>
              </React.Fragment>
            );
          })}
        </div>

        {/* selected-region editor (keyboard-accessible alternative to drag) */}
        {selected && selCut && selStem ? (
          <div className="mt-selection">
            <div className="mt-selection-head">
              <span className="speaker-badge" style={speakerStyle(selStem.speaker)}>
                {selStem.speaker}
              </span>
              <strong>{selCut.line_id}</strong>
              <span className="mt-selection-text" title={selLine?.text}>
                {selLine?.text ?? ""}
              </span>
            </div>
            <div className="mt-selection-controls">
              <label>
                <span>track</span>
                <select
                  value={selTake?.id ?? ""}
                  onChange={(e) => {
                    if (selCut) switchCutTake(selStem, selCut, e.target.value);
                  }}
                  title="Which take this clip uses"
                >
                  {selStem.takes.map((take, index) => (
                    <option key={take.id} value={take.id}>
                      T{index + 1} · {take.engine}
                      {take.vocal_isolated ? " vocal" : ""} ·{" "}
                      {formatDuration(take.duration_s)}
                      {take.selected ? " · default" : ""}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span>start</span>
                <input
                  type="number"
                  step="0.01"
                  min={0}
                  value={Number(selCut.start_s.toFixed(2))}
                  onChange={(e) => {
                    const idx = (pending[selStem.id] ?? []).findIndex(
                      (c) => c.line_id === selCut.line_id,
                    );
                    if (idx >= 0) updateCut(selStem.id, idx, "start_s", Number(e.target.value));
                  }}
                />
              </label>
              <label>
                <span>end</span>
                <input
                  type="number"
                  step="0.01"
                  min={0}
                  value={Number(selCut.end_s.toFixed(2))}
                  onChange={(e) => {
                    const idx = (pending[selStem.id] ?? []).findIndex(
                      (c) => c.line_id === selCut.line_id,
                    );
                    if (idx >= 0) updateCut(selStem.id, idx, "end_s", Number(e.target.value));
                  }}
                />
              </label>
              <button
                type="button"
                className="button button-ghost button-sm"
                onClick={() => void playRegion(selStem.id, selCut.line_id)}
              >
                {auditioning ? "Stop" : "Play"}
              </button>
              {invalidByStem[selStem.id] ? (
                <span className="render-warn">Fix cut ranges before saving.</span>
              ) : null}
            </div>
          </div>
        ) : (
          <p className="panel-sub mt-hint">
            Regions follow the script order. Click one to select it, drag its body to slip the
            audio inside the region, or edit start/end below. Click empty space to seek.
          </p>
        )}
      </div>
    </div>
  );
}
