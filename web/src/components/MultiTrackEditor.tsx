import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CaretLeft,
  CaretRight,
  FloppyDisk,
  MagnifyingGlassMinus,
  MagnifyingGlassPlus,
  MusicNotes,
  Pause,
  Play,
  Scissors,
  SpeakerHigh,
  Trash,
} from "@phosphor-icons/react";
import { api } from "../api";
import {
  computeCueSpans,
  cueLaneOf,
  type CueLane,
  type CueSpan,
} from "../audio/cues";
import { SyncEngine, type ScheduleSegment } from "../audio/syncEngine";
import { computeTimeline, segIdOf, type Cut } from "../audio/timeline";
import type { Cue, Project, Stem, Take } from "../types";
import { formatDuration, speakerStyle } from "../utils";

interface Props {
  project: Project;
  onSaveCuts: (stem: Stem, cuts: Cut[], rebuildMix?: boolean) => void | Promise<void>;
  onSaveAll?: (stems: Record<string, Cut[]>) => void | Promise<void>;
  onSaveCues?: (cues: Cue[]) => void | Promise<void>;
  onDeleteCue?: (cue: Cue) => void | Promise<void>;
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
const CUE_LANE_H = 64;
const EDGE_PX = 10;

const CUE_LANES: { id: CueLane; label: string; hint: string }[] = [
  { id: "music", label: "Music", hint: "music cues" },
  { id: "sfx", label: "SFX", hint: "beds and one-shots" },
];

const cueLaneKey = (lane: CueLane) => `cue:${lane}`;
const cueBufferKey = (sfxId: string) => `sfx:${sfxId}`;

function cueSegmentOf(
  span: CueSpan,
  sourceDuration: number,
): ScheduleSegment | null {
  if (sourceDuration <= 0) return null;
  const head = Math.max(0, span.start - span.anchor);
  const length = span.end - span.start;
  if (length <= 0.005) return null;
  const localEnd = span.loops
    ? head + length
    : Math.min(head + length, sourceDuration);
  if (localEnd - head <= 0.005) return null;
  return {
    stemId: cueLaneKey(span.lane),
    takeId: cueBufferKey(span.cue.sfx_id),
    localStart: head,
    localEnd,
    start: span.start,
    end: span.end,
    gain: Math.pow(10, span.cue.gain_db / 20),
    loop: span.loops,
  };
}

const round2 = (value: number) => Math.round(value * 100) / 100;
const clamp = (lo: number, hi: number, v: number) => Math.max(lo, Math.min(hi, v));

interface DragState {
  stemId: string;
  idx: number;
  mode: "trim-start" | "trim-end" | "slip" | "move";
  /** Global slot start when the drag began (stable while trimming). */
  anchorGlobal: number;
  /** Local take time under the cursor when the drag began. */
  grabLocal: number;
  /** Global time under the cursor when the drag began (for move). */
  grabGlobal: number;
  origStart: number;
  origEnd: number;
  /** Global position before the drag (for move). */
  origPosition: number;
}

interface CueDragState {
  cueId: string;
  /** Global time under the cursor when the drag began. */
  grabGlobal: number;
  /** Cue offset before the drag. */
  origOffset: number;
}

export function MultiTrackEditor({
  project,
  onSaveCuts,
  onSaveAll,
  onSaveCues,
  onDeleteCue,
  onRefresh,
}: Props) {
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
            gain_db: l.gain_db ?? 0,
            fade_in_s: l.fade_in_s ?? 0,
            fade_out_s: l.fade_out_s ?? 0,
            speed: l.speed ?? 1,
            pitch_cents: l.pitch_cents ?? 0,
            reverb_mix: l.reverb_mix ?? 0,
            dereverb: l.dereverb ?? false,
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

  const updateCutEffect = (stemId: string, segId: string, patch: Partial<Cut>) => {
    const cuts = (pending[stemId] ?? []).map((c) => ({ ...c }));
    const idx = cuts.findIndex((c) => segIdOf(c) === segId);
    if (idx < 0) return;
    cuts[idx] = { ...cuts[idx], ...patch };
    commitCuts(stemId, cuts);
  };

  const [pending, setPending] = useState<Record<string, Cut[]>>(savedCuts);
  const [cues, setCues] = useState<Cue[]>(project.cues);
  const [cueDirty, setCueDirty] = useState<Record<string, boolean>>({});
  const [selectedCueId, setSelectedCueId] = useState<string | null>(null);
  const [cueAudio, setCueAudio] = useState<
    Record<string, { duration: number; peaks: number[]; missing?: boolean }>
  >({});
  const [dirty, setDirty] = useState<Record<string, boolean>>({});
  const [loaded, setLoaded] = useState<Record<string, { duration: number; peaks: number[] }>>({});
  const [loadError, setLoadError] = useState<string | null>(null);
  const [view, setView] = useState<{ start: number; end: number } | null>(null);
  const [playhead, setPlayhead] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [selected, setSelected] = useState<{ stemId: string; segId: string } | null>(null);
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
  const [savingCues, setSavingCues] = useState(false);
  const [confirmDeleteCueId, setConfirmDeleteCueId] = useState<string | null>(null);
  const [trackH, setTrackH] = useState(TRACK_H);

  const timelineRef = useRef<HTMLDivElement>(null);
  const canvasRefs = useRef(new Map<string, HTMLCanvasElement>());
  const headRefs = useRef(new Map<string, HTMLDivElement>());
  const loadedUrl = useRef<Record<string, string>>({});
  const cueLoaded = useRef<Record<string, boolean>>({});
  const dragRef = useRef<DragState | null>(null);
  const cueDragRef = useRef<CueDragState | null>(null);
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
    const cutsDirty = Object.values(dirty).some(Boolean);
    const cuesDirty = Object.values(cueDirty).some(Boolean);
    if (!cutsDirty && !cuesDirty) return;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty, cueDirty]);

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

  // Sync non-dirty cues from the server project; dirty rows keep the local
  // edit until Save all (drag / inspector changes are staged).
  useEffect(() => {
    setCues((prev) => {
      const localById = new Map(prev.map((cue) => [cue.id, cue]));
      const remoteIds = new Set(project.cues.map((cue) => cue.id));
      const merged = project.cues.map((cue) =>
        cueDirty[cue.id] ? (localById.get(cue.id) ?? cue) : cue,
      );
      // Keep a dirty local cue the server has not seen yet (defensive).
      for (const cue of prev) {
        if (cueDirty[cue.id] && !remoteIds.has(cue.id)) merged.push(cue);
      }
      return merged;
    });
  }, [project.cues, cueDirty]);

  // Source-script layout: every segment sits at its mixer-compatible slot.
  // Multiple segments per line stagger left-to-right (split clips stay
  // adjacent), matching mixer.py `_seen_lines`.
  const timeline = useMemo(() => computeTimeline(project, pending), [project, pending]);
  const slots = timeline.slots;
  const lineStarts = timeline.lineStarts;
  const total = timeline.total;

  const schedule = useMemo<ScheduleSegment[]>(() => {
    const segments: ScheduleSegment[] = [];
    for (const stem of project.stems) {
      for (const cut of pending[stem.id] ?? []) {
        const slot = slots.get(segIdOf(cut));
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
          gainDb: cut.gain_db ?? 0,
          fadeInS: cut.fade_in_s ?? 0,
          fadeOutS: cut.fade_out_s ?? 0,
          speed: cut.speed ?? 1,
          pitchCents: cut.pitch_cents ?? 0,
          reverbMix: cut.reverb_mix ?? 0,
          dereverb: cut.dereverb ?? false,
        });
      }
    }
    return segments;
  }, [project, pending, slots, takeByIdFor]);

  const cueDurations = useMemo(() => {
    const durations: Record<string, number> = {};
    for (const [sfxId, info] of Object.entries(cueAudio)) {
      durations[sfxId] = info.duration;
    }
    return durations;
  }, [cueAudio]);

  const cueSpans = useMemo(
    () => computeCueSpans(project, cues, slots, total, cueDurations, lineStarts),
    [project, cues, slots, total, cueDurations, lineStarts],
  );

  const cueSegments = useMemo<ScheduleSegment[]>(() => {
    const segments: ScheduleSegment[] = [];
    for (const span of cueSpans) {
      const info = cueAudio[span.cue.sfx_id];
      if (!info || info.missing) continue;
      const segment = cueSegmentOf(span, info.duration);
      if (segment) segments.push(segment);
    }
    return segments;
  }, [cueSpans, cueAudio]);

  const allSchedule = useMemo(
    () => [...schedule, ...cueSegments],
    [schedule, cueSegments],
  );

  const audibleEnd = useMemo(
    () => allSchedule.reduce((max, seg) => Math.max(max, seg.end), 0),
    [allSchedule],
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

  const neededCueSfx = useMemo(
    () => Array.from(new Set(cues.map((cue) => cue.sfx_id))),
    [cues],
  );

  // Load + decode each needed take once per file.
  useEffect(() => {
    let cancelled = false;
    // Evict decoded buffers the board no longer draws (LRU in the engine caps
    // memory; prune drops ids that left the needed set entirely). Cue sources
    // share the same cache under `sfx:<id>`.
    engine.prune([
      ...neededTakes.map(({ take }) => take.id),
      ...neededCueSfx.map(cueBufferKey),
    ]);
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
  }, [engine, neededTakes, neededCueSfx, project.id]);

  // Decode cue sources once; the waveform and cue length come from the buffer.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      for (const sfxId of neededCueSfx) {
        if (cueLoaded.current[sfxId] && engine.hasTrack(cueBufferKey(sfxId))) continue;
        const cue = cues.find((item) => item.sfx_id === sfxId);
        const laneKey = cueLaneKey(cueLaneOf(cue?.kind ?? "one_shot"));
        try {
          const info = await engine.loadTrack(
            laneKey,
            cueBufferKey(sfxId),
            api.sfxAudioUrl(sfxId),
          );
          if (cancelled) return;
          cueLoaded.current[sfxId] = true;
          setCueAudio((prev) => ({
            ...prev,
            [sfxId]: { duration: info.duration, peaks: info.peaks },
          }));
        } catch {
          if (cancelled) return;
          cueLoaded.current[sfxId] = true;
          setCueAudio((prev) => ({
            ...prev,
            [sfxId]: { duration: 0, peaks: [], missing: true },
          }));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [engine, neededCueSfx, cues]);

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
        engine.seek(c, allSchedule);
      } else {
        engine.setFrozen(c);
        setPlayhead(c);
      }
    },
    [engine, total, allSchedule],
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
      engine.play(t, allSchedule);
      setPlaying(engine.playing);
      if (!engine.playing) setPlayhead(t);
    }
  }, [engine, audibleEnd, allSchedule]);

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
      const slot = slots.get(selected.segId);
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
    const slot = slots.get(selected.segId);
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
      kind:
        | { ruler: true }
        | { ruler: false; stemId: string; trackIdx: number }
        | { ruler: false; cueLane: CueLane },
    ) => {
      const root = timelineRef.current;
      if (!root) return;
      const dpr = window.devicePixelRatio || 1;
      const w = Math.max(50, root.clientWidth - 196);
      const h = kind.ruler
        ? RULER_H
        : "cueLane" in kind
          ? CUE_LANE_H
          : trackH;
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
      const drawPlayhead = () => {
        const px = toX(playheadRef.current);
        if (px < 0 || px > w) return;
        ctx.strokeStyle = "#fb7185";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(Math.round(px) + 0.5, 0);
        ctx.lineTo(Math.round(px) + 0.5, h);
        ctx.stroke();
      };

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
        drawPlayhead();
        return;
      }

      if ("cueLane" in kind) {
        // Cue lanes: anchored clips, dragged horizontally to change offset.
        const laneSpans = cueSpans.filter((item) => item.lane === kind.cueLane);
        const mid = h / 2;
        if (laneSpans.length === 0) {
          ctx.fillStyle = "rgba(255,255,255,0.35)";
          ctx.font = "12px sans-serif";
          ctx.fillText(
            kind.cueLane === "music"
              ? "No music cues — add them in Sound."
              : "No SFX cues — add beds or one-shots in Sound.",
            Math.max(8, toX(start) + 8),
            mid + 4,
          );
        }
        for (const item of laneSpans) {
          if (item.end < start || item.start > end) continue;
          const info = cueAudio[item.cue.sfx_id];
          const missing = Boolean(info?.missing);
          const isSel = selectedCueId === item.cue.id;
          const x0 = toX(item.start);
          const x1 = toX(item.end);
          const wpx = Math.max(2, x1 - x0);
          ctx.fillStyle = missing
            ? "rgba(248, 113, 113, 0.16)"
            : item.lane === "music"
              ? "rgba(168, 85, 247, 0.24)"
              : "rgba(34, 211, 238, 0.16)";
          ctx.fillRect(x0, 0, wpx, h);

          if (info && info.peaks.length > 0 && item.sourceDuration > 0) {
            ctx.save();
            ctx.beginPath();
            ctx.rect(x0, 0, wpx, h);
            ctx.clip();
            ctx.strokeStyle = isSel
              ? "rgba(254, 243, 199, 0.9)"
              : "rgba(226, 232, 240, 0.55)";
            ctx.lineWidth = 1;
            ctx.beginPath();
            const source = item.sourceDuration;
            const peaks = info.peaks;
            const drawRep = (repStart: number, repOffset: number, repEnd: number) => {
              const visStart = Math.max(repStart + repOffset, item.start, start);
              const visEnd = Math.min(repEnd, item.end, end);
              if (visEnd <= visStart) return;
              const ls = visStart - repStart;
              const le = visEnd - repStart;
              const i0 = Math.max(0, Math.floor((ls / source) * peaks.length));
              const i1 = Math.min(peaks.length, Math.ceil((le / source) * peaks.length));
              for (let i = i0; i < i1; i++) {
                const localT = ((i + 0.5) / peaks.length) * source;
                const x = toX(repStart + localT);
                if (x < x0 || x > x1) continue;
                const amp = peaks[i] * (h * 0.34);
                ctx.moveTo(x, mid - amp);
                ctx.lineTo(x, mid + amp);
              }
            };
            if (item.loops) {
              const reps = Math.min(
                2000,
                Math.ceil((item.end - item.start) / source) + 1,
              );
              for (let k = 0; k < reps; k++) {
                const repStart = item.start + k * source;
                drawRep(repStart, 0, repStart + source);
              }
            } else {
              drawRep(item.start, Math.max(0, item.start - item.anchor), item.end);
            }
            ctx.stroke();
            ctx.restore();
          }

          ctx.strokeStyle = missing
            ? "rgba(248, 113, 113, 0.9)"
            : isSel
              ? "#fbbf24"
              : "rgba(255,255,255,0.75)";
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

          ctx.save();
          ctx.beginPath();
          ctx.rect(x0, 0, wpx, h);
          ctx.clip();
          const label = `${item.cue.name}${item.loops ? " ↻" : ""}`;
          ctx.font = isSel ? "600 12px sans-serif" : "12px sans-serif";
          const tw = ctx.measureText(label).width;
          const lx = Math.max(x0 + 4, 4);
          const ly = 15;
          ctx.fillStyle = isSel ? "rgba(251,191,36,0.9)" : "rgba(0,0,0,0.65)";
          ctx.fillRect(lx - 3, ly - 12, tw + 6, 16);
          ctx.fillStyle = missing ? "#fecaca" : isSel ? "#000" : "#fff";
          ctx.fillText(missing ? `${label} · missing` : label, lx, ly);
          ctx.restore();
        }
        drawPlayhead();
        return;
      }

      const { stemId, trackIdx } = kind;
      const stem = stemById.get(stemId) ?? null;
      const mid = h / 2;

      // Regions live at their segment slots; waveform inside each region
      // is the cut window of its own take, mapped 1:1 into that slot.
      // Split clips (cutting a marked section out) draw as adjacent segments.
      const cuts = pending[stemId] ?? [];
      cuts.forEach((cut, idx) => {
        const slot = slots.get(segIdOf(cut));
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
        const isSel = selected?.stemId === stemId && selected.segId === segIdOf(cut);

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
          selection.segId === segIdOf(cut)
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

      drawPlayhead();
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
      cueAudio,
      cueSpans,
      selectedCueId,
    ],
  );

  useEffect(() => {
    const drawAll = () => {
      const ruler = canvasRefs.current.get("__ruler__");
      if (ruler) drawOne(ruler, { ruler: true });
      tracks.forEach(({ stem }, i) => {
        const c = canvasRefs.current.get(stem.id);
        if (c) drawOne(c, { ruler: false, stemId: stem.id, trackIdx: i });
      });
      for (const lane of CUE_LANES) {
        const c = canvasRefs.current.get(`__cue_${lane.id}__`);
        if (c) drawOne(c, { ruler: false, cueLane: lane.id });
      }
    };
    drawAll();
    window.addEventListener("resize", drawAll);
    return () => window.removeEventListener("resize", drawAll);
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
        const slot = slots.get(segIdOf(cuts[i]));
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
      for (let i = 0; i < cuts.length; i++) {
        const slot = slots.get(segIdOf(cuts[i]));
        if (!slot || slot.end <= slot.start) continue;
        if (globalT >= slot.start && globalT <= slot.end) return i;
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

  const findCueSpan = useCallback(
    (lane: CueLane, globalT: number) => {
      const laneSpans = cueSpans.filter((item) => item.lane === lane);
      for (let i = laneSpans.length - 1; i >= 0; i--) {
        const item = laneSpans[i];
        if (globalT >= item.start && globalT <= item.end) return item;
      }
      return null;
    },
    [cueSpans],
  );

  const updateCueLocal = useCallback(
    (cueId: string, patch: Partial<Omit<Cue, "id">>) => {
      setCues((prev) =>
        prev.map((cue) => (cue.id === cueId ? { ...cue, ...patch } : cue)),
      );
      setCueDirty((prev) => ({ ...prev, [cueId]: true }));
    },
    [],
  );

  const onCueMouseDown = (e: React.MouseEvent, lane: CueLane) => {
    if (e.button !== 0) return;
    const canvas = e.currentTarget as HTMLCanvasElement;
    const globalT = toGlobal(canvas, e.clientX);
    const item = findCueSpan(lane, globalT);
    if (item) {
      setSelectedCueId(item.cue.id);
      setConfirmDeleteCueId((prev) => (prev === item.cue.id ? prev : null));
      setSelected(null);
      setSelection(null);
      selectionRef.current = null;
      cueDragRef.current = {
        cueId: item.cue.id,
        grabGlobal: globalT,
        origOffset: item.cue.offset_s,
      };
      canvas.style.cursor = "grabbing";
      return;
    }
    if (total > 0) seek(clamp(0, total, globalT));
    setSelectedCueId(null);
    setConfirmDeleteCueId(null);
  };

  const onCueMouseMove = (e: React.MouseEvent, lane: CueLane) => {
    const canvas = e.currentTarget as HTMLCanvasElement;
    const drag = cueDragRef.current;
    if (drag) {
      const delta = toGlobal(canvas, e.clientX) - drag.grabGlobal;
      const raw = drag.origOffset + delta;
      // Shift snaps to quarter-seconds for musical placement.
      const next = e.shiftKey ? Math.round(raw * 4) / 4 : round2(raw);
      updateCueLocal(drag.cueId, { offset_s: next });
      canvas.style.cursor = "grabbing";
      return;
    }
    const item = findCueSpan(lane, toGlobal(canvas, e.clientX));
    canvas.style.cursor = item ? "grab" : "default";
  };

  const onCueMouseUp = () => {
    cueDragRef.current = null;
  };

  /** Move one region to another take, scaling its window by the duration ratio. */
  const switchCutTake = useCallback(
    (stem: Stem, cut: Cut, takeId: string) => {
      const next = stem.takes.find((take) => take.id === takeId);
      if (!next) return;
      const prev = takeByIdFor(stem, cut.take_id);
      const prevDur = takeDurationOf(prev);
      const nextDur = takeDurationOf(next) || next.duration_s;
      const cuts = (pending[stem.id] ?? []).map((item) => ({ ...item }));
      const idx = cuts.findIndex((item) => segIdOf(item) === segIdOf(cut));
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
    setSelectedCueId(null);
    setConfirmDeleteCueId(null);
    const canvas = e.currentTarget as HTMLCanvasElement;
    const globalT = toGlobal(canvas, e.clientX);
    const edge = findEdge(stemId, canvas, e.clientX);
    if (edge) {
      const cuts = pending[stemId] ?? [];
      const cut = cuts[edge.idx];
      if (!cut) return;
      const slot = slots.get(segIdOf(cut));
      if (!slot) return;
      dragRef.current = {
        stemId,
        idx: edge.idx,
        mode: edge.edge,
        anchorGlobal: slot.start,
        grabLocal: cut.start_s + (globalT - slot.start),
        grabGlobal: globalT,
        origStart: cut.start_s,
        origEnd: cut.end_s,
        origPosition: slot.start,
      };
      setSelected({ stemId, segId: segIdOf(cut) });
      return;
    }
    const regionIdx = findRegion(stemId, globalT);
    const cuts = pending[stemId] ?? [];
    const idx = regionIdx ?? -1;
    const cut = idx >= 0 ? cuts[idx] : null;
    const stem = stemById.get(stemId) ?? null;
    const dur = takeDurationOf(takeByIdFor(stem, cut?.take_id));
    const slot = cut ? (slots.get(segIdOf(cut)) ?? null) : null;
    if (cut && dur > 0 && slot && e.shiftKey) {
      // Shift-drag inside a clip: mark a section to cut out.
      // Replaces any previous mark; Backspace/Delete (or the button) cuts it.
      const segId = segIdOf(cut);
      const s = clamp(slot.start, slot.end, globalT);
      selectionRef.current = {
        stemId,
        lineId: cut.line_id,
        segId,
        anchorGlobal: s,
        clipStart: slot.start,
        clipEnd: slot.end,
      };
      setSelection({ stemId, lineId: cut.line_id, segId, start: s, end: s });
      setSelected({ stemId, segId });
      return;
    }
    if (cut && dur > 0 && slot) {
      if (e.altKey || e.ctrlKey || e.metaKey) {
        // Alt/Ctrl-drag: slip the cut window inside the take (DAW slip).
        dragRef.current = {
          stemId,
          idx,
          mode: "slip",
          anchorGlobal: slot.start,
          grabLocal: cut.start_s + (globalT - slot.start),
          grabGlobal: globalT,
          origStart: cut.start_s,
          origEnd: cut.end_s,
          origPosition: slot.start,
        };
        setSelected({ stemId, segId: segIdOf(cut) });
        return;
      }
      // Plain LMB drag (Suno Studio style): move the section on the timeline
      // (position_s). The local cut window stays intact.
      dragRef.current = {
        stemId,
        idx,
        mode: "move",
        anchorGlobal: slot.start,
        grabLocal: cut.start_s + (globalT - slot.start),
        grabGlobal: globalT,
        origStart: cut.start_s,
        origEnd: cut.end_s,
        origPosition: slot.start,
      };
      setSelected({ stemId, segId: segIdOf(cut) });
      return;
    }
    // Empty space: LMB seeks the shared playhead.
    if (total > 0) seek(clamp(0, total, globalT));
    setSelected(null);
  };

  const onTrackMouseMove = (e: React.MouseEvent, stemId: string) => {
    const canvas = e.currentTarget as HTMLCanvasElement;
    const marking = selectionRef.current;
    if (marking && marking.stemId === stemId && !dragRef.current) {
      // Shift-drag marking: extend the marked section, clamped to the clip.
      const globalT = toGlobal(canvas, e.clientX);
      const t = clamp(marking.clipStart, marking.clipEnd, globalT);
      const s = Math.min(marking.anchorGlobal, t);
      const en = Math.max(marking.anchorGlobal, t);
      setSelection({
        stemId: marking.stemId,
        lineId: marking.lineId,
        segId: marking.segId,
        start: round2(s),
        end: round2(en),
      });
      canvas.style.cursor = "text";
      return;
    }
    const drag = dragRef.current;
    if (!drag || drag.stemId !== stemId) {
      if (!drag && !marking) {
        const edge = findEdge(stemId, canvas, e.clientX);
        if (edge) {
          canvas.style.cursor = "ew-resize";
          return;
        }
        const region = findRegion(stemId, toGlobal(canvas, e.clientX));
        canvas.style.cursor = region != null ? "move" : "default";
      }
      return;
    }
    const globalT = toGlobal(canvas, e.clientX);
    const cuts = (pending[stemId] ?? []).map((c) => ({ ...c }));
    const cut = cuts[drag.idx];
    if (!cut) return;
    const stem = stemById.get(stemId) ?? null;
    const dur = takeDurationOf(takeByIdFor(stem, cut.take_id));
    if (dur <= 0) return;
    const local = drag.origStart + (globalT - drag.anchorGlobal);
    if (drag.mode === "trim-start") {
      cut.start_s = round2(clamp(0, cut.end_s - 0.05, local));
      canvas.style.cursor = "ew-resize";
    } else if (drag.mode === "trim-end") {
      cut.end_s = round2(clamp(cut.start_s + 0.05, dur, local));
      canvas.style.cursor = "ew-resize";
    } else if (drag.mode === "slip") {
      const len = drag.origEnd - drag.origStart;
      const shiftMin = -drag.origStart;
      const shiftMax = dur - drag.origEnd;
      const shift = clamp(shiftMin, shiftMax, local - drag.grabLocal);
      cut.start_s = round2(drag.origStart + shift);
      cut.end_s = round2(drag.origStart + shift + len);
      canvas.style.cursor = "move";
    } else {
      // move: Suno-Studio-style position drag — slot start follows cursor.
      const delta = globalT - drag.grabGlobal;
      cut.position_s = round2(Math.max(0, drag.origPosition + delta));
      canvas.style.cursor = "grabbing";
    }
    commitCuts(stemId, cuts);
  };

  const onTrackMouseUp = () => {
    if (selectionRef.current) {
      const cur = selection;
      if (cur && Math.abs(cur.end - cur.start) >= 0.05) {
        // Keep the mark so Backspace/Delete (or the button) can cut it out.
        // The ref only tracks the in-progress drag.
      } else {
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

  /** Cut the marked section out of its clip: the clip splits into the kept
   * head and tail, placed adjacent so the timeline (and the next mix) closes
   * the gap. Marking the whole clip removes the segment entirely. */
  const cutOutSelection = useCallback(() => {
    if (!selection) return false;
    const { stemId, lineId, segId, start, end } = selection;
    const cuts = (pending[stemId] ?? []).map((c) => ({ ...c }));
    const idx = cuts.findIndex((c) => segIdOf(c) === segId && c.line_id === lineId);
    if (idx < 0) {
      setSelection(null);
      return false;
    }
    const cut = cuts[idx];
    const slot = slots.get(segId);
    if (!slot || slot.end <= slot.start) {
      setSelection(null);
      return false;
    }
    const dur = slot.end - slot.start;
    const len = cut.end_s - cut.start_s;
    if (len <= 0 || dur <= 0) {
      setSelection(null);
      return false;
    }
    // Map the global mark to local take times.
    const localSelStart = cut.start_s + ((start - slot.start) / dur) * len;
    const localSelEnd = cut.start_s + ((end - slot.start) / dur) * len;
    const ls = Math.max(cut.start_s, Math.min(cut.end_s, localSelStart));
    const le = Math.max(cut.start_s, Math.min(cut.end_s, localSelEnd));
    if (le - ls < 0.01) {
      setSelection(null);
      return false;
    }
    const speed = cut.speed && cut.speed > 0 ? cut.speed : 1;
    const keptHeadLen = ls - cut.start_s;
    const newCuts: Cut[] = [];
    // Kept head (when the mark does not start at the clip edge).
    if (keptHeadLen >= 0.05) {
      newCuts.push({
        ...cut,
        id: cut.id,
        start_s: round2(cut.start_s),
        end_s: round2(ls),
        position_s: cut.position_s,
      });
    }
    // Kept tail (when the mark does not end at the clip edge): placed
    // adjacent to the head so no silence gap is left behind.
    if (cut.end_s - le >= 0.05) {
      const seg: Cut = {
        ...cut,
        id: `${cut.id ?? cut.line_id}__${Date.now()}`,
        line_id: cut.line_id,
        take_id: cut.take_id,
        start_s: round2(le),
        end_s: round2(cut.end_s),
        position_s: (cut.position_s ?? slot.start) + keptHeadLen / speed,
      };
      newCuts.push(seg);
    }
    // newCuts empty = the mark covered the whole clip: drop the segment.
    const replaced = [...cuts.slice(0, idx), ...newCuts, ...cuts.slice(idx + 1)];
    commitCuts(stemId, replaced);
    setSelection(null);
    const kept = newCuts[0];
    if (kept) setSelected({ stemId, segId: segIdOf(kept) });
    else setSelected(null);
    return true;
  }, [selection, pending, slots, commitCuts]);

  const onCardKeyDown = (e: React.KeyboardEvent) => {
    const target = e.target as HTMLElement | null;
    if (target?.closest("input, select, textarea, button, a, [contenteditable='true']")) return;
    if ((e.key === "Backspace" || e.key === "Delete") && selection) {
      e.preventDefault();
      cutOutSelection();
      return;
    }
    if (e.key === "Escape" && selection) {
      e.preventDefault();
      setSelection(null);
      selectionRef.current = null;
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
      } catch {
        // The page surfaces the error; keep the stem dirty so the user can retry.
      } finally {
        setSaving((p) => ({ ...p, [stem.id]: false }));
      }
    },
    [invalidByStem, saving, onSaveCuts, pending],
  );

  const dirtyCueIds = useMemo(
    () => Object.keys(cueDirty).filter((id) => cueDirty[id]),
    [cueDirty],
  );
  const dirtyCount =
    tracks.filter((t) => dirty[t.stem.id] && !invalidByStem[t.stem.id]).length +
    dirtyCueIds.length;

  const saveAll = useCallback(async () => {
    if (savingAll || savingCues) return;
    setSavingAll(true);
    try {
      const dirtyStems = tracks.filter(
        (t) => dirty[t.stem.id] && !invalidByStem[t.stem.id],
      );
      const dirtyCueList = cues.filter((cue) => cueDirty[cue.id]);
      if (dirtyStems.length === 0 && dirtyCueList.length === 0) return;
      if (dirtyStems.length > 0) {
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
      }
      if (dirtyCueList.length > 0) {
        setSavingCues(true);
        try {
          if (onSaveCues) {
            await onSaveCues(dirtyCueList);
          } else {
            for (const cue of dirtyCueList) {
              const { id, ...patch } = cue;
              void id;
              await api.updateCue(project.id, cue.id, patch);
            }
            await onRefresh();
          }
          setCueDirty((p) => {
            const next = { ...p };
            for (const cue of dirtyCueList) next[cue.id] = false;
            return next;
          });
        } finally {
          setSavingCues(false);
        }
      }
    } catch {
      // The page surfaces the error; dirty flags stay set for a retry.
    } finally {
      setSavingAll(false);
    }
  }, [savingAll, savingCues, tracks, dirty, invalidByStem, onSaveCuts, onSaveAll, onSaveCues, onRefresh, pending, cues, cueDirty, project.id]);

  const updateCut = (stemId: string, index: number, field: "start_s" | "end_s", value: number) => {
    const cuts = (pending[stemId] ?? []).map((c) => ({ ...c }));
    cuts[index] = {
      ...cuts[index],
      [field]: Number.isFinite(value) ? value : cuts[index][field],
    };
    commitCuts(stemId, cuts);
  };

  const playRegion = useCallback(
    async (stemId: string, segId: string) => {
      const cut = (pending[stemId] ?? []).find((c) => segIdOf(c) === segId);
      if (!cut) return;
      const aud = auditionRef.current;
      if (aud && aud.stemId === stemId && aud.lineId === segId && engine.playing) {
        engine.pause();
        auditionRef.current = null;
        setPlaying(false);
        setPlayhead(engine.getTime());
        return;
      }
      await engine.resume();
      const slot = slots.get(segId);
      const take = takeByIdFor(stemById.get(stemId), cut.take_id);
      if (!slot || slot.end <= slot.start || !take) return;
      auditionRef.current = { stemId, lineId: segId, endGlobal: slot.end };
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
            gainDb: cut.gain_db ?? 0,
            fadeInS: cut.fade_in_s ?? 0,
            fadeOutS: cut.fade_out_s ?? 0,
            speed: cut.speed ?? 1,
            pitchCents: cut.pitch_cents ?? 0,
            reverbMix: cut.reverb_mix ?? 0,
            dereverb: cut.dereverb ?? false,
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

  const auditionCue = useCallback(
    async (cueId: string) => {
      const cue = cues.find((item) => item.id === cueId);
      if (!cue) return;
      const span = cueSpans.find((item) => item.cue.id === cueId);
      if (!span) return;
      const info = cueAudio[cue.sfx_id];
      if (!info || info.missing || info.duration <= 0) return;
      const segment = cueSegmentOf(span, info.duration);
      if (!segment) return;
      await engine.resume();
      auditionRef.current = null;
      engine.play(span.start, [segment], { onlyStemId: cueLaneKey(span.lane) });
      setPlaying(engine.playing);
      setPlayhead(span.start);
    },
    [cues, cueSpans, cueAudio, engine],
  );

  const deleteSelectedCue = useCallback(async () => {
    if (!selectedCueId) return;
    const cue = cues.find((item) => item.id === selectedCueId);
    if (!cue) {
      setSelectedCueId(null);
      return;
    }
    if (confirmDeleteCueId !== cue.id) {
      setConfirmDeleteCueId(cue.id);
      return;
    }
    setConfirmDeleteCueId(null);
    if (onDeleteCue) {
      await onDeleteCue(cue);
    } else {
      await api.deleteCue(project.id, cue.id);
      await onRefresh();
    }
    setCues((prev) => prev.filter((item) => item.id !== cue.id));
    setCueDirty((prev) => {
      const next = { ...prev };
      delete next[cue.id];
      return next;
    });
    setSelectedCueId(null);
  }, [selectedCueId, cues, confirmDeleteCueId, onDeleteCue, project.id, onRefresh]);

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
      ? (pending[selected.stemId] ?? []).find((c) => segIdOf(c) === selected.segId) ?? null
      : null;
  const selStem = selected != null ? tracks.find((t) => t.stem.id === selected.stemId)?.stem ?? null : null;
  const selLine = selCut != null ? lineById.get(selCut.line_id) : undefined;
  const selTake = selStem ? takeByIdFor(selStem, selCut?.take_id) : null;
  const auditioning =
    auditionRef.current != null &&
    playing &&
    selected != null &&
    auditionRef.current.stemId === selected.stemId &&
    auditionRef.current.lineId === selected.segId;
  const selCue = selectedCueId != null ? cues.find((cue) => cue.id === selectedCueId) ?? null : null;
  const selCueSpan = selCue != null ? cueSpans.find((item) => item.cue.id === selCue.id) ?? null : null;
  const selCueMissing = selCue != null ? Boolean(cueAudio[selCue.sfx_id]?.missing) : false;

  const [alignState, setAlignState] = useState<"idle" | "running">("idle");
  const [alignError, setAlignError] = useState<string | null>(null);
  const alignToScript = useCallback(async () => {
    if (alignState !== "idle") return;
    setAlignState("running");
    setAlignError(null);
    try {
      await api.alignToScript(project.id);
      await onRefresh();
    } catch (reason) {
      setAlignError(
        reason instanceof Error ? reason.message : "Could not align to script",
      );
    } finally {
      setAlignState("idle");
    }
  }, [alignState, project.id, onRefresh]);

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
            Cuts follow the script order (Viktoria 1, Andreas 1, Grandmother 1,
            Viktoria 2 …) — drag a clip to move its section, edges to trim,
            Alt/Ctrl-drag to slip the audio inside. Shift-drag marks a section;
            Backspace/Delete cuts it out. Align to script re-sorts.
          </p>
        </div>
        <div className="panel-actions">
          <button
            type="button"
            className="button button-ghost button-sm"
            onClick={() => void alignToScript()}
            disabled={savingAll || savingCues || dirtyCount > 0 || alignState === "running"}
            title={
              dirtyCount > 0
                ? "Save or discard your edits first — aligning would overwrite moved sections"
                : "Re-sort all cuts by their appearance in the main script (Viktoria 1, Andreas 1, Grandmother 1, Viktoria 2, ...)"
            }
          >
            {alignState === "running" ? <span className="spinner" /> : null}
            Align to script
          </button>
          <button type="button" className="button button-ghost button-sm" onClick={onRefresh}>
            Refresh
          </button>
          {project.mix_stale ? <span className="render-warn">mix stale</span> : null}
          <button
            type="button"
            className="button button-primary button-sm"
            disabled={savingAll || savingCues || dirtyCount === 0}
            onClick={() => void saveAll()}
            title={
              dirtyCueIds.length > 0
                ? `Save ${dirtyCueIds.length} cue edit${dirtyCueIds.length === 1 ? "" : "s"} plus cuts`
                : "Save all dirty cuts"
            }
          >
            {savingAll || savingCues ? <span className="spinner" /> : <FloppyDisk size={14} />}
            {savingAll || savingCues ? "Saving…" : `Save all (${dirtyCount})`}
          </button>
        </div>
      </section>

      {alignError ? (
        <div className="render-error" role="alert">
          {alignError}
        </div>
      ) : null}

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

          {CUE_LANES.map((lane) => {
            const laneSpans = cueSpans.filter((item) => item.lane === lane.id);
            const dirtyInLane = laneSpans.some((item) => cueDirty[item.cue.id]);
            return (
              <div className="mt-row" key={`cue-${lane.id}`}>
                <div className="mt-track-head">
                  {lane.id === "music" ? <MusicNotes size={14} /> : <SpeakerHigh size={14} />}
                  <span className="mt-track-sub">
                    {lane.label} · {laneSpans.length} cue{laneSpans.length === 1 ? "" : "s"}
                    {dirtyInLane ? " · unsaved" : ""}
                  </span>
                  <span className="panel-sub">{lane.hint}</span>
                </div>
                <canvas
                  data-seek={`cue-${lane.id}`}
                  className="mt-canvas"
                  ref={(el) => {
                    if (el) canvasRefs.current.set(`__cue_${lane.id}__`, el);
                    else canvasRefs.current.delete(`__cue_${lane.id}__`);
                  }}
                  onMouseDown={(e) => onCueMouseDown(e, lane.id)}
                  onMouseMove={(e) => onCueMouseMove(e, lane.id)}
                  onMouseUp={onCueMouseUp}
                  onMouseLeave={onCueMouseUp}
                />
              </div>
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
                      (c) => segIdOf(c) === segIdOf(selCut),
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
                      (c) => segIdOf(c) === segIdOf(selCut),
                    );
                    if (idx >= 0) updateCut(selStem.id, idx, "end_s", Number(e.target.value));
                  }}
                />
              </label>
              <label title="Per-region volume (-48..+12 dB)">
                <span>volume</span>
                <input
                  type="range"
                  min={-48}
                  max={12}
                  step={1}
                  value={Math.round(selCut.gain_db ?? 0)}
                  onChange={(e) => updateCutEffect(selStem.id, segIdOf(selCut), { gain_db: Number(e.target.value) })}
                />
                <span>{Math.round(selCut.gain_db ?? 0)} dB</span>
              </label>
              <label title="Fade-in seconds">
                <span>fade in</span>
                <input
                  type="number"
                  step="0.05"
                  min={0}
                  max={10}
                  value={Number((selCut.fade_in_s ?? 0).toFixed(2))}
                  onChange={(e) => updateCutEffect(selStem.id, segIdOf(selCut), { fade_in_s: Number(e.target.value) || 0 })}
                />
              </label>
              <label title="Fade-out seconds">
                <span>fade out</span>
                <input
                  type="number"
                  step="0.05"
                  min={0}
                  max={10}
                  value={Number((selCut.fade_out_s ?? 0).toFixed(2))}
                  onChange={(e) => updateCutEffect(selStem.id, segIdOf(selCut), { fade_out_s: Number(e.target.value) || 0 })}
                />
              </label>
              <label title="Playback speed (0.5 slow – 2.0 fast)">
                <span>speed</span>
                <input
                  type="range"
                  min={50}
                  max={200}
                  step={5}
                  value={Math.round((selCut.speed ?? 1) * 100)}
                  onChange={(e) => updateCutEffect(selStem.id, segIdOf(selCut), { speed: Number(e.target.value) / 100 })}
                />
                <span>{((selCut.speed ?? 1) * 100).toFixed(0)}%</span>
              </label>
              <label title="Pitch shift (-1200..+1200 cents)">
                <span>pitch</span>
                <input
                  type="range"
                  min={-1200}
                  max={1200}
                  step={50}
                  value={selCut.pitch_cents ?? 0}
                  onChange={(e) => updateCutEffect(selStem.id, segIdOf(selCut), { pitch_cents: Number(e.target.value) })}
                />
                <span>{selCut.pitch_cents ?? 0}¢</span>
              </label>
              <label title="Reverb wet amount (0 dry – 1 full wet)">
                <span>reverb</span>
                <input
                  type="range"
                  min={0}
                  max={100}
                  step={5}
                  value={Math.round((selCut.reverb_mix ?? 0) * 100)}
                  onChange={(e) => updateCutEffect(selStem.id, segIdOf(selCut), { reverb_mix: Number(e.target.value) / 100 })}
                />
                <span>{Math.round((selCut.reverb_mix ?? 0) * 100)}%</span>
              </label>
              <label title="Remove room reverb (spectral subtraction)">
                <span>dereverb</span>
                <input
                  type="checkbox"
                  checked={Boolean(selCut.dereverb)}
                  onChange={(e) => updateCutEffect(selStem.id, segIdOf(selCut), { dereverb: e.target.checked })}
                />
              </label>
              <button
                type="button"
                className="button button-ghost button-sm"
                onClick={() => void playRegion(selStem.id, segIdOf(selCut))}
              >
                {auditioning ? "Stop" : "Play"}
              </button>
              {invalidByStem[selStem.id] ? (
                <span className="render-warn">Fix cut ranges before saving.</span>
              ) : null}
            </div>
          </div>
        ) : selCue ? (
          <div className="mt-selection">
            <div className="mt-selection-head">
              {selCue.kind === "music" ? <MusicNotes size={14} /> : <SpeakerHigh size={14} />}
              <strong>{selCue.name}</strong>
              <span className="mt-selection-text" title={selCue.sfx_id}>
                {selCue.sfx_id}
                {selCueSpan ? ` · ${selCueSpan.start.toFixed(1)}–${selCueSpan.end.toFixed(1)}s` : ""}
                {cueDirty[selCue.id] ? " · unsaved" : ""}
              </span>
            </div>
            <div className="mt-selection-controls">
              <label>
                <span>anchor</span>
                <select
                  value={selCue.anchor_line_id}
                  onChange={(e) => updateCueLocal(selCue.id, { anchor_line_id: e.target.value })}
                  title="Which script line this cue is anchored to"
                >
                  {allLines.map((line) => (
                    <option key={line.id} value={line.id}>
                      {line.id} · {line.speaker}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span>offset</span>
                <input
                  type="number"
                  step="0.01"
                  value={Number(selCue.offset_s.toFixed(2))}
                  onChange={(e) =>
                    updateCueLocal(selCue.id, { offset_s: Number(e.target.value) || 0 })
                  }
                  title="Seconds after the anchor line starts (drag the clip or edit here)"
                />
              </label>
              <label>
                <span>gain</span>
                <input
                  type="range"
                  min={-48}
                  max={6}
                  step={1}
                  value={Math.round(selCue.gain_db)}
                  onChange={(e) => updateCueLocal(selCue.id, { gain_db: Number(e.target.value) })}
                  title={`${selCue.gain_db} dB`}
                  aria-label={`${selCue.name} gain`}
                />
                <span>{Math.round(selCue.gain_db)} dB</span>
              </label>
              <label>
                <span>kind</span>
                <select
                  value={selCue.kind}
                  onChange={(e) =>
                    updateCueLocal(selCue.id, { kind: e.target.value as Cue["kind"] })
                  }
                  title="bed/music run to the scene end, one-shots play once"
                >
                  <option value="one_shot">one-shot</option>
                  <option value="bed">bed</option>
                  <option value="music">music</option>
                </select>
              </label>
              <label title="Repeat the source until the scene ends">
                <span>loop</span>
                <input
                  type="checkbox"
                  checked={selCue.loop}
                  onChange={(e) => updateCueLocal(selCue.id, { loop: e.target.checked })}
                />
              </label>
              <label title="Duck under dialogue">
                <span>duck</span>
                <input
                  type="checkbox"
                  checked={selCue.duck}
                  onChange={(e) => updateCueLocal(selCue.id, { duck: e.target.checked })}
                />
              </label>
              <button
                type="button"
                className="button button-ghost button-sm"
                onClick={() => void auditionCue(selCue.id)}
                title="Play this cue alone"
              >
                Play
              </button>
              <button
                type="button"
                className="button button-ghost button-sm"
                onClick={() => void deleteSelectedCue()}
                title={confirmDeleteCueId === selCue.id ? "Click again to confirm" : "Delete this cue"}
              >
                <Trash size={14} />
                {confirmDeleteCueId === selCue.id ? "Confirm" : "Delete"}
              </button>
              {selCueMissing ? <span className="render-warn">SFX file missing.</span> : null}
            </div>
          </div>
        ) : selection ? (
          <div className="mt-selection">
            <div className="mt-selection-head">
              <strong>Marked section</strong>
              <span className="mt-selection-text">
                {selection.lineId} · {selection.start.toFixed(2)}–{selection.end.toFixed(2)}s
                ({Math.max(0, selection.end - selection.start).toFixed(2)}s inside the clip)
              </span>
            </div>
            <div className="mt-selection-controls">
              <button
                type="button"
                className="button button-primary button-sm"
                onClick={() => void cutOutSelection()}
                title="Remove the marked section and close the gap (Backspace/Delete)"
              >
                <Scissors size={14} />
                Cut out section
              </button>
              <button
                type="button"
                className="button button-ghost button-sm"
                onClick={() => {
                  setSelection(null);
                  selectionRef.current = null;
                }}
                title="Clear the mark (Escape)"
              >
                Clear
              </button>
              <span className="panel-sub">Save all writes the split clips.</span>
            </div>
          </div>
        ) : (
          <p className="panel-sub mt-hint">
            Cuts follow the script order. Click a clip to select it, drag its body to move
            the section, edges to trim, Alt/Ctrl-drag to slip the audio inside.
            Shift-drag inside a clip marks a section — Backspace/Delete cuts it out.
            Click a music/SFX clip to edit its anchor, offset and gain. Click empty
            space to seek.
          </p>
        )}
      </div>
    </div>
  );
}
