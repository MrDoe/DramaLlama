import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CaretLeft,
  CaretRight,
  CornersOut,
  FloppyDisk,
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
import type { Cue, Project, SfxItem, Stem, Take } from "../types";
import { formatDuration, speakerColor, speakerStyle } from "../utils";

interface Props {
  project: Project;
  onSaveCuts: (stem: Stem, cuts: Cut[], rebuildMix?: boolean) => void | Promise<void>;
  onSaveAll?: (stems: Record<string, Cut[]>) => void | Promise<void>;
  onSaveCues?: (cues: Cue[]) => void | Promise<void>;
  onDeleteCue?: (cue: Cue) => void | Promise<void>;
  onRefresh: () => void;
}

/** Minimum waveform height; grows to match the track card on the left. */
const TRACK_H = 120;
const RULER_H = 26;
const CUE_LANE_H = 64;
const EDGE_PX = 10;

const CUE_LANES: { id: CueLane; label: string; hint: string }[] = [
  { id: "music", label: "Music", hint: "music cues" },
  { id: "sfx", label: "SFX", hint: "beds and one-shots" },
];

/** Cue audio buffer key helpers. */
const cueLaneKey = (lane: CueLane) => `cue:${lane}`;
const cueBufferKey = (sfxId: string) => `sfx:${sfxId}`;

/** Clip fill for a track: the speaker's badge colour, kept translucent. */
function clipFill(speaker: string): string {
  const value = speakerColor(speaker).replace("#", "");
  const n = Number.parseInt(value, 16);
  if (Number.isNaN(n)) return "rgba(99, 102, 241, 0.25)";
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, 0.26)`;
}

/** `bbc_footsteps-_07041190` → `bbc_footsteps` (drop the dedup hash in labels). */
function sfxLabel(name: string): string {
  return name.replace(/[-_][0-9a-f]{8,}$/i, "").replace(/[-_]+$/, "") || name;
}

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
  mode: "trim-start" | "trim-end" | "slip" | "move" | "fade-in" | "fade-out";
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
  /** Multi-clip move: every selected segment and its original position. */
  group?: { stemId: string; idx: number; origPosition: number }[];
}

interface CueDragState {
  cueId: string;
  /** Global time under the cursor when the drag began. */
  grabGlobal: number;
  /** Cue offset before the drag. */
  origOffset: number;
  /** Intended start before the drag, for moving a freeform cue. */
  origStart: number;
  /** Explicit end before the drag, if the cue had one. */
  origEnd: number | null;
  /** Move `position_s` (a cue on the timeline) instead of `offset_s` (a cue on a line). */
  byPosition: boolean;
  /** Distance from the block's start to where it was grabbed, so it does not jump. */
  grabOffset: number;
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
            enabled: l.enabled ?? true,
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
    pushHistory(1000);
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
  // Multi-clip selection (Shift-click adds/removes). The last entry is the
  // primary clip used by the inspector.
  const [clipSelection, setClipSelection] = useState<
    { stemId: string; segId: string }[]
  >([]);
  const selected =
    clipSelection.length > 0 ? clipSelection[clipSelection.length - 1] : null;
  const setSelected = useCallback((value: { stemId: string; segId: string } | null) => {
    setClipSelection(value ? [value] : []);
  }, []);
  const isClipSelected = useCallback(
    (stemId: string, segId: string) =>
      clipSelection.some((c) => c.stemId === stemId && c.segId === segId),
    [clipSelection],
  );
  const toggleClipSelection = useCallback(
    (ref: { stemId: string; segId: string }, additive: boolean) => {
      setClipSelection((prev) => {
        if (!additive) return [ref];
        const exists = prev.some(
          (c) => c.stemId === ref.stemId && c.segId === ref.segId,
        );
        return exists
          ? prev.filter((c) => !(c.stemId === ref.stemId && c.segId === ref.segId))
          : [...prev, ref];
      });
    },
    [],
  );
  const [selection, setSelection] = useState<{
    stemId: string;
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
    anchorGlobal: number;
    trackStart: number;
    trackEnd: number;
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

  // Sync non-dirty pending cuts from saved alignments. Stems without an
  // alignment are seeded locally (even split); their savedCuts entries are
  // zero-length placeholders, so never overwrite them from the server.
  useEffect(() => {
    setPending((prev) => {
      const next: Record<string, Cut[]> = { ...prev };
      for (const stem of project.stems) {
        if (!project.alignments[stem.id]) continue;
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
        if (cut.enabled === false) continue; // disabled: dim + silent, slot kept
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

  // ---- insert sounds / music at the playhead (Suno library-dock equivalent)
  // Immediate server op like cue delete (not part of undo history).
  const [insertQuery, setInsertQuery] = useState("");
  const [insertItems, setInsertItems] = useState<SfxItem[]>([]);
  const [insertId, setInsertId] = useState("");
  const [insertKind, setInsertKind] = useState<"one_shot" | "bed" | "music">("one_shot");
  const [inserting, setInserting] = useState(false);

  useEffect(() => {
    const handle = window.setTimeout(() => {
      api.searchSfx(insertQuery, 8).then((items) => {
        setInsertItems(items);
        setInsertId((prev) =>
          items.some((item) => item.id === prev) ? prev : (items[0]?.id ?? ""),
        );
      }).catch(() => setInsertItems([]));
    }, 220);
    return () => window.clearTimeout(handle);
  }, [insertQuery]);

  const insertCueAtPlayhead = useCallback(async () => {
    const item = insertItems.find((i) => i.id === insertId);
    if (!item || inserting) return;
    const t = playheadRef.current;
    let anchor = "";
    let anchorStart = 0;
    for (const line of allLines) {
      const s = lineStarts.get(line.id);
      if (s == null || s > t) continue;
      if (anchor === "" || s >= anchorStart) {
        anchor = line.id;
        anchorStart = s;
      }
    }
    if (!anchor) return;
    setInserting(true);
    try {
      // Music goes in at the playhead as a timeline cue: it belongs to the piece,
      // not to whichever word happens to be under the cursor. Effects stay
      // anchored so they keep following the dialogue when it moves.
      const freeform = insertKind === "music";
      await api.addCue(project.id, {
        kind: insertKind,
        sfx_id: item.id,
        name: item.name,
        anchor_line_id: freeform ? null : anchor,
        position_s: freeform ? round2(Math.max(0, t)) : undefined,
        offset_s: freeform ? undefined : round2(Math.max(0, t - anchorStart)),
        gain_db: insertKind === "music" ? -18 : -12,
        loop: insertKind !== "one_shot",
        duck: true,
      });
      await onRefresh();
    } catch {
      // The page surfaces the error; keep the insert bar state for retry.
    } finally {
      setInserting(false);
    }
  }, [insertItems, insertId, insertKind, inserting, allLines, lineStarts, project.id, onRefresh]);

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

  /** Zoom in ±25% steps of the visible timeline, centered on the selection. */
  const zoomStep = useCallback(
    (direction: 1 | -1) => {
      if (total <= 0) return;
      const v = viewRef.current;
      const start = v?.start ?? 0;
      const end = v?.end ?? total;
      const span = Math.max(0.001, end - start);
      const pct = (total / span) * 100;
      const base = Math.round(pct / 25) * 25;
      const maxPct = Math.max(125, (total / 0.5) * 100);
      const target = clamp(100, maxPct, base + direction * 25);
      if (target <= 100) {
        setView(null);
        return;
      }
      const newSpan = Math.min(total, total / (target / 100));
      const center = (() => {
        if (!selected) return (start + end) / 2;
        const slot = slots.get(selected.segId);
        return slot ? (slot.start + slot.end) / 2 : (start + end) / 2;
      })();
      const s0 = clamp(0, Math.max(0, total - newSpan), center - newSpan / 2);
      setView({ start: s0, end: s0 + newSpan });
    },
    [total, selected, slots],
  );

  const zoomIn = useCallback(() => zoomStep(1), [zoomStep]);

  const zoomOut = useCallback(() => zoomStep(-1), [zoomStep]);

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
      ctx.fillStyle = "#0a0a10";
      ctx.fillRect(0, 0, w, h);

      const v = viewRef.current;
      const start = v?.start ?? 0;
      const end = v?.end ?? total;
      const span = Math.max(0.001, end - start);
      const toX = (t: number) => ((t - start) / span) * w;
      const drawPlayhead = () => {
        const px = toX(playheadRef.current);
        if (px < 0 || px > w) return;
        ctx.strokeStyle = "#f4f4f5";
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
        if (kind.ruler) {
          ctx.fillStyle = "rgba(255,255,255,0.55)";
          ctx.fillRect(x, 14, 1, 8);
          ctx.fillText(`${t.toFixed(tickStep < 1 ? 1 : 0)}s`, x + 3, 12);
        } else {
          // Grid line instead of repeating the time label on every row.
          ctx.fillStyle = "rgba(255,255,255,0.05)";
          ctx.fillRect(x, 0, 1, h);
          ctx.fillStyle = "rgba(255,255,255,0.22)";
          ctx.fillRect(x, h - 6, 1, 6);
        }
      }

      if (kind.ruler) {
        drawPlayhead();
        return;
      }

      if ("cueLane" in kind) {
        // Cue lanes: music and beds move on the timeline, one-shots move around
        // the line they are anchored to.
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
          if (item.offTimeline) {
            // Outside the play, pinned here so it can be dragged back. Marked,
            // because a silent block that looks like a cue is a lie: the mixer
            // says so in its warnings and the board should agree in public.
            ctx.save();
            ctx.setLineDash([4, 3]);
            ctx.strokeStyle = "rgba(248, 113, 113, 0.95)";
            ctx.lineWidth = 1.5;
            ctx.strokeRect(x0 + 0.75, 0.75, Math.max(2, wpx - 1.5), h - 1.5);
            ctx.restore();
          }

          if (info && info.peaks.length > 0 && item.sourceDuration > 0) {
            ctx.save();
            ctx.beginPath();
            ctx.rect(x0, 0, wpx, h);
            ctx.clip();
            ctx.strokeStyle = isSel
              ? "rgba(237, 233, 254, 0.9)"
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
              ? "#c4b5fd"
              : "rgba(255,255,255,0.75)";
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(Math.round(x0) + 0.5, 0);
          ctx.lineTo(Math.round(x0) + 0.5, h);
          ctx.moveTo(Math.round(x1) + 0.5, 0);
          ctx.lineTo(Math.round(x1) + 0.5, h);
          ctx.stroke();
          if (isSel) {
            ctx.strokeStyle = "rgba(167, 139, 250, 0.9)";
            ctx.strokeRect(x0 + 0.5, 0.5, wpx, h - 1);
          }

          ctx.save();
          ctx.beginPath();
          ctx.rect(x0, 0, wpx, h);
          ctx.clip();
          const base = `${item.cue.name}${item.loops ? " ↻" : ""}`;
          ctx.font = isSel ? "600 12px sans-serif" : "12px sans-serif";
          const full = missing ? `${base} · missing` : base;
          const lx = Math.max(x0 + 4, 4);
          const maxText = x1 - lx - 6;
          let label = full;
          if (maxText < 16) {
            label = "";
          } else {
            while (label.length > 1 && ctx.measureText(`${label}…`).width > maxText) {
              label = label.slice(0, -1);
            }
            if (label !== full) label = `${label}…`;
          }
          if (label) {
            const tw = ctx.measureText(label).width;
            const ly = 15;
            ctx.fillStyle = isSel ? "rgba(251,191,36,0.9)" : "rgba(0,0,0,0.65)";
            ctx.fillRect(lx - 3, ly - 12, tw + 6, 16);
            ctx.fillStyle = missing ? "#fecaca" : isSel ? "#000" : "#fff";
            ctx.fillText(label, lx, ly);
          }
          ctx.restore();
        }
        drawPlayhead();
        return;
      }

      const { stemId } = kind;
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
        const isSel = isClipSelected(stemId, segIdOf(cut));
        const isOff = cut.enabled === false;
        ctx.globalAlpha = isOff ? 0.45 : 1;

        ctx.fillStyle = clipFill(stem?.speaker ?? "");
        ctx.fillRect(x0, 0, wpx, h);

        const len = cut.end_s - cut.start_s;
        if (len > 0 && peaks.length > 0) {
          const local0 = Math.max(0, cut.start_s);
          const local1 = Math.min(takeDur, cut.end_s);
          if (local1 > local0) {
            const i0 = Math.max(0, Math.floor((local0 / takeDur) * peaks.length));
            const i1 = Math.min(peaks.length, Math.ceil((local1 / takeDur) * peaks.length));
            ctx.strokeStyle = isSel ? "rgba(237, 233, 254, 0.85)" : "rgba(226, 232, 240, 0.55)";
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

        ctx.strokeStyle = isSel ? "#c4b5fd" : "rgba(255,255,255,0.9)";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(Math.round(x0) + 0.5, 0);
        ctx.lineTo(Math.round(x0) + 0.5, h);
        ctx.moveTo(Math.round(x1) + 0.5, 0);
        ctx.lineTo(Math.round(x1) + 0.5, h);
        ctx.stroke();
        if (isSel) {
          ctx.strokeStyle = "rgba(167, 139, 250, 0.9)";
          ctx.strokeRect(x0 + 0.5, 0.5, wpx, h - 1);
          // Fade ramps + corner grab handles (drag the top corners).
          const slotDur = Math.max(0.001, slot.end - slot.start);
          const inPx = Math.min(wpx, ((cut.fade_in_s ?? 0) / slotDur) * wpx);
          const outPx = Math.min(wpx, ((cut.fade_out_s ?? 0) / slotDur) * wpx);
          ctx.strokeStyle = "rgba(196, 181, 253, 0.95)";
          ctx.lineWidth = 1.5;
          ctx.beginPath();
          ctx.moveTo(x0, h - 1);
          ctx.lineTo(x0 + inPx, 1);
          ctx.moveTo(x1, h - 1);
          ctx.lineTo(x1 - outPx, 1);
          ctx.stroke();
          ctx.fillStyle = "rgba(196, 181, 253, 0.95)";
          ctx.fillRect(x0, 0, 5, 5);
          ctx.fillRect(x1 - 5, 0, 5, 5);
        }
        const takeNo = stem
          ? stem.takes.findIndex((item) => item.id === take.id) + 1
          : 0;
        const showTake = (stem?.takes.length ?? 0) > 1 && takeNo > 0;
        const fullLabel = `${lineNo.get(cut.line_id) ?? "?"} ${cut.line_id}${
          showTake ? ` · T${takeNo}` : ""
        }${isOff ? " · off" : ""}`;
        ctx.font = isSel ? "600 12px sans-serif" : "12px sans-serif";
        const lx = Math.max(x0 + 6, 6);
        const maxText = Math.min(x0 + wpx, w) - lx - 6;
        let label = fullLabel;
        if (maxText < 20) {
          label = "";
        } else {
          while (label.length > 1 && ctx.measureText(`${label}…`).width > maxText) {
            label = label.slice(0, -1);
          }
          if (label !== fullLabel) label = `${label}…`;
        }
        if (label) {
          const tw = ctx.measureText(label).width;
          const row = idx % 2;
          const ly = 14 + row * 18;
          // Clip to the clip rect so a label never spills into its neighbour.
          ctx.save();
          ctx.beginPath();
          ctx.rect(x0, 0, wpx, h);
          ctx.clip();
          ctx.fillStyle = isSel ? "rgba(251,191,36,0.9)" : "rgba(0,0,0,0.65)";
          const pad = 3;
          ctx.fillRect(lx - pad, ly - 12, tw + pad * 2, 16);
          ctx.fillStyle = isSel ? "#000" : "#fff";
          ctx.fillText(label, lx, ly);
          ctx.restore();
        }
        ctx.globalAlpha = 1;
      });

      // Free range selection across the whole track (not tied to one clip).
      if (selection && selection.stemId === stemId && selection.end > selection.start) {
        const sx0 = toX(selection.start);
        const sx1 = toX(selection.end);
        if (sx1 > sx0) {
          ctx.fillStyle = "rgba(167, 139, 250, 0.24)";
          ctx.fillRect(sx0, 0, sx1 - sx0, h);
          ctx.strokeStyle = "rgba(196, 181, 253, 0.95)";
          ctx.lineWidth = 1;
          ctx.strokeRect(sx0 + 0.5, 0.5, sx1 - sx0 - 1, h - 1);
        }
      }

      drawPlayhead();
    },
    [
      total,
      loaded,
      pending,
      selected,
      isClipSelected,
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

  // ---- undo / redo + clipboard (Suno parity: Ctrl+Z/Y, D/C/X/V) -----------

  interface HistoryEntry {
    pending: Record<string, Cut[]>;
    cues: Cue[];
    dirty: Record<string, boolean>;
    cueDirty: Record<string, boolean>;
  }
  const pastRef = useRef<HistoryEntry[]>([]);
  const futureRef = useRef<HistoryEntry[]>([]);
  const clipboardRef = useRef<{ stemId: string; cut: Cut }[] | null>(null);
  const lastPushRef = useRef(0);

  /** Snapshot before a mutating action; redo stack clears on new edits.
   * Pass coalesceMs for continuous inputs (sliders, drags) so one gesture
   * stays a single undo step. */
  const pushHistory = useCallback((coalesceMs = 0) => {
    const now = Date.now();
    if (coalesceMs > 0 && now - lastPushRef.current < coalesceMs) return;
    lastPushRef.current = now;
    pastRef.current.push({
      pending: structuredClone(pending),
      cues: structuredClone(cues),
      dirty: { ...dirty },
      cueDirty: { ...cueDirty },
    });
    if (pastRef.current.length > 50) pastRef.current.shift();
    futureRef.current = [];
  }, [pending, cues, dirty, cueDirty]);

  const restoreHistory = useCallback(
    (entry: HistoryEntry) => {
      setPending(entry.pending);
      // Cue membership is server truth (inserts/deletes apply immediately):
      // restore field edits for surviving ids, keep newly added cues.
      const entryById = new Map(entry.cues.map((c) => [c.id, c]));
      setCues(project.cues.map((c) => entryById.get(c.id) ?? c));
      setDirty({ ...entry.dirty });
      setCueDirty({ ...entry.cueDirty });
      setSelection(null);
      selectionRef.current = null;
    },
    [project.cues],
  );

  const undoEdit = useCallback(() => {
    if (pastRef.current.length === 0) return false;
    futureRef.current.push({
      pending: structuredClone(pending),
      cues: structuredClone(cues),
      dirty: { ...dirty },
      cueDirty: { ...cueDirty },
    });
    restoreHistory(pastRef.current.pop()!);
    return true;
  }, [pending, cues, dirty, cueDirty, restoreHistory]);

  const redoEdit = useCallback(() => {
    if (futureRef.current.length === 0) return false;
    pastRef.current.push({
      pending: structuredClone(pending),
      cues: structuredClone(cues),
      dirty: { ...dirty },
      cueDirty: { ...cueDirty },
    });
    restoreHistory(futureRef.current.pop()!);
    return true;
  }, [pending, cues, dirty, cueDirty, restoreHistory]);

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
      pushHistory(1000);
      setCues((prev) =>
        prev.map((cue) => (cue.id === cueId ? { ...cue, ...patch } : cue)),
      );
      setCueDirty((prev) => ({ ...prev, [cueId]: true }));
    },
    [pushHistory],
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
      pushHistory();
      // Beds and music are not events that happen *at* a word, so dragging one
      // puts it on the timeline: the move writes `position_s` and drops the line
      // anchor. One-shots keep dragging by offset, because a footstep should
      // still follow its line when the dialogue reflows.
      const byPosition = item.freeform || item.cue.kind !== "one_shot";
      cueDragRef.current = {
        cueId: item.cue.id,
        grabGlobal: globalT,
        origOffset: item.cue.offset_s,
        // An off-timeline cue is drawn pinned to the edge; grab it from there,
        // so dragging left pulls it back into the play instead of throwing it
        // further out by its real (invisible) offset.
        origStart: item.offTimeline ? item.start : item.anchor,
        origEnd: item.cue.end_s ?? null,
        byPosition,
        grabOffset: Math.max(0, globalT - item.start),
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
      if (drag.byPosition) {
        const raw = drag.origStart + delta;
        // Shift snaps to quarter-seconds for musical placement.
        const next = e.shiftKey ? Math.round(raw * 4) / 4 : round2(raw);
        const patch: Partial<Omit<Cue, "id">> = {
          position_s: Math.max(0, next),
          anchor_line_id: null,
        };
        // A cue with a stated end keeps its length; one without runs to the end
        // of the play wherever it is put, which is what a bed is for.
        if (drag.origEnd != null) patch.end_s = Math.max(0.05, round2(drag.origEnd + delta));
        updateCueLocal(drag.cueId, patch);
      } else {
        const raw = drag.origOffset + delta;
        const next = e.shiftKey ? Math.round(raw * 4) / 4 : round2(raw);
        updateCueLocal(drag.cueId, { offset_s: next });
      }
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
      pushHistory();
      commitCuts(stem.id, cuts);
    },
    [pending, takeByIdFor, takeDurationOf, commitCuts, pushHistory],
  );

  /** Rebind clips that reference deleted takes to each stem's selected take. */
  const reassignMissingCuts = useCallback(() => {
    const byStem = new Map<string, { line_id: string; takeId: string }[]>();
    for (const item of missingTakes) {
      const list = byStem.get(item.stemId) ?? [];
      list.push({ line_id: item.lineId, takeId: item.takeId });
      byStem.set(item.stemId, list);
    }
    if (byStem.size === 0) return;
    pushHistory();
    let changed = 0;
    for (const [stemId, items] of byStem) {
      const stem = stemById.get(stemId);
      if (!stem) continue;
      const target =
        stem.takes.find((take) => take.selected) ?? stem.takes[0] ?? null;
      if (!target) continue;
      const targetDur = takeDurationOf(target) || target.duration_s;
      const missingSegs = new Set(items.map((i) => i.line_id));
      // A stem can contain multiple segments per line; reassign any missing one.
      const cuts = (pending[stemId] ?? []).map((cut) => {
        if (!(cut.take_id && !stem.takes.some((t) => t.id === cut.take_id))) {
          return { ...cut };
        }
        if (!missingSegs.has(cut.line_id) && missingSegs.size > 0) {
          // still missing by take id even if not listed (defensive)
        }
        const prevDur = 0;
        let start = cut.start_s;
        let end = cut.end_s;
        if (prevDur > 0 && targetDur > 0) {
          const ratio = targetDur / prevDur;
          start = cut.start_s * ratio;
          end = cut.end_s * ratio;
        } else if (targetDur > 0) {
          const count = Math.max(1, stem.line_ids.length);
          const pos = Math.max(0, stem.line_ids.indexOf(cut.line_id));
          start = (targetDur / count) * pos;
          end = (targetDur / count) * (pos + 1);
        }
        start = clamp(0, Math.max(0, targetDur), start);
        end = clamp(0, Math.max(0, targetDur), end);
        if (targetDur > 0 && end - start < 0.05) {
          end = Math.min(targetDur, start + 0.05);
          start = Math.max(0, end - 0.05);
        }
        changed += 1;
        return {
          ...cut,
          take_id: target.id,
          start_s: round2(start),
          end_s: round2(end),
        };
      });
      commitCuts(stemId, cuts);
    }
  }, [missingTakes, pending, stemById, takeDurationOf, commitCuts, pushHistory]);

  const findFadeHandle = useCallback(
    (stemId: string, canvas: HTMLCanvasElement, clientX: number, clientY: number) => {
      const rect = canvas.getBoundingClientRect();
      const w = Math.max(1, rect.width);
      const v = viewRef.current;
      const start = v?.start ?? 0;
      const end = v?.end ?? total;
      const span = Math.max(0.001, end - start);
      const x = clientX - rect.left;
      const y = clientY - rect.top;
      if (y > 16) return null;
      for (const cut of pending[stemId] ?? []) {
        const segId = segIdOf(cut);
        if (!isClipSelected(stemId, segId)) continue;
        const slot = slots.get(segId);
        if (!slot || slot.end <= slot.start) continue;
        const xs = ((slot.start - start) / span) * w;
        const xe = ((slot.end - start) / span) * w;
        if (Math.abs(x - xs) <= 12) return { segId, mode: "fade-in" as const };
        if (Math.abs(x - xe) <= 12) return { segId, mode: "fade-out" as const };
      }
      return null;
    },
    [pending, slots, total, isClipSelected],
  );

  // Transient feedback so a shortcut never fails silently.
  const [actionHint, setActionHint] = useState<string | null>(null);
  const hintTimerRef = useRef<number | null>(null);
  const flashHint = useCallback((message: string) => {
    setActionHint(message);
    if (hintTimerRef.current != null) window.clearTimeout(hintTimerRef.current);
    hintTimerRef.current = window.setTimeout(() => setActionHint(null), 3000);
  }, []);

  const onTrackPointerDown = (e: React.PointerEvent, stemId: string) => {
    if (e.button !== 0) return;
    setSelectedCueId(null);
    setConfirmDeleteCueId(null);
    setContextMenu(null);
    const canvas = e.currentTarget as HTMLCanvasElement;
    try { canvas.setPointerCapture(e.pointerId); } catch { /* synthetic pointer */ }
    const globalT = toGlobal(canvas, e.clientX);
    const cuts = pending[stemId] ?? [];

    const startRange = () => {
      if (total <= 0) return;
      const s = clamp(0, total, globalT);
      selectionRef.current = {
        stemId,
        anchorGlobal: s,
        trackStart: 0,
        trackEnd: total,
      };
      setSelection({ stemId, start: s, end: s });
    };

    const regionIdx = findRegion(stemId, globalT);
    const regionCut = regionIdx != null ? cuts[regionIdx] : null;

    // Shift-click a clip: toggle it in the multi-selection (Suno rA/rL).
    if (e.shiftKey && regionCut) {
      toggleClipSelection({ stemId, segId: segIdOf(regionCut) }, true);
      return;
    }

    // Fade handles: top corners of an already-selected clip.
    const fade = findFadeHandle(stemId, canvas, e.clientX, e.clientY);
    if (fade) {
      const cut = cuts.find((c) => segIdOf(c) === fade.segId);
      const slot = cut ? slots.get(fade.segId) : null;
      if (cut && slot) {
        pushHistory();
        dragRef.current = {
          stemId,
          idx: cuts.indexOf(cut),
          mode: fade.mode,
          anchorGlobal: slot.start,
          grabLocal: 0,
          grabGlobal: globalT,
          origStart: cut.start_s,
          origEnd: cut.end_s,
          origPosition: slot.start,
        };
        return;
      }
    }

    // Trim/slip/move only act on the already-selected clip.
    const edge = findEdge(stemId, canvas, e.clientX);
    if (edge && regionCut && isClipSelected(stemId, segIdOf(regionCut))) {
      const cut = cuts[edge.idx];
      const slot = cut ? slots.get(segIdOf(cut)) : null;
      if (cut && slot) {
        pushHistory();
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
        return;
      }
    }

    const cut = regionCut;
    const stem = stemById.get(stemId) ?? null;
    const dur = takeDurationOf(takeByIdFor(stem, cut?.take_id));
    const slot = cut ? (slots.get(segIdOf(cut)) ?? null) : null;
    if (cut && slot && dur > 0 && isClipSelected(stemId, segIdOf(cut))) {
      if (e.altKey || e.ctrlKey || e.metaKey) {
        pushHistory();
        dragRef.current = {
          stemId,
          idx: regionIdx ?? -1,
          mode: "slip",
          anchorGlobal: slot.start,
          grabLocal: cut.start_s + (globalT - slot.start),
          grabGlobal: globalT,
          origStart: cut.start_s,
          origEnd: cut.end_s,
          origPosition: slot.start,
        };
        return;
      }
      // Move the whole selection together (Suno moves multi-clips).
      const group: { stemId: string; idx: number; origPosition: number }[] = [];
      for (const ref of clipSelection) {
        const refCuts = pending[ref.stemId] ?? [];
        const i = refCuts.findIndex((c) => segIdOf(c) === ref.segId);
        const refSlot = slots.get(ref.segId);
        if (i < 0) continue;
        group.push({
          stemId: ref.stemId,
          idx: i,
          origPosition: refCuts[i].position_s ?? refSlot?.start ?? 0,
        });
      }
      pushHistory();
      dragRef.current = {
        stemId,
        idx: regionIdx ?? -1,
        mode: "move",
        anchorGlobal: slot.start,
        grabLocal: cut.start_s + (globalT - slot.start),
        grabGlobal: globalT,
        origStart: cut.start_s,
        origEnd: cut.end_s,
        origPosition: cut.position_s ?? slot.start,
        group: group.length ? group : undefined,
      };
      return;
    }

    // Rule: with no clip selected, a drag draws a free range.
    startRange();
  };

  const onTrackPointerMove = (e: React.PointerEvent, stemId: string) => {
    const canvas = e.currentTarget as HTMLCanvasElement;
    const marking = selectionRef.current;
    if (marking && marking.stemId === stemId && !dragRef.current) {
      const globalT = toGlobal(canvas, e.clientX);
      const t = clamp(marking.trackStart, marking.trackEnd, globalT);
      const s = Math.min(marking.anchorGlobal, t);
      const en = Math.max(marking.anchorGlobal, t);
      setSelection({ stemId: marking.stemId, start: round2(s), end: round2(en) });
      canvas.style.cursor = "text";
      return;
    }
    const drag = dragRef.current;
    if (!drag || drag.stemId !== stemId) {
      if (!drag && !marking) {
        const region = findRegion(stemId, toGlobal(canvas, e.clientX));
        if (region != null) {
          const cut = (pending[stemId] ?? [])[region];
          const marked = cut != null && isClipSelected(stemId, segIdOf(cut));
          const fade = marked ? findFadeHandle(stemId, canvas, e.clientX, e.clientY) : null;
          if (fade) {
            canvas.style.cursor = "ew-resize";
            return;
          }
          const edge = marked ? findEdge(stemId, canvas, e.clientX) : null;
          if (edge) {
            canvas.style.cursor = "ew-resize";
            return;
          }
          canvas.style.cursor = marked ? "move" : "pointer";
          return;
        }
        canvas.style.cursor = "default";
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

    if (drag.mode === "fade-in" || drag.mode === "fade-out") {
      const len = Math.max(0, (cut.end_s - cut.start_s) / (cut.speed && cut.speed > 0 ? cut.speed : 1));
      if (drag.mode === "fade-in") {
        cut.fade_in_s = round2(clamp(0, Math.min(10, len), globalT - drag.anchorGlobal));
      } else {
        cut.fade_out_s = round2(clamp(0, Math.min(10, len), drag.anchorGlobal + len - globalT));
      }
      canvas.style.cursor = "ew-resize";
      commitCuts(stemId, cuts);
      return;
    }

    if (drag.mode === "trim-start") {
      cut.start_s = round2(clamp(0, cut.end_s - 0.05, local));
      canvas.style.cursor = "ew-resize";
      commitCuts(stemId, cuts);
      return;
    }
    if (drag.mode === "trim-end") {
      cut.end_s = round2(clamp(cut.start_s + 0.05, dur, local));
      canvas.style.cursor = "ew-resize";
      commitCuts(stemId, cuts);
      return;
    }
    if (drag.mode === "slip") {
      const len = drag.origEnd - drag.origStart;
      const shiftMin = -drag.origStart;
      const shiftMax = dur - drag.origEnd;
      const shift = clamp(shiftMin, shiftMax, local - drag.grabLocal);
      cut.start_s = round2(drag.origStart + shift);
      cut.end_s = round2(drag.origStart + shift + len);
      canvas.style.cursor = "move";
      commitCuts(stemId, cuts);
      return;
    }

    // move: apply the same delta to every selected clip.
    const delta = globalT - drag.grabGlobal;
    const updates: Record<string, Cut[]> = { [stemId]: cuts };
    for (const g of drag.group ?? []) {
      if (g.stemId === stemId && g.idx === drag.idx) continue;
      const arr = updates[g.stemId] ?? (pending[g.stemId] ?? []).map((c) => ({ ...c }));
      const item = arr[g.idx];
      if (!item) continue;
      item.position_s = round2(Math.max(0, g.origPosition + delta));
      updates[g.stemId] = arr;
    }
    const primary = updates[stemId][drag.idx];
    if (primary) primary.position_s = round2(Math.max(0, drag.origPosition + delta));
    canvas.style.cursor = "grabbing";
    for (const [sid, arr] of Object.entries(updates)) commitCuts(sid, arr);
  };

  const onTrackPointerUp = (e: React.PointerEvent) => {
    const canvas = e.currentTarget as HTMLCanvasElement;
    try { canvas.releasePointerCapture(e.pointerId); } catch { /* synthetic pointer */ }
    const marking = selectionRef.current;
    if (marking) {
      const cur = selection;
      if (!cur || Math.abs(cur.end - cur.start) < 0.05) {
        setSelection(null);
        const t = toGlobal(canvas, e.clientX);
        const idx = findRegion(marking.stemId, t);
        const cut = idx != null ? (pending[marking.stemId] ?? [])[idx] : null;
        if (cut) {
          toggleClipSelection({ stemId: marking.stemId, segId: segIdOf(cut) }, false);
        } else {
          setSelected(null);
          if (total > 0) seek(clamp(0, total, t));
        }
      }
      selectionRef.current = null;
    }
    dragRef.current = null;
  };

  const onRulerPointerDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    seek(clamp(0, total, toGlobal(e.currentTarget as HTMLCanvasElement, e.clientX)));
  };

  /** Double-click the ruler: open the clip under that time in the dock. */
  const onRulerDoubleClick = (e: React.MouseEvent) => {
    const t = toGlobal(e.currentTarget as HTMLCanvasElement, e.clientX);
    for (const stem of project.stems) {
      const idx = findRegion(stem.id, t);
      if (idx == null) continue;
      const cut = (pending[stem.id] ?? [])[idx];
      if (!cut) continue;
      setSelected({ stemId: stem.id, segId: segIdOf(cut) });
      seek(clamp(0, total, t));
      requestAnimationFrame(() => {
        document.getElementById("clip-dock")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
      return;
    }
    flashHint("Double-click the ruler on a clip to open its editor.");
  };

  // Right-click clip menu (Suno onContextMenu).
  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
    stemId: string;
    segId: string;
  } | null>(null);

  const onTrackContextMenu = (e: React.MouseEvent, stemId: string) => {
    e.preventDefault();
    const canvas = e.currentTarget as HTMLCanvasElement;
    const t = toGlobal(canvas, e.clientX);
    const idx = findRegion(stemId, t);
    const cut = idx != null ? (pending[stemId] ?? [])[idx] : null;
    if (!cut) {
      setContextMenu(null);
      return;
    }
    const segId = segIdOf(cut);
    if (!isClipSelected(stemId, segId)) setSelected({ stemId, segId });
    setContextMenu({ x: e.clientX, y: e.clientY, stemId, segId });
  };

  /** Ctrl+E / Delete: cut a free timeline range out of the marked track.
   * Every segment the range touches keeps its head/tail; fully covered
   * segments are dropped; later segments ripple left by the removed span. */
  const cutOutRange = useCallback(() => {
    if (!selection) {
      flashHint("Mark a range first: LMB-drag on the timeline (no clip selected).");
      return false;
    }
    const { stemId, start, end } = selection;
    const span = end - start;
    if (span < 0.02) {
      flashHint("Marked range is too short to cut.");
      return false;
    }
    const cuts = pending[stemId] ?? [];
    const next: Cut[] = [];
    let removed = false;
    for (const cut of cuts) {
      const slot = slots.get(segIdOf(cut));
      if (!slot || slot.end <= slot.start) {
        next.push({ ...cut });
        continue;
      }
      const s = slot.start;
      const e = slot.end;
      if (e <= start + 1e-6) {
        next.push({ ...cut });
        continue;
      }
      if (s >= end - 1e-6) {
        // After the range: ripple left by the removed span.
        next.push({
          ...cut,
          position_s: round2(Math.max(0, (cut.position_s ?? s) - span)),
        });
        continue;
      }
      removed = true;
      const slotLen = e - s;
      const toLocal = (t: number) =>
        cut.start_s + ((t - s) / slotLen) * (cut.end_s - cut.start_s);
      if (s < start - 0.01) {
        next.push({
          ...cut,
          start_s: round2(cut.start_s),
          end_s: round2(toLocal(start)),
          position_s: round2(cut.position_s ?? s),
        });
      }
      if (e > end + 0.01) {
        next.push({
          ...cut,
          id: `${cut.id ?? cut.line_id}__${Date.now()}`,
          start_s: round2(toLocal(end)),
          end_s: round2(cut.end_s),
          position_s: round2(start),
        });
      }
    }
    if (!removed) {
      flashHint("Nothing to cut — the range does not overlap a clip on this track.");
      return false;
    }
    pushHistory();
    commitCuts(stemId, next);
    setSelection(null);
    setSelected(null);
    flashHint(`Cut ${span.toFixed(2)}s out of the timeline.`);
    return true;
  }, [selection, pending, slots, pushHistory, commitCuts, flashHint]);

  // ---- Suno-parity clip actions (key-driven) -------------------------------

  // Fullscreen: native where allowed, CSS-fixed fallback otherwise.
  const pageRef = useRef<HTMLDivElement>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);

  useEffect(() => {
    const onChange = () =>
      setIsFullscreen(
        Boolean(pageRef.current && document.fullscreenElement === pageRef.current),
      );
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  const toggleFullscreen = useCallback(() => {
    const node = pageRef.current;
    if (!node) return;
    if (isFullscreen || document.fullscreenElement === node) {
      if (document.fullscreenElement === node) void document.exitFullscreen();
      setIsFullscreen(false);
      return;
    }
    // The class guarantees the layout even when the API is blocked;
    // requestFullscreen adds the real fullscreen window on top.
    setIsFullscreen(true);
    node.requestFullscreen?.().catch(() => {});
  }, [isFullscreen]);

  // The CSS fallback must escape the animated page wrapper: hide the app
  // chrome so the fixed editor resolves against the viewport.
  useEffect(() => {
    document.body.classList.toggle("mt-fs-active", isFullscreen);
    return () => document.body.classList.remove("mt-fs-active");
  }, [isFullscreen]);

  const [showShortcuts, setShowShortcuts] = useState(false);

  /** Suno Cmd/Ctrl+D: duplicate the selected segment right after itself. */
  /** Suno Cmd/Ctrl+D: duplicate every selected clip right after itself. */
  const duplicateSelected = useCallback(() => {
    if (clipSelection.length === 0) {
      flashHint("Ctrl+D: select a clip first (click it or use ↑/↓).");
      return false;
    }
    pushHistory();
    const picks = [...clipSelection].sort((a, b) => {
      if (a.stemId !== b.stemId) return a.stemId < b.stemId ? -1 : 1;
      return 0;
    });
    const created: { stemId: string; segId: string }[] = [];
    for (const ref of picks) {
      const cuts = (pending[ref.stemId] ?? []).map((c) => ({ ...c }));
      const idx = cuts.findIndex((c) => segIdOf(c) === ref.segId);
      if (idx < 0) continue;
      const cut = cuts[idx];
      const slot = slots.get(ref.segId);
      const dup: Cut = { ...cut, id: `${cut.id ?? cut.line_id}__${Date.now()}` };
      if (slot) dup.position_s = round2((cut.position_s ?? slot.start) + (slot.end - slot.start));
      cuts.splice(idx + 1, 0, dup);
      commitCuts(ref.stemId, cuts);
      created.push({ stemId: ref.stemId, segId: segIdOf(dup) });
    }
    if (created.length) setClipSelection(created);
    return true;
  }, [clipSelection, pending, slots, pushHistory, commitCuts, flashHint, setClipSelection]);

  /** Suno 0: dim + silence the selected clip (keeps its slot timing). */
  /** Suno 0: dim + silence every selected clip (keeps slot timing). */
  const toggleSelectedEnabled = useCallback(() => {
    if (clipSelection.length === 0) {
      flashHint("0: select a clip first (click it or use ↑/↓).");
      return false;
    }
    pushHistory();
    const byStem = new Map<string, Set<string>>();
    for (const ref of clipSelection) {
      const set = byStem.get(ref.stemId) ?? new Set<string>();
      set.add(ref.segId);
      byStem.set(ref.stemId, set);
    }
    for (const [stemId, segIds] of byStem) {
      const cuts = (pending[stemId] ?? []).map((c) =>
        segIds.has(segIdOf(c)) ? { ...c, enabled: c.enabled === false } : { ...c },
      );
      commitCuts(stemId, cuts);
    }
    return true;
  }, [clipSelection, pending, pushHistory, commitCuts, flashHint]);

  /** Suno ←/→: nudge the selected section along the timeline. */
  /** Suno ←/→: nudge every selected clip along the timeline. */
  const nudgeSelected = useCallback((delta: number) => {
    if (clipSelection.length === 0) return false;
    pushHistory();
    const byStem = new Map<string, Set<string>>();
    for (const ref of clipSelection) {
      const set = byStem.get(ref.stemId) ?? new Set<string>();
      set.add(ref.segId);
      byStem.set(ref.stemId, set);
    }
    for (const [stemId, segIds] of byStem) {
      const cuts = (pending[stemId] ?? []).map((c) => {
        if (!segIds.has(segIdOf(c))) return { ...c };
        const slot = slots.get(segIdOf(c));
        const base = c.position_s ?? slot?.start ?? 0;
        return { ...c, position_s: round2(Math.max(0, base + delta)) };
      });
      commitCuts(stemId, cuts);
    }
    return true;
  }, [clipSelection, pending, slots, pushHistory, commitCuts]);

  /** Move the marked range, or extend its end (Suno Shift+←/→). */
  const shiftMark = useCallback((delta: number, extend: boolean) => {
    if (!selection) {
      flashHint("Mark a range first: drag on the timeline (no clip selected).");
      return false;
    }
    if (extend) {
      const end = clamp(selection.start + 0.01, total, round2(selection.end + delta));
      if (end - selection.start < 0.01) return false;
      setSelection({ ...selection, end });
    } else {
      const len = selection.end - selection.start;
      const start = clamp(0, Math.max(0, total - len), round2(selection.start + delta));
      setSelection({ ...selection, start, end: round2(start + len) });
    }
    return true;
  }, [selection, total, flashHint]);

  /** Suno ↑/↓: move the selection across clips (same-stem order here). */
  const cycleSelected = useCallback((direction: 1 | -1) => {
    const stemId = selected?.stemId ?? project.stems[0]?.id;
    if (!stemId) return false;
    const cuts = pending[stemId] ?? [];
    if (cuts.length === 0) return false;
    const order = cuts
      .map((c, i) => ({ segId: segIdOf(c), start: slots.get(segIdOf(c))?.start ?? 0, i }))
      .sort((a, b) => a.start - b.start || a.i - b.i);
    const cur = selected ? order.findIndex((o) => o.segId === selected.segId) : -1;
    const next = order[(cur + direction + order.length) % order.length];
    setSelected({ stemId, segId: next.segId });
    return true;
  }, [selected, pending, slots, project]);

  /** Suno Shift+M / Shift+S on the selected track. */
  const toggleMuteSolo = useCallback((kind: "mute" | "solo") => {
    const stemId = selected?.stemId ?? project.stems[0]?.id;
    if (!stemId) return false;
    if (kind === "mute") {
      setMuted((p) => {
        const n = new Set(p);
        if (n.has(stemId)) n.delete(stemId);
        else n.add(stemId);
        return n;
      });
    } else {
      setSolo((p) => {
        const n = new Set(p);
        if (n.has(stemId)) n.delete(stemId);
        else n.add(stemId);
        return n;
      });
    }
    return true;
  }, [selected, project]);

  /** Suno Shift+F: toggle a short fade on the selected clip. */
  /** Suno Shift+F: toggle a short fade on every selected clip. */
  const toggleFadeSelected = useCallback(() => {
    if (clipSelection.length === 0) {
      flashHint("Shift+F: select a clip first (click it or use ↑/↓).");
      return false;
    }
    pushHistory();
    const byStem = new Map<string, Set<string>>();
    for (const ref of clipSelection) {
      const set = byStem.get(ref.stemId) ?? new Set<string>();
      set.add(ref.segId);
      byStem.set(ref.stemId, set);
    }
    for (const [stemId, segIds] of byStem) {
      const cuts = (pending[stemId] ?? []).map((c) => {
        if (!segIds.has(segIdOf(c))) return { ...c };
        const on = (c.fade_in_s ?? 0) > 0 || (c.fade_out_s ?? 0) > 0;
        return { ...c, fade_in_s: on ? 0 : 0.1, fade_out_s: on ? 0 : 0.1 };
      });
      commitCuts(stemId, cuts);
    }
    return true;
  }, [clipSelection, pending, pushHistory, commitCuts, flashHint]);

  const copySelected = useCallback(() => {
    if (clipSelection.length === 0) {
      flashHint("Ctrl+C: select a clip first (click it or use ↑/↓).");
      return false;
    }
    clipboardRef.current = clipSelection
      .map((ref) => {
        const cut = (pending[ref.stemId] ?? []).find((c) => segIdOf(c) === ref.segId);
        return cut ? { stemId: ref.stemId, cut: { ...cut } } : null;
      })
      .filter((v): v is { stemId: string; cut: Cut } => v != null);
    flashHint(`Copied ${clipboardRef.current.length} clip(s) — Ctrl+V pastes them.`);
    return true;
  }, [clipSelection, pending, flashHint]);

  const pasteClipboard = useCallback(() => {
    const clips = clipboardRef.current;
    if (!clips || clips.length === 0) {
      flashHint("Ctrl+V: copy a clip first (Ctrl+C).");
      return false;
    }
    pushHistory();
    const created: { stemId: string; segId: string }[] = [];
    for (const entry of clips) {
      const stem = stemById.get(entry.stemId) ?? null;
      if (!stem || !stem.line_ids.includes(entry.cut.line_id)) continue;
      const cuts = (pending[entry.stemId] ?? []).map((c) => ({ ...c }));
      const afterIdx = selected?.stemId === entry.stemId
        ? cuts.findIndex((c) => segIdOf(c) === selected.segId)
        : cuts.length - 1;
      const anchor = afterIdx >= 0 ? cuts[afterIdx] : null;
      const anchorSlot = anchor ? slots.get(segIdOf(anchor)) : null;
      const dup: Cut = {
        ...entry.cut,
        id: `${entry.cut.id ?? entry.cut.line_id}__${Date.now()}`,
        position_s: anchorSlot ? round2(anchorSlot.end) : entry.cut.position_s,
      };
      const at = afterIdx >= 0 ? afterIdx + 1 : cuts.length;
      cuts.splice(at, 0, dup);
      commitCuts(entry.stemId, cuts);
      created.push({ stemId: entry.stemId, segId: segIdOf(dup) });
    }
    if (created.length) setClipSelection(created);
    return created.length > 0;
  }, [selected, pending, slots, stemById, pushHistory, commitCuts, flashHint, setClipSelection]);

  const cutSelectedToClipboard = useCallback(() => {
    if (!selected || !copySelected()) return false;
    const cuts = (pending[selected.stemId] ?? []).map((c) => ({ ...c }));
    const idx = cuts.findIndex((c) => segIdOf(c) === selected.segId);
    if (idx < 0) return false;
    pushHistory();
    commitCuts(selected.stemId, [...cuts.slice(0, idx), ...cuts.slice(idx + 1)]);
    setSelected(null);
    return true;
  }, [selected, pending, copySelected, pushHistory, commitCuts]);

  /** Suno double-click: open the bottom-dock clip editor for the clip. */
  const openDock = (stemId: string, e: React.MouseEvent) => {
    const canvas = e.currentTarget as HTMLCanvasElement;
    const idx = findRegion(stemId, toGlobal(canvas, e.clientX));
    if (idx == null) return;
    const cut = (pending[stemId] ?? [])[idx];
    if (!cut) return;
    setSelectedCueId(null);
    setSelected({ stemId, segId: segIdOf(cut) });
    requestAnimationFrame(() => {
      document.getElementById("clip-dock")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
  };

  /** Suno Delete with no marked range: remove the selected clip. */
  /** Delete every selected clip (Suno multi-clip delete). */
  const deleteSelectedClip = useCallback(() => {
    if (clipSelection.length === 0) {
      flashHint("Delete: select a clip first (click it or use ↑/↓).");
      return false;
    }
    const byStem = new Map<string, Set<string>>();
    for (const ref of clipSelection) {
      const set = byStem.get(ref.stemId) ?? new Set<string>();
      set.add(ref.segId);
      byStem.set(ref.stemId, set);
    }
    pushHistory();
    for (const [stemId, segIds] of byStem) {
      const kept = (pending[stemId] ?? []).filter((c) => !segIds.has(segIdOf(c)));
      commitCuts(stemId, kept.map((c) => ({ ...c })));
    }
    setSelected(null);
    return true;
  }, [clipSelection, pending, pushHistory, commitCuts, flashHint]);

  /** Play from the marked section (Alt+Space, or P where the OS owns
   * Alt+Space). Falls back to the playhead when nothing is marked. */
  const playFromMark = useCallback(() => {
    if (engine.playing) {
      engine.pause();
      auditionRef.current = null;
      setPlaying(false);
      setPlayhead(engine.getTime());
      return;
    }
    const start = selection ? selection.start : playheadRef.current;
    if (!selection) {
      flashHint("No marked range — playing from the playhead. Drag on the timeline to mark one.");
    }
    void (async () => {
      await engine.resume();
      engine.play(start, allSchedule);
      setPlaying(engine.playing);
      setPlayhead(start);
    })();
  }, [engine, allSchedule, selection, flashHint]);

  const onCardKeyDown = (e: KeyboardEvent) => {
    const target = e.target as HTMLElement | null;
    // Ignore text-entry controls only: buttons/links stay shortcut-enabled so
    // Space works right after clicking a step or toolbar button, which is
    // where focus usually lands.
    if (target?.closest("input, select, textarea, [contenteditable='true']")) return;
    const mod = e.metaKey || e.ctrlKey;
    const k = e.key.toLowerCase();
    // Undo / redo.
    if (mod && k === "z" && !e.shiftKey) {
      e.preventDefault();
      undoEdit();
      return;
    }
    if ((mod && k === "z" && e.shiftKey) || (mod && k === "y")) {
      e.preventDefault();
      redoEdit();
      return;
    }
    if (mod && k === "s") {
      e.preventDefault();
      void saveAll();
      return;
    }
    if (mod && k === "e") {
      e.preventDefault();
      cutOutRange();
      return;
    }
    if (mod && k === "d") {
      e.preventDefault();
      duplicateSelected();
      return;
    }
    if (mod && k === "c") {
      e.preventDefault();
      copySelected();
      return;
    }
    if (mod && k === "x") {
      e.preventDefault();
      cutSelectedToClipboard();
      return;
    }
    if (mod && k === "v") {
      e.preventDefault();
      pasteClipboard();
      return;
    }
    if (mod && (e.key === "=" || e.key === "+" || e.key === "-")) {
      e.preventDefault();
      if (e.key === "-") zoomOut();
      else zoomIn();
      return;
    }
    // Plain + / − zoom (25% steps), Suno-style without a modifier.
    if (
      !mod &&
      !e.altKey &&
      (e.key === "+" || e.key === "=" || e.key === "-" || e.key === "_" ||
        e.key === "Add" || e.key === "Subtract")
    ) {
      e.preventDefault();
      if (e.key === "-" || e.key === "_" || e.key === "Subtract") zoomOut();
      else zoomIn();
      return;
    }
    if (mod && (e.key === "ArrowLeft" || e.key === "ArrowRight")) return;
    if (k === "0" && !mod && !e.altKey) {
      e.preventDefault();
      toggleSelectedEnabled();
      return;
    }
    if (k === "z" && !mod && !e.altKey) {
      e.preventDefault();
      zoomToSelection();
      return;
    }
    if (k === "f" && e.shiftKey && !mod) {
      e.preventDefault();
      toggleFadeSelected();
      return;
    }
    if (k === "f" && !e.shiftKey && !mod && !e.altKey) {
      e.preventDefault();
      toggleFullscreen();
      return;
    }
    if ((k === "m" || k === "s") && e.shiftKey && !mod && !e.altKey) {
      e.preventDefault();
      toggleMuteSolo(k === "m" ? "mute" : "solo");
      return;
    }
    if (e.key === "?" || (e.key === "/" && e.shiftKey)) {
      e.preventDefault();
      setShowShortcuts((v) => !v);
      return;
    }
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      const step = (e.key === "ArrowRight" ? 1 : -1) * (e.shiftKey && !selection ? 1 : 0.1);
      if (selection) shiftMark(step, e.shiftKey);
      else nudgeSelected(step);
      return;
    }
    if (e.key === "ArrowUp" || e.key === "ArrowDown") {
      e.preventDefault();
      cycleSelected(e.key === "ArrowDown" ? 1 : -1);
      return;
    }
    if (e.key === "Backspace" || e.key === "Delete") {
      e.preventDefault();
      if (selection) cutOutRange();
      else deleteSelectedClip();
      return;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      if (showShortcuts) setShowShortcuts(false);
      else if (selection) {
        setSelection(null);
        selectionRef.current = null;
      } else if (isFullscreen) {
        toggleFullscreen();
      }
      return;
    }
    if (k === "p" && !mod && !e.altKey) {
      e.preventDefault();
      playFromMark();
      return;
    }
    if ((e.code === "Space" || e.key === " ") && e.altKey) {
      // Suno parity; on Linux/Windows the OS may own Alt+Space, so P is the
      // reliable alias.
      e.preventDefault();
      playFromMark();
      return;
    }
    if (e.code !== "Space" && e.key !== " ") return;
    e.preventDefault();
    void togglePlay();
  };

  // Shortcuts are global to the Cut page (DAW behaviour): the timeline div is
  // not guaranteed to own focus after clicking a step, a toolbar button or a
  // clip, so a document-level listener is required. The ref avoids
  // re-registering on every render.
  const keyHandlerRef = useRef(onCardKeyDown);
  keyHandlerRef.current = onCardKeyDown;
  useEffect(() => {
    const handler = (event: KeyboardEvent) => keyHandlerRef.current(event);
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, []);

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
    pushHistory();
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
    <div ref={pageRef} className={`mt-page${isFullscreen ? " mt-page-fs" : ""}`}>
      <section className="panel stems-toolbar">
        <div>
          <h2 className="panel-title">
            <Scissors size={15} weight="fill" />
            Cut board
          </h2>
          <p className="panel-note">
            Cuts follow the script order — drag a clip to move it, edges to trim,
            Alt/Ctrl-drag to slip, double-click to edit. Shift+? lists all keys.
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
          <button
            type="button"
            className="button button-ghost button-sm"
            onClick={() => setShowShortcuts((v) => !v)}
            title="Keyboard shortcuts (Shift+?)"
            aria-label="Show keyboard shortcuts"
          >
            Keys
          </button>
          <button
            type="button"
            className="button button-ghost button-sm"
            onClick={toggleFullscreen}
            title="Fullscreen the Cut page (F) — fits the whole editor on screen"
            aria-label={isFullscreen ? "Exit fullscreen" : "Fullscreen the Cut page"}
            aria-pressed={isFullscreen}
          >
            <CornersOut size={14} />
            {isFullscreen ? "Exit" : "Fullscreen"}
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
          <span>
            {missingTakes.length} clip{missingTakes.length === 1 ? "" : "s"} point at a
            deleted take ({missingTakes.slice(0, 3).map((m) => m.takeId.slice(0, 8)).join(", ")}
            {missingTakes.length > 3 ? "…" : ""}). Reassign the track or re-render.
          </span>{" "}
          <button
            type="button"
            className="button button-ghost button-sm"
            onClick={reassignMissingCuts}
            title="Rebind clips with missing takes to each stem's selected take"
          >
            Use selected take
          </button>
        </div>
      ) : null}

      {showShortcuts ? (
        <div
          className="panel mt-shortcuts"
          role="dialog"
          aria-label="Keyboard shortcuts"
          style={{ marginBottom: 12, padding: "14px 18px" }}
        >
          <div className="mt-selection-head">
            <strong>Keyboard shortcuts</strong>
            <span className="panel-sub">global on the Cut page · Suno Studio parity</span>
            <button
              type="button"
              className="button button-ghost button-sm"
              onClick={() => setShowShortcuts(false)}
              title="Close (Esc)"
              aria-label="Close keyboard shortcuts"
            >
              ×
            </button>
          </div>
          <div className="mt-selection-controls" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))", gap: "4px 18px" }}>
            <span><b>Space</b> · play / pause</span>
            <span><b>P / Alt+Space</b> · play from mark</span>
            <span><b>Ctrl/Cmd+E</b> · cut marked range</span>
            <span><b>Backspace/Del</b> · cut range / delete clip</span>
            <span><b>0</b> · disable / enable clip</span>
            <span><b>Ctrl/Cmd+D</b> · duplicate clip</span>
            <span><b>Ctrl/Cmd+C/X/V</b> · copy / cut / paste clip</span>
            <span><b>Ctrl/Cmd+Z</b> · undo</span>
            <span><b>Ctrl+Shift+Z / Ctrl+Y</b> · redo</span>
            <span><b>Ctrl/Cmd+S</b> · save all</span>
            <span><b>←/→</b> · nudge clip (Shift: coarse)</span>
            <span><b>Shift+←/→</b> · extend marked section</span>
            <span><b>↑/↓</b> · select prev / next clip</span>
            <span><b>Shift+M / Shift+S</b> · mute / solo track</span>
            <span><b>+ / −</b> · zoom 25% steps (Ctrl/Cmd too)</span>
            <span><b>Z</b> · zoom to selection</span>
            <span><b>F</b> · fullscreen Cut page (Shift+F: fade)</span>
            <span><b>Shift+?</b> · this dialog</span>
            <span><b>Esc</b> · clear mark / close</span>
            <span><b>LMB-drag</b> · free range (no clip marked)</span>
            <span><b>Click clip</b> · select · drag again to move</span>
            <span><b>Shift-click clip</b> · add / remove from selection</span>
            <span><b>Drag top corners</b> · fade in / out</span>
            <span><b>Right-click clip</b> · actions menu</span>
            <span><b>Double-click ruler</b> · open clip editor</span>
            <span><b>Double-click</b> · open clip editor</span>
          </div>
        </div>
      ) : null}

      <div
        className="panel mt-timeline"
        tabIndex={0}
        aria-label="Multitrack timeline. Left click sets the playhead, Space plays or pauses, double-click opens the clip editor, Shift+question-mark lists all shortcuts."
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
          <div className="mt-zoom" role="group" aria-label="Timeline zoom">
            <button
              type="button"
              className="button button-ghost button-sm mt-zoom-btn"
              onClick={zoomOut}
              disabled={total <= 0 || !zoomed}
              title="Zoom out 25% (−)"
              aria-label="Zoom out"
            >
              −
            </button>
            <span className="mt-zoom-pct" aria-live="polite" title="Visible portion of the timeline">
              {Math.round((total / Math.max(0.001, viewEnd - viewStart)) * 100)}%
            </span>
            <button
              type="button"
              className="button button-ghost button-sm mt-zoom-btn"
              onClick={zoomIn}
              disabled={total <= 0}
              title="Zoom in 25% (+), centered on the selection"
              aria-label="Zoom in"
            >
              +
            </button>
          </div>
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
        {actionHint ? (
          <div className="mt-hint-flash" role="status" aria-live="polite">
            {actionHint}
          </div>
        ) : null}
        {loadError ? <div className="render-error">waveform: {loadError}</div> : null}

        {/* insert sounds / music at the playhead (no page switch needed) */}
        <div className="mt-insert">
          <span className="mt-zoom-label">Insert:</span>
          <input
            type="search"
            value={insertQuery}
            onChange={(e) => setInsertQuery(e.target.value)}
            placeholder="Search sounds…"
            aria-label="Search sounds to insert"
          />
          <select
            value={insertId}
            onChange={(e) => setInsertId(e.target.value)}
            aria-label="Sound to insert"
            disabled={insertItems.length === 0}
          >
            {insertItems.map((item) => (
              <option key={item.id} value={item.id} title={item.name}>
                {sfxLabel(item.name)}
              </option>
            ))}
          </select>
          <select
            value={insertKind}
            onChange={(e) => setInsertKind(e.target.value as "one_shot" | "bed" | "music")}
            aria-label="Cue kind"
            title="one-shot plays once, beds and music run to the scene end"
          >
            <option value="one_shot">one-shot</option>
            <option value="bed">bed</option>
            <option value="music">music</option>
          </select>
          <button
            type="button"
            className="button button-ghost button-sm"
            onClick={() => void insertCueAtPlayhead()}
            disabled={!insertId || inserting}
            title="Insert the selected sound at the playhead"
          >
            {inserting ? "…" : "At playhead"}
          </button>
        </div>

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
              onPointerDown={onRulerPointerDown}
              onDoubleClick={onRulerDoubleClick}
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
                      onPointerDown={(e) => onTrackPointerDown(e, stem.id)}
                      onPointerMove={(e) => onTrackPointerMove(e, stem.id)}
                      onPointerUp={onTrackPointerUp}
                      onPointerCancel={onTrackPointerUp}
                      onDoubleClick={(e) => openDock(stem.id, e)}
                      onContextMenu={(e) => onTrackContextMenu(e, stem.id)}
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
          <div className="mt-selection" id="clip-dock">
            <div className="mt-selection-head">
              <span className="panel-sub">clip editor · double-click a clip to open</span>
              <button
                type="button"
                className="button button-ghost button-sm"
                onClick={() => {
                  setSelected(null);
                  setSelection(null);
                }}
                title="Close clip editor"
                aria-label="Close clip editor"
              >
                ×
              </button>
            </div>
            <div className="mt-selection-head">
              <span className="speaker-badge" style={speakerStyle(selStem.speaker)}>
                {selStem.speaker}
              </span>
              <strong>
                {selCut.line_id}
                {clipSelection.length > 1 ? ` +${clipSelection.length - 1}` : ""}
              </strong>
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
              <label title="Clip enabled — 0 toggles (disabled clips are dim and silent)">
                <span>enabled</span>
                <input
                  type="checkbox"
                  checked={selCut.enabled !== false}
                  onChange={(e) => updateCutEffect(selStem.id, segIdOf(selCut), { enabled: e.target.checked })}
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
                  value={selCue.anchor_line_id ?? ""}
                  onChange={(e) => {
                    const value = e.target.value;
                    // Picking the timeline detaches the cue; picking a line puts
                    // it back under script order.
                    updateCueLocal(
                      selCue.id,
                      value === ""
                        ? { anchor_line_id: null, position_s: round2(selCueSpan?.anchor ?? 0) }
                        : { anchor_line_id: value, position_s: null, end_s: null },
                    );
                  }}
                  title="A line anchor ties the cue to that line's slot; 'on the timeline' lets it sit anywhere"
                >
                  <option value="">on the timeline</option>
                  {allLines.map((line) => (
                    <option key={line.id} value={line.id}>
                      {line.id} · {line.speaker}
                    </option>
                  ))}
                </select>
              </label>
              {selCue.position_s != null ? (
                <>
                  <label>
                    <span>start</span>
                    <input
                      type="number"
                      step="0.01"
                      min="0"
                      value={Number((selCue.position_s ?? 0).toFixed(2))}
                      onChange={(e) =>
                        updateCueLocal(selCue.id, {
                          position_s: Math.max(0, Number(e.target.value) || 0),
                        })
                      }
                      title="Global timeline position in seconds (drag the clip or edit here)"
                    />
                  </label>
                  {selCue.kind !== "one_shot" ? (
                    <label>
                      <span>end</span>
                      <input
                        type="number"
                        step="0.01"
                        min="0"
                        placeholder="auto"
                        value={selCue.end_s == null ? "" : Number(selCue.end_s.toFixed(2))}
                        onChange={(e) =>
                          updateCueLocal(selCue.id, {
                            end_s: e.target.value === "" ? null : Math.max(0.05, Number(e.target.value) || 0),
                          })
                        }
                        title="Leave empty to run to the end of the play"
                      />
                    </label>
                  ) : null}
                </>
              ) : (
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
              )}
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
                  title="beds and music run until their end, one-shots play once"
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
              <strong>Marked range</strong>
              <span className="mt-selection-text">
                {stemById.get(selection.stemId)?.speaker ?? selection.stemId} ·{" "}
                {selection.start.toFixed(2)}–{selection.end.toFixed(2)}s
                ({Math.max(0, selection.end - selection.start).toFixed(2)}s on the timeline)
              </span>
            </div>
            <div className="mt-selection-controls">
              <button
                type="button"
                className="button button-primary button-sm"
                onClick={() => void cutOutRange()}
                title="Cut this range out and ripple the rest left (Ctrl+E or Delete)"
              >
                <Scissors size={14} />
                Cut range
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
              <span className="panel-sub">Save all writes the cut segments.</span>
            </div>
          </div>
        ) : (
          <p className="panel-note mt-hint">
            Click a clip to select it (↑/↓ cycles clips), drag its body to move
            it, edges to trim, Alt/Ctrl-drag to slip, double-click to edit. With
            no clip selected, drag on the timeline for a free range — Ctrl+E
            deletes it and ripples the rest left. Drag a music or bed clip to
            place it anywhere on the timeline; drag a one-shot to nudge it around
            its line. Click empty space to seek.
          </p>
        )}
      </div>

      {contextMenu ? (
        <div
          className="mt-ctx-overlay"
          onClick={() => setContextMenu(null)}
          onContextMenu={(e) => {
            e.preventDefault();
            setContextMenu(null);
          }}
        >
          <div
            className="mt-ctx-menu"
            style={{ left: contextMenu.x, top: contextMenu.y }}
            role="menu"
            aria-label="Clip actions"
          >
            <button type="button" onClick={() => { duplicateSelected(); setContextMenu(null); }}>Duplicate</button>
            <button type="button" onClick={() => { copySelected(); setContextMenu(null); }}>Copy</button>
            <button type="button" onClick={() => { pasteClipboard(); setContextMenu(null); }}>Paste here</button>
            <button type="button" onClick={() => { toggleSelectedEnabled(); setContextMenu(null); }}>Disable / enable</button>
            <button type="button" onClick={() => { toggleFadeSelected(); setContextMenu(null); }}>Toggle fade</button>
            {selection ? (
              <button type="button" onClick={() => { cutOutRange(); setContextMenu(null); }}>Cut marked range</button>
            ) : null}
            <button type="button" onClick={() => { deleteSelectedClip(); setContextMenu(null); }}>Delete</button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
