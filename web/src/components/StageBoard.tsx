import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowCounterClockwise,
  HardDrives,
  Pause,
  Play,
  Sparkle,
  SpeakerHigh,
  WarningCircle,
} from "@phosphor-icons/react";
import { api, ApiError } from "../api";
import type {
  Job,
  Project,
  SceneStage,
  StageActor,
  StageCapabilities,
  StagePath,
  StageProject,
} from "../types";
import { speakerColor } from "../utils";

interface Props {
  project: Project;
  onRefresh: () => void;
}

/** SVG canvas size in units; the room is scaled to fit inside it. */
const STAGE_W = 640;
const STAGE_H = 480;

type Vec3 = [number, number, number];
type Absorption = [number, number, number, number, number, number];
/** One motion keyframe: [seconds, x, y, z], scene-relative time. */
type PathKey = [number, number, number, number];

function emptyStage(): StageProject {
  return {
    version: 1,
    enabled: false,
    order: 3,
    export_ambisonic: false,
    tail_s: 0.6,
    default_room: {
      dims_m: [6, 5, 3],
      absorption: [0.25, 0.25, 0.25, 0.25, 0.25, 0.25],
      reflections: 2,
    },
    scenes: {},
  };
}

/** Distance in the x/y plane — what a top-down layout can actually show. */
function planDistance(a: Vec3, b: Vec3): number {
  return Math.hypot(a[0] - b[0], a[1] - b[1]);
}

/** Keep a scene internally valid: everything must sit inside its own room.
 *  Applying a smaller room preset, or editing a dimension, would otherwise
 *  leave the listener or an actor outside the new walls and the save would be
 *  rejected by the server with no way for the user to see why. */
function fitToRoom(scene: SceneStage): SceneStage {
  const [width, depth, height] = scene.room.dims_m;
  const clamp = (point: Vec3): Vec3 => [
    Math.min(width, Math.max(0, point[0])),
    Math.min(depth, Math.max(0, point[1])),
    Math.min(height, Math.max(0, point[2])),
  ];
  return {
    ...scene,
    listener: { ...scene.listener, position_m: clamp(scene.listener.position_m) },
    actors: scene.actors.map((actor) => ({
      ...actor,
      position_m: clamp(actor.position_m),
    })),
    // Paths are the same promise: a walk that was inside a 6 m room is not
    // inside a 2 m corridor. The renderer hides the fault with clamp_to_room,
    // so an unclamped keyframe is invisible in the mix and obvious on the
    // board — clamp here and the two agree.
    paths: scene.paths.map((path) => ({
      ...path,
      keys: path.keys.map((key): PathKey => [key[0], ...clamp([key[1], key[2], key[3]])]),
    })),
  };
}

export function StageBoard({ project, onRefresh }: Props) {
  const [capabilities, setCapabilities] = useState<StageCapabilities | null>(null);
  const [stage, setStage] = useState<StageProject>(project.stage ?? emptyStage());
  const [sceneSpeakers, setSceneSpeakers] = useState<Record<string, string[]>>({});
  const [activeScene, setActiveScene] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [previewJob, setPreviewJob] = useState<Job | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  // null means "show resting positions"; a number is the scene clock the board
  // is auditioning, so actor dots follow their paths instead of the end pose.
  const [playhead, setPlayhead] = useState<number | null>(null);
  const [duration, setDuration] = useState(0);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const dragRef = useRef<{ speaker: string; kind: "actor" | "key"; index: number } | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const pollRef = useRef<number | null>(null);

  const scenes = project.script.scenes;

  // Capabilities gate the whole step: no library means no staging.
  useEffect(() => {
    let cancelled = false;
    api
      .stageCapabilities()
      .then((result) => !cancelled && setCapabilities(result))
      .catch(() => !cancelled && setCapabilities(null));
    return () => {
      cancelled = true;
    };
  }, []);

  // Speakers come from the backend so the cast matches the parsed script.
  useEffect(() => {
    let cancelled = false;
    api
      .getStage(project.id)
      .then((result) => {
        if (cancelled) return;
        setStage(result.stage);
        setSceneSpeakers(result.scene_speakers);
        const first = scenes.map((scene) => String(scene.id)).find((key) => result.stage.scenes[key]);
        setActiveScene(first ?? String(scenes[0]?.id ?? "") ?? null);
        setDirty(false);
      })
      .catch((exc: unknown) => setError(exc instanceof ApiError ? exc.message : "could not load the stage"));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project.id]);

  useEffect(() => {
    if (!previewJob || previewJob.status === "succeeded" || previewJob.status === "failed" || previewJob.status === "cancelled") return;
    pollRef.current = window.setTimeout(() => {
      api
        .getJob(previewJob.id)
        .then((job) => {
          setPreviewJob(job);
          if (job.status === "succeeded" && job.result) {
            const file = String((job.result as Record<string, unknown>).file ?? "");
            if (file) setPreviewUrl(api.fileUrl(project.id, file));
          } else if (job.status === "failed") {
            setError(job.error ?? "preview failed");
          }
        })
        .catch(() => setError("preview status unavailable"));
    }, 700);
    return () => {
      if (pollRef.current !== null) window.clearTimeout(pollRef.current);
    };
  }, [previewJob, project.id]);

  // A new audition resets the scene clock. When the file goes away (scene
  // switch, or the job starting over) the board returns to resting positions.
  useEffect(() => {
    setPlaying(false);
    setDuration(0);
    setPlayhead(previewUrl ? 0 : null);
    const audio = audioRef.current;
    if (audio) {
      try {
        audio.currentTime = 0;
      } catch {
        /* metadata not loaded yet; the next play starts at zero anyway */
      }
    }
  }, [previewUrl]);

  const sceneKey = activeScene ?? String(scenes[0]?.id ?? "");
  const scene = stage.scenes[sceneKey];
  const roomDims = (scene?.room ?? stage.default_room).dims_m;
  const previewBusy = previewJob?.status === "queued" || previewJob?.status === "running";

  // Map stage metres to SVG pixels: x across, y down the screen (front wall at
  // the top, matching a listener who faces -y).
  const scale = useMemo(() => {
    const [width, depth] = roomDims;
    return Math.min((STAGE_W - 60) / Math.max(0.5, width), (STAGE_H - 60) / Math.max(0.5, depth));
  }, [roomDims]);
  const toPx = useCallback(
    (point: Vec3): [number, number] => [30 + point[0] * scale, 30 + point[1] * scale],
    [scale],
  );
  const toMetres = useCallback(
    (px: number, py: number): [number, number] => [
      Math.min(roomDims[0], Math.max(0, (px - 30) / scale)),
      Math.min(roomDims[1], Math.max(0, (py - 30) / scale)),
    ],
    [scale, roomDims],
  );

  const patch = useCallback((next: StageProject) => {
    setStage(next);
    setDirty(true);
  }, []);

  const patchScene = useCallback(
    (key: string, updater: (current: SceneStage) => SceneStage) => {
      setStage((current) => {
        // An unstaged scene starts from a blank one rather than being skipped:
        // "Spread the cast" has to be able to *stage* a scene, not only edit
        // one that is already staged. Reading `current` here (not the closure's
        // `stage`) keeps concurrent patches from clobbering each other.
        const existing = current.scenes[key] ?? emptyScene(current);
        return {
          ...current,
          scenes: { ...current.scenes, [key]: fitToRoom(updater(existing)) },
        };
      });
      setDirty(true);
    },
    [],
  );

  const patchActor = useCallback(
    (speaker: string, changes: Partial<StageActor>) => {
      patchScene(sceneKey, (current) => ({
        ...current,
        actors: current.actors.map((actor) =>
          actor.speaker === speaker ? { ...actor, ...changes } : actor,
        ),
      }));
    },
    [patchScene, sceneKey],
  );

  // Motion lives beside the actor, not on it: `position_m` is the resting spot
  // and a path is an overlay that only exists while it has at least two keys.
  // Sorting here (rather than at save) keeps the server's `keys_sorted`
  // validator from rejecting an edit the user made in the panel, which would
  // surface as a 422 with no visible cause.
  const patchPath = useCallback(
    (speaker: string, updater: (keys: PathKey[]) => PathKey[]) => {
      patchScene(sceneKey, (current) => {
        const existing = current.paths.find((path) => path.speaker === speaker);
        const keys = updater(existing?.keys ?? []).sort((a, b) => a[0] - b[0]);
        const others = current.paths.filter((path) => path.speaker !== speaker);
        return {
          ...current,
          paths: keys.length > 1 ? [...others, { speaker, keys }] : others,
        };
      });
    },
    [patchScene, sceneKey],
  );

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const saved = await api.saveStage(project.id, stage);
      setStage(saved);
      setDirty(false);
      setNotice("stage saved — the next mix will be spatial");
      onRefresh();
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : "could not save the stage");
    } finally {
      setSaving(false);
    }
  }

  async function stageSceneFromCast() {
    const speakers = sceneSpeakers[sceneKey] ?? [];
    if (!speakers.length) {
      setError("this scene has no dialogue to stage");
      return;
    }
    try {
      // Send the room on screen: it may be a preset the user picked seconds ago
      // and never saved, and the server cannot know about it. Keeping the room
      // on the way back in is belt and braces for the same promise — spreading
      // is a decision about people, not about architecture.
      const room = scene?.room ?? stage.scenes[sceneKey]?.room;
      const layout = await api.layoutScene(project.id, Number(sceneKey), room);
      patchScene(sceneKey, () => ({ ...layout, room: room ?? layout.room }));
      setSelected(speakers[0]);
    } catch {
      setError("could not lay out this scene");
    }
  }

  async function addSpeaker(speaker: string) {
    patchScene(sceneKey, (current) => ({
      ...current,
      actors: [
        ...current.actors,
        {
          speaker,
          position_m: [current.room.dims_m[0] / 2, current.room.dims_m[1] * 0.4, 1.5],
          facing_deg: 0,
          gain_db: 0,
          direct: 1,
        } satisfies StageActor,
      ],
    }));
    setSelected(speaker);
  }

  function removeSpeaker(speaker: string) {
    patchScene(sceneKey, (current) => ({
      ...current,
      actors: current.actors.filter((actor) => actor.speaker !== speaker),
      paths: current.paths.filter((path) => path.speaker !== speaker),
    }));
    if (selected === speaker) setSelected(null);
  }

  async function togglePlay() {
    const audio = audioRef.current;
    if (!audio) return;
    if (!audio.paused) {
      audio.pause();
      return;
    }
    // Replay from the top once the take has run out, or the button looks dead.
    if (audio.ended || (Number.isFinite(audio.duration) && audio.currentTime >= audio.duration - 0.01)) {
      audio.currentTime = 0;
      setPlayhead(0);
    }
    try {
      await audio.play();
    } catch {
      setError("the browser blocked playback — press play again");
    }
  }

  function seekPreview(value: number) {
    const audio = audioRef.current;
    setPlayhead(value);
    if (audio && Number.isFinite(audio.duration)) {
      audio.currentTime = Math.min(value, audio.duration);
    }
  }

  /** Editing and playback are different modes: a drag moves the resting pose,
   *  so leave the audition clock (and pause) rather than editing a position the
   *  board is not showing. */
  function leavePlayback() {
    const audio = audioRef.current;
    if (audio && !audio.paused) audio.pause();
    setPlayhead(null);
  }

  function onPointerDown(event: React.PointerEvent, speaker: string) {
    event.preventDefault();
    leavePlayback();
    // Capture is best-effort: a pointer that ended between render and event
    // (or a synthetic one) makes setPointerCapture throw, and that must not
    // stop the drag from being tracked.
    try {
      (event.target as Element).setPointerCapture?.(event.pointerId);
    } catch {
      /* pointer is not active; the move handler still works */
    }
    dragRef.current = { speaker, kind: "actor", index: -1 };
    setSelected(speaker);
  }

  function onWaypointDown(event: React.PointerEvent, speaker: string, index: number) {
    // The handle sits on top of the actor group, so stop the drag from being
    // read as a request to move the actor's resting position instead.
    event.preventDefault();
    event.stopPropagation();
    leavePlayback();
    try {
      (event.target as Element).setPointerCapture?.(event.pointerId);
    } catch {
      /* pointer is not active; the move handler still works */
    }
    dragRef.current = { speaker, kind: "key", index };
    setSelected(speaker);
  }

  function onPointerMove(event: React.PointerEvent) {
    const drag = dragRef.current;
    const svg = svgRef.current;
    if (!drag || !svg) return;
    const rect = svg.getBoundingClientRect();
    const x = ((event.clientX - rect.left) / rect.width) * STAGE_W;
    const y = ((event.clientY - rect.top) / rect.height) * STAGE_H;
    const [metresX, metresY] = toMetres(x, y);
    const round = (value: number) => Math.round(value * 100) / 100;

    if (drag.kind === "key") {
      // A waypoint keeps its own time and height; only its plan position moves.
      const index = drag.index;
      patchPath(drag.speaker, (keys) =>
        keys.map((key, position): PathKey =>
          position === index
            ? [key[0], round(metresX), round(metresY), key[3]]
            : key,
        ),
      );
      return;
    }

    patchActor(drag.speaker, {
      position_m: [
        round(metresX),
        round(metresY),
        (stage.scenes[sceneKey]?.actors.find((actor) => actor.speaker === drag.speaker)?.position_m ?? [0, 0, 1.5])[2],
      ],
    });
  }

  function onPointerUp() {
    dragRef.current = null;
  }

  const unstaged = (sceneSpeakers[sceneKey] ?? []).filter(
    (speaker) => !scene?.actors.some((actor) => actor.speaker === speaker),
  );
  const selectedActor = scene?.actors.find((actor) => actor.speaker === selected) ?? null;
  const available = capabilities?.available ?? false;

  return (
    <div className="stage-board">
      {!available ? (
        <div className="stage-warn" role="status">
          <WarningCircle size={16} weight="fill" />
          <div>
            <strong>Spatial renderer not installed.</strong> Build it with{" "}
            <code>python scripts/build_spatial.py</code>; until then the mix stays dry.
          </div>
        </div>
      ) : null}
      {error ? (
        <div className="error-banner" role="alert">
          <WarningCircle size={16} weight="fill" />
          {error}
        </div>
      ) : null}
      {notice ? (
        <div className="notice-banner" role="status">
          <Sparkle size={16} weight="fill" />
          {notice}
        </div>
      ) : null}

      <div className="stage-head">
        <label className="stage-enable">
          <input
            type="checkbox"
            checked={stage.enabled}
            onChange={(event) => patch({ ...stage, enabled: event.target.checked })}
          />
          <span>Stage this project</span>
        </label>
        <div className="stage-order" role="group" aria-label="Ambisonic order">
          {(capabilities?.orders ?? [1, 3]).map((order) => (
            <button
              key={order}
              type="button"
              className={stage.order === order ? "chip chip--on" : "chip"}
              onClick={() => patch({ ...stage, order: order as 1 | 3 })}
              title={`${(order + 1) * (order + 1)} Ambisonic channels`}
            >
              order {order}
            </button>
          ))}
        </div>
        <label className="stage-field">
          <span>Ambisonic master</span>
          <input
            type="checkbox"
            checked={stage.export_ambisonic}
            onChange={(event) => patch({ ...stage, export_ambisonic: event.target.checked })}
          />
        </label>
        <label className="stage-field">
          <span>Room tail (s)</span>
          <input
            type="number"
            min={0}
            step={0.05}
            max={capabilities?.max_tail_s?.["48000"] ?? 0.68}
            value={stage.tail_s}
            onChange={(event) => patch({ ...stage, tail_s: Number(event.target.value) })}
          />
        </label>
        <button type="button" className="button button--primary" onClick={save} disabled={saving || !dirty}>
          {saving ? "Saving…" : dirty ? "Save stage" : "Saved"}
        </button>
      </div>

      <div className="stage-tabs" role="tablist" aria-label="Scenes">
        {scenes.map((item) => {
          const key = String(item.id);
          const staged = Boolean(stage.scenes[key]?.actors.length);
          return (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={key === sceneKey}
              className={key === sceneKey ? "stage-tab stage-tab--on" : "stage-tab"}
              onClick={() => {
                setActiveScene(key);
                setSelected(null);
                setPreviewJob(null);
                setPreviewUrl(null);
              }}
            >
              <span className="stage-tab__n">{item.id}</span>
              <span className="stage-tab__label">{item.heading || `Scene ${item.id}`}</span>
              {staged ? <span className="stage-tab__dot" aria-label="staged" /> : null}
            </button>
          );
        })}
      </div>

      {!scene ? (
        <div className="stage-empty">
          <p>This scene is not staged yet.</p>
          <button type="button" className="button" onClick={stageSceneFromCast}>
            <Sparkle size={14} /> Spread the cast
          </button>
        </div>
      ) : (
        <div className="stage-grid">
          <div className="stage-canvas">
            <div className="stage-transport">
              <button
                type="button"
                className="button stage-transport__play"
                onClick={() => void togglePlay()}
                disabled={!previewUrl || previewBusy}
                aria-label={playing ? "Pause scene preview" : "Play scene preview"}
              >
                {playing ? <Pause size={14} weight="fill" /> : <Play size={14} weight="fill" />}
              </button>
              <input
                type="range"
                className="stage-transport__scrub"
                min={0}
                max={duration || 0}
                step={0.01}
                value={playhead ?? 0}
                disabled={!previewUrl || !duration || previewBusy}
                onChange={(event) => seekPreview(Number(event.target.value))}
                aria-label="Scene preview position"
              />
              <span className="stage-transport__time">
                {previewBusy
                  ? `rendering preview… ${Math.round((previewJob?.progress ?? 0) * 100)}%`
                  : previewUrl
                    ? `${playhead === null ? "—" : playhead.toFixed(1)} / ${duration ? duration.toFixed(1) : "0.0"} s`
                    : "no preview"}
              </span>
            </div>
            <svg
              ref={svgRef}
              viewBox={`0 0 ${STAGE_W} ${STAGE_H}`}
              className="stage-svg"
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
              onPointerLeave={onPointerUp}
            >
              <text x={STAGE_W / 2} y={18} textAnchor="middle" className="stage-wall-label">
                front wall
              </text>
              <rect
                x={30}
                y={30}
                width={roomDims[0] * scale}
                height={roomDims[1] * scale}
                className="stage-room"
              />
              {/* Where a speaker walks. Auto-staging derives a move from the
                  stage directions and the panel can author one by hand, so the
                  board always shows the motion the mix will render. Only the
                  selected actor's keyframes are draggable, to keep the canvas
                  calm when nobody is being edited. */}
              {scene.paths
                .filter((path) => path.keys.length > 1)
                .map((path) => {
                  const points = pathPoints(path.keys, roomDims)
                    .map((point) => toPx(point).join(","))
                    .join(" ");
                  const first = toPx([path.keys[0][1], path.keys[0][2], path.keys[0][3]]);
                  return (
                    <g
                      key={`path-${path.speaker}`}
                      className="stage-path"
                      style={{ color: speakerColor(path.speaker) }}
                    >
                      <polyline points={points} />
                      <circle cx={first[0]} cy={first[1]} r={4} className="stage-path__start" />
                    </g>
                  );
                })}
              {[1, 2, 3, 4, 5, 6].map((metre) => {
                const radius = metre * scale;
                const [cx, cy] = toPx(scene.listener.position_m);
                if (radius > Math.max(STAGE_W, STAGE_H)) return null;
                return (
                  <circle
                    key={metre}
                    cx={cx}
                    cy={cy}
                    r={radius}
                    className="stage-ring"
                  />
                );
              })}
              <ListenerGlyph scene={scene} toPx={toPx} scale={scale} />
              {scene.actors.map((actor) => {
                const motion = scene.paths.find(
                  (path) => path.speaker === actor.speaker && path.keys.length > 1,
                );
                // During an audition the dot follows the same spline the mix
                // renders; otherwise it rests where the actor was placed.
                const shown =
                  motion && playhead !== null ? samplePath(motion.keys, playhead) : actor.position_m;
                const [cx, cy] = toPx(shown);
                const distance = planDistance(shown, scene.listener.position_m);
                return (
                  <g
                    key={actor.speaker}
                    className={
                      "stage-actor" +
                      (motion ? " stage-actor--moves" : "") +
                      (selected === actor.speaker ? " stage-actor--on" : "")
                    }
                    style={{ color: speakerColor(actor.speaker) }}
                    transform={`translate(${cx} ${cy})`}
                    onPointerDown={(event) => onPointerDown(event, actor.speaker)}
                    tabIndex={0}
                    role="button"
                    aria-label={
                      `${actor.speaker}, ${distance.toFixed(1)} metres` +
                      (motion ? `, walks ${pathLength(motion)} m` : "")
                    }
                    onKeyDown={(event) => {
                      // Keyboard nudging keeps the stage operable without a
                      // pointer; a focused chip is otherwise unreachable.
                      const step = event.shiftKey ? 1 : 0.25;
                      const [x, y] = actor.position_m;
                      if (event.key === "ArrowLeft") patchActor(actor.speaker, { position_m: [Math.max(0, x - step), y, actor.position_m[2]] });
                      else if (event.key === "ArrowRight") patchActor(actor.speaker, { position_m: [Math.min(roomDims[0], x + step), y, actor.position_m[2]] });
                      else if (event.key === "ArrowUp") patchActor(actor.speaker, { position_m: [x, Math.max(0, y - step), actor.position_m[2]] });
                      else if (event.key === "ArrowDown") patchActor(actor.speaker, { position_m: [x, Math.min(roomDims[1], y + step), actor.position_m[2]] });
                      else return;
                      event.preventDefault();
                    }}
                  >
                    <FacingArrow actor={actor} />
                    <circle r={13} className="stage-actor__dot" />
                    <text y={4} textAnchor="middle" className="stage-actor__initials">
                      {actor.speaker.slice(0, 2)}
                    </text>
                    <text y={30} textAnchor="middle" className="stage-actor__meta">
                      {distance.toFixed(1)} m
                    </text>
                  </g>
                );
              })}
              {/* Draggable keyframes paint last so a handle is never buried
                  under the actor dot it starts on: a freshly seeded walk puts
                  both keys exactly where the actor stands. */}
              {scene.paths
                .filter((path) => path.speaker === selected && path.keys.length > 1)
                .map((path) => (
                  <g
                    key={`keys-${path.speaker}`}
                    className="stage-path__keys"
                    style={{ color: speakerColor(path.speaker) }}
                  >
                    {path.keys.map((key, index) => {
                      const [cx, cy] = toPx([key[1], key[2], key[3]]);
                      return (
                        <circle
                          key={index}
                          cx={cx}
                          cy={cy}
                          r={5}
                          className="stage-path__key"
                          onPointerDown={(event) => onWaypointDown(event, path.speaker, index)}
                          onKeyDown={(event) => {
                            // Per-handle nudging keeps waypoints operable
                            // without a pointer; plain arrows on the actor move
                            // the resting position instead.
                            const step = event.shiftKey ? 1 : 0.25;
                            const [x, y, z] = [key[1], key[2], key[3]];
                            let next: PathKey | null = null;
                            if (event.key === "ArrowLeft") next = [key[0], Math.max(0, x - step), y, z];
                            else if (event.key === "ArrowRight") next = [key[0], Math.min(roomDims[0], x + step), y, z];
                            else if (event.key === "ArrowUp") next = [key[0], x, Math.max(0, y - step), z];
                            else if (event.key === "ArrowDown") next = [key[0], x, Math.min(roomDims[1], y + step), z];
                            if (!next) return;
                            event.preventDefault();
                            event.stopPropagation();
                            patchPath(path.speaker, (keys) =>
                              keys.map((item, position) => (position === index ? next! : item)),
                            );
                          }}
                          tabIndex={0}
                          role="button"
                          aria-label={`waypoint ${index + 1} at ${key[0].toFixed(1)} seconds`}
                        />
                      );
                    })}
                  </g>
                ))}
            </svg>
            {previewUrl ? (
              <audio
                ref={audioRef}
                className="stage-audio"
                src={previewUrl}
                data-testid="stage-preview"
                onLoadedMetadata={(event) => setDuration(event.currentTarget.duration)}
                onTimeUpdate={(event) => setPlayhead(event.currentTarget.currentTime)}
                onPlay={() => setPlaying(true)}
                onPause={() => setPlaying(false)}
                onEnded={() => setPlaying(false)}
              />
            ) : null}
            <div className="stage-canvas__foot">
              <span>drag to move · arrow keys nudge 25 cm (Shift: 1 m)</span>
              <span className="stage-canvas__dims">
                {roomDims[0]} × {roomDims[1]} × {roomDims[2]} m
              </span>
            </div>
          </div>

          <div className="stage-side">
            <div className="stage-side__actions">
              <button type="button" className="button" onClick={stageSceneFromCast}>
                <Sparkle size={14} /> Spread the cast
              </button>
              <button
                type="button"
                className="button"
                disabled={!scene.actors.length || !available || previewBusy}
                onClick={async () => {
                  setPreviewUrl(null);
                  setError(null);
                  try {
                    const job = await api.previewScene(project.id, Number(sceneKey));
                    const jobScene = Number((job.payload as Record<string, unknown>).scene ?? NaN);
                    if (Number.isFinite(jobScene) && jobScene !== Number(sceneKey)) {
                      setError(
                        `a preview for scene ${jobScene} is still rendering — try again when it finishes`,
                      );
                      return;
                    }
                    setPreviewJob(job);
                  } catch (exc) {
                    setError(exc instanceof ApiError ? exc.message : "preview failed");
                  }
                }}
              >
                {previewBusy ? <span className="spinner" /> : <Play size={14} />}
                {previewBusy ? "Rendering…" : "Audition scene"}
              </button>
              <button
                type="button"
                className="button"
                onClick={() => patchScene(sceneKey, () => emptyScene(stage))}
                title="Clear this scene"
              >
                <ArrowCounterClockwise size={14} /> Clear
              </button>
            </div>

            {previewBusy ? (
              <p className="stage-hint">
                Rendering the scene preview… {Math.round((previewJob?.progress ?? 0) * 100)}%
              </p>
            ) : previewUrl ? (
              <p className="stage-hint">Scene preview ready — use the transport above the board.</p>
            ) : null}

            {unstaged.length ? (
              <div className="stage-add">
                <span className="stage-side__title">Add to the room</span>
                {unstaged.map((speaker) => (
                  <button key={speaker} type="button" className="chip" onClick={() => addSpeaker(speaker)}>
                    {speaker}
                  </button>
                ))}
              </div>
            ) : null}

            <RoomPanel
              scene={scene}
              presets={capabilities?.presets ?? []}
              onChange={(next) => patchScene(sceneKey, () => next)}
            />

            {selectedActor ? (
              <ActorPanel
                actor={selectedActor}
                path={scene.paths.find(
                  (item) => item.speaker === selectedActor.speaker && item.keys.length > 1,
                )}
                listener={scene.listener.position_m}
                room={scene.room.dims_m}
                onChange={(changes) => patchActor(selectedActor.speaker, changes)}
                onPathChange={(updater) => patchPath(selectedActor.speaker, updater)}
                onRemove={() => removeSpeaker(selectedActor.speaker)}
              />
            ) : (
              <p className="stage-hint">Select a speaker on the stage to edit them.</p>
            )}

            <ListenerPanel
              scene={scene}
              onChange={(next) => patchScene(sceneKey, () => next)}
            />
          </div>
        </div>
      )}
    </div>
  );
}

function emptyScene(stage: StageProject): SceneStage {
  return {
    room: { ...stage.default_room },
    listener: { position_m: [stage.default_room.dims_m[0] / 2, stage.default_room.dims_m[1] * 0.8, 1.6], facing_deg: 0 },
    actors: [],
    paths: [],
  };
}

function ListenerGlyph({
  scene,
  toPx,
}: {
  scene: SceneStage;
  toPx: (point: Vec3) => [number, number];
  scale: number;
}) {
  const [cx, cy] = toPx(scene.listener.position_m);
  // Facing azimuth 0 points at the front wall, which is up the screen.
  const radians = (scene.listener.facing_deg * Math.PI) / 180;
  const length = 34;
  const tipX = cx + Math.sin(radians) * length;
  const tipY = cy - Math.cos(radians) * length;
  return (
    <g className="stage-listener">
      <circle cx={cx} cy={cy} r={9} className="stage-listener__head" />
      <line x1={cx} y1={cy} x2={tipX} y2={tipY} className="stage-listener__arrow" />
      <text x={cx} y={cy + 24} textAnchor="middle" className="stage-listener__label">
        listener
      </text>
    </g>
  );
}

function FacingArrow({ actor }: { actor: StageActor }) {
  const radians = (actor.facing_deg * Math.PI) / 180;
  const x = Math.sin(radians) * 22;
  const y = -Math.cos(radians) * 22;
  return <line x1={0} y1={0} x2={x} y2={y} className="stage-actor__facing" />;
}

function NumberField({
  label,
  value,
  min,
  max,
  step,
  onChange,
  suffix,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (value: number) => void;
  suffix?: string;
}) {
  return (
    <label className="stage-num">
      <span>{label}</span>
      <input
        type="number"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(event) => {
          const next = Number(event.target.value);
          if (Number.isFinite(next)) onChange(Math.min(max, Math.max(min, next)));
        }}
      />
      {suffix ? <em>{suffix}</em> : null}
    </label>
  );
}

function TimeField({
  value,
  max,
  onChange,
}: {
  value: number;
  max: number;
  onChange: (value: number) => void;
}) {
  // Keyframe times are authored on the scene clock, so "1:30" is the unit a
  // director thinks in. The model stores seconds; the two fields are whole
  // minutes and the 0-59 seconds inside one.
  const total = Math.max(0, Math.round(value));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  const commit = (nextMinutes: number, nextSeconds: number) => {
    const m = Math.max(0, Math.floor(nextMinutes));
    const s = Math.min(59, Math.max(0, Math.floor(nextSeconds)));
    onChange(Math.min(max, m * 60 + s));
  };
  return (
    <label className="stage-num">
      <span>time</span>
      <span className="stage-time">
        <input
          type="number"
          min={0}
          max={Math.floor(max / 60)}
          step={1}
          value={minutes}
          aria-label="keyframe minutes"
          onChange={(event) => {
            const next = Number(event.target.value);
            if (Number.isFinite(next)) commit(next, seconds);
          }}
        />
        <em>m</em>
        <input
          type="number"
          min={0}
          max={59}
          step={1}
          value={seconds}
          aria-label="keyframe seconds"
          onChange={(event) => {
            const next = Number(event.target.value);
            if (Number.isFinite(next)) commit(minutes, next);
          }}
        />
        <em>s</em>
      </span>
    </label>
  );
}

function RoomPanel({
  scene,
  presets,
  onChange,
}: {
  scene: SceneStage;
  presets: { key: string; label: string; dims_m: Vec3; absorption: number[]; reflections: number }[];
  onChange: (next: SceneStage) => void;
}) {
  const room = scene.room;
  const applyAbsorption = (value: number): Absorption => [
    value,
    value,
    value,
    value,
    value,
    Math.max(0.02, value * 0.6),
  ];
  return (
    <div className="stage-panel">
      <span className="stage-side__title">Room</span>
      <div className="stage-presets">
        {presets.map((preset) => (
          <button
            key={preset.key}
            type="button"
            className="chip"
            title={`${preset.dims_m.join(" × ")} m`}
            onClick={() =>
              onChange({
                ...scene,
                room: {
                  dims_m: preset.dims_m,
                  absorption: preset.absorption as Absorption,
                  reflections: preset.reflections,
                },
              })
            }
          >
            {preset.label}
          </button>
        ))}
      </div>
      <div className="stage-num-row">
        <NumberField
          label="width"
          value={room.dims_m[0]}
          min={0.5}
          max={20}
          step={0.1}
          suffix="m"
          onChange={(value) => onChange({ ...scene, room: { ...room, dims_m: [value, room.dims_m[1], room.dims_m[2]] } })}
        />
        <NumberField
          label="depth"
          value={room.dims_m[1]}
          min={0.5}
          max={20}
          step={0.1}
          suffix="m"
          onChange={(value) => onChange({ ...scene, room: { ...room, dims_m: [room.dims_m[0], value, room.dims_m[2]] } })}
        />
        <NumberField
          label="height"
          value={room.dims_m[2]}
          min={0.5}
          max={6}
          step={0.1}
          suffix="m"
          onChange={(value) => onChange({ ...scene, room: { ...room, dims_m: [room.dims_m[0], room.dims_m[1], value] } })}
        />
      </div>
      <label className="stage-slider">
        <span>walls</span>
        <input
          type="range"
          min={0.02}
          max={0.98}
          step={0.01}
          value={room.absorption[0]}
          onChange={(event) => {
            const value = Number(event.target.value);
            onChange({ ...scene, room: { ...room, absorption: applyAbsorption(value) } });
          }}
        />
        <em>{room.absorption[0].toFixed(2)}</em>
      </label>
      <label className="stage-slider">
        <span>reflections</span>
        <input
          type="range"
          min={0}
          max={4}
          step={1}
          value={room.reflections}
          onChange={(event) => onChange({ ...scene, room: { ...room, reflections: Number(event.target.value) } })}
        />
        <em>{room.reflections === 0 ? "off" : `order ${room.reflections}`}</em>
      </label>
    </div>
  );
}

function ActorPanel({
  actor,
  path,
  listener,
  room,
  onChange,
  onPathChange,
  onRemove,
}: {
  actor: StageActor;
  path?: StagePath;
  listener: Vec3;
  room: Vec3;
  onChange: (changes: Partial<StageActor>) => void;
  onPathChange: (updater: (keys: PathKey[]) => PathKey[]) => void;
  onRemove: () => void;
}) {
  const distance = planDistance(actor.position_m, listener);
  const bearing = bearingDeg(actor.position_m, listener);

  const seedWalk = () => {
    const [x, y, z] = actor.position_m;
    // Two keys, three seconds apart, starting where the actor stands. A
    // zero-length walk is honest: the user drags the second handle to make it
    // a move, and nothing is invented on their behalf.
    const keys: PathKey[] = [
      [0, x, y, z],
      [3, x, y, z],
    ];
    onPathChange(() => keys);
  };

  const addKey = () => {
    onPathChange((keys) => {
      const last: PathKey = keys[keys.length - 1] ?? [0, actor.position_m[0], actor.position_m[1], actor.position_m[2]];
      return [...keys, [last[0] + 1, last[1], last[2], last[3]]];
    });
  };

  const removeKey = (index: number) => {
    // patchPath drops a path below two keys, so removing down to one clears it
    // rather than leaving a stub that renders nothing.
    onPathChange((keys) => keys.filter((_key, position) => position !== index));
  };

  const updateKey = (
    index: number,
    changes: { time?: number; x?: number; y?: number; z?: number },
  ) => {
    onPathChange((keys) =>
      keys.map((key, position): PathKey =>
        position === index
          ? [
              changes.time ?? key[0],
              changes.x ?? key[1],
              changes.y ?? key[2],
              changes.z ?? key[3],
            ]
          : key,
      ),
    );
  };
  return (
    <div className="stage-panel" style={{ borderColor: speakerColor(actor.speaker) }}>
      <span className="stage-side__title" style={{ color: speakerColor(actor.speaker) }}>
        {actor.speaker}
      </span>
      <p className="stage-readout">
        {distance.toFixed(2)} m away, {bearing}° {Math.abs(bearing) < 12 ? "ahead" : bearing > 0 ? "left" : "right"}
      </p>
      {path ? (
        <p className="stage-readout stage-readout--motion">
          walks {pathLength(path)} m, {path.keys[0][0].toFixed(1)} s →{" "}
          {path.keys[path.keys.length - 1][0].toFixed(1)} s
        </p>
      ) : null}
      <div className="stage-num-row">
        <NumberField
          label="x"
          value={actor.position_m[0]}
          min={0}
          max={room[0]}
          step={0.05}
          suffix="m"
          onChange={(value) => onChange({ position_m: [value, actor.position_m[1], actor.position_m[2]] })}
        />
        <NumberField
          label="y"
          value={actor.position_m[1]}
          min={0}
          max={room[1]}
          step={0.05}
          suffix="m"
          onChange={(value) => onChange({ position_m: [actor.position_m[0], value, actor.position_m[2]] })}
        />
        <NumberField
          label="height"
          value={actor.position_m[2]}
          min={0}
          max={room[2]}
          step={0.05}
          suffix="m"
          onChange={(value) => onChange({ position_m: [actor.position_m[0], actor.position_m[1], value] })}
        />
      </div>
      <label className="stage-slider">
        <span>facing</span>
        <input
          type="range"
          min={-180}
          max={180}
          step={1}
          value={actor.facing_deg}
          onChange={(event) => onChange({ facing_deg: Number(event.target.value) })}
        />
        <em>{actor.facing_deg}°</em>
      </label>
      <label className="stage-slider">
        <span>trim</span>
        <input
          type="range"
          min={-48}
          max={12}
          step={0.5}
          value={actor.gain_db}
          onChange={(event) => onChange({ gain_db: Number(event.target.value) })}
        />
        <em>{actor.gain_db} dB</em>
      </label>
      <label className="stage-slider">
        <span>in room</span>
        <input
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={actor.direct}
          onChange={(event) => onChange({ direct: Number(event.target.value) })}
        />
        <em>{Math.round(actor.direct * 100)}%</em>
      </label>
      <p className="stage-hint">
        Lower “in room” pulls the voice towards a dry, centred narration.
      </p>
      <div className="stage-motion">
        <span className="stage-side__title">Motion path</span>
        {path ? (
          <>
            <div className="stage-motion__keys">
              {path.keys.map((key, index) => (
                <div className="stage-key" key={index}>
                  <div className="stage-key__head">
                    <span className="stage-key__n">{index + 1}</span>
                    <TimeField
                      value={key[0]}
                      max={3600}
                      onChange={(value) => updateKey(index, { time: value })}
                    />
                    <button
                      type="button"
                      className="chip"
                      onClick={() => removeKey(index)}
                      aria-label={`remove waypoint ${index + 1}`}
                    >
                      ✕
                    </button>
                  </div>
                  <div className="stage-num-row">
                    <NumberField
                      label="x"
                      value={key[1]}
                      min={0}
                      max={room[0]}
                      step={0.05}
                      suffix="m"
                      onChange={(value) => updateKey(index, { x: value })}
                    />
                    <NumberField
                      label="y"
                      value={key[2]}
                      min={0}
                      max={room[1]}
                      step={0.05}
                      suffix="m"
                      onChange={(value) => updateKey(index, { y: value })}
                    />
                    <NumberField
                      label="height"
                      value={key[3]}
                      min={0}
                      max={room[2]}
                      step={0.05}
                      suffix="m"
                      onChange={(value) => updateKey(index, { z: value })}
                    />
                  </div>
                </div>
              ))}
            </div>
            <div className="stage-motion__actions">
              <button type="button" className="button" onClick={addKey}>
                ＋ keyframe
              </button>
              <button type="button" className="button button--ghost" onClick={() => onPathChange(() => [])}>
                Clear path
              </button>
            </div>
          </>
        ) : (
          <>
            <button type="button" className="button" onClick={seedWalk}>
              Add walk
            </button>
            <p className="stage-hint">
              A walk is two keyframes on the scene clock; drag the handles on the board
              or edit the numbers.
            </p>
          </>
        )}
      </div>
      <button type="button" className="button button--ghost" onClick={onRemove}>
        <SpeakerHigh size={14} /> Remove from scene
      </button>
    </div>
  );
}

function ListenerPanel({ scene, onChange }: { scene: SceneStage; onChange: (next: SceneStage) => void }) {
  const [x, y, z] = scene.listener.position_m;
  const room = scene.room.dims_m;
  return (
    <div className="stage-panel">
      <span className="stage-side__title">
        <HardDrives size={14} /> Listener
      </span>
      <div className="stage-num-row">
        <NumberField
          label="x"
          value={x}
          min={0}
          max={room[0]}
          step={0.05}
          suffix="m"
          onChange={(value) => onChange({ ...scene, listener: { ...scene.listener, position_m: [value, y, z] } })}
        />
        <NumberField
          label="y"
          value={y}
          min={0}
          max={room[1]}
          step={0.05}
          suffix="m"
          onChange={(value) => onChange({ ...scene, listener: { ...scene.listener, position_m: [x, value, z] } })}
        />
        <NumberField
          label="height"
          value={z}
          min={0}
          max={room[2]}
          step={0.05}
          suffix="m"
          onChange={(value) => onChange({ ...scene, listener: { ...scene.listener, position_m: [x, y, value] } })}
        />
      </div>
      <label className="stage-slider">
        <span>facing</span>
        <input
          type="range"
          min={-180}
          max={180}
          step={1}
          value={scene.listener.facing_deg}
          onChange={(event) => onChange({ ...scene, listener: { ...scene.listener, facing_deg: Number(event.target.value) } })}
        />
        <em>{scene.listener.facing_deg}°</em>
      </label>
      <p className="stage-hint">Everything the mix renders is heard from here.</p>
    </div>
  );
}

/** Catmull-Rom tangents — the mirror of `spatial._path_tangents`. */
function pathTangents(keys: PathKey[]): Vec3[] {
  const count = keys.length;
  return keys.map((key, index) => {
    if (count < 2) return [0, 0, 0];
    let span: number;
    let previous: PathKey;
    let following: PathKey;
    if (index === 0) {
      span = keys[1][0] - key[0];
      previous = key;
      following = keys[1];
    } else if (index === count - 1) {
      span = key[0] - keys[count - 2][0];
      previous = keys[count - 2];
      following = key;
    } else {
      span = keys[index + 1][0] - keys[index - 1][0];
      previous = keys[index - 1];
      following = keys[index + 1];
    }
    if (span <= 0) return [0, 0, 0];
    return [
      (following[1] - previous[1]) / span,
      (following[2] - previous[2]) / span,
      (following[3] - previous[3]) / span,
    ];
  });
}

/** The same cubic Hermite the mix uses, so the board shows the audible walk. */
function samplePath(keys: PathKey[], time: number): Vec3 {
  if (!keys.length) return [0, 0, 0];
  if (time <= keys[0][0]) return [keys[0][1], keys[0][2], keys[0][3]];
  const last = keys[keys.length - 1];
  if (time >= last[0]) return [last[1], last[2], last[3]];

  const tangents = pathTangents(keys);
  for (let index = 0; index < keys.length - 1; index += 1) {
    const [t0, x0, y0, z0] = keys[index];
    const [t1, x1, y1, z1] = keys[index + 1];
    if (time <= t1) {
      const span = t1 - t0;
      if (span <= 0) return [x1, y1, z1];
      const u = (time - t0) / span;
      const u2 = u * u;
      const u3 = u2 * u;
      const h00 = 2 * u3 - 3 * u2 + 1;
      const h10 = u3 - 2 * u2 + u;
      const h01 = -2 * u3 + 3 * u2;
      const h11 = u3 - u2;
      const m0 = tangents[index];
      const m1 = tangents[index + 1];
      return [
        h00 * x0 + h10 * span * m0[0] + h01 * x1 + h11 * span * m1[0],
        h00 * y0 + h10 * span * m0[1] + h01 * y1 + h11 * span * m1[1],
        h00 * z0 + h10 * span * m0[2] + h01 * z1 + h11 * span * m1[2],
      ];
    }
  }
  return [last[1], last[2], last[3]];
}

/** A fine sample of the curve, clamped the way the renderer clamps it. */
function pathPoints(keys: PathKey[], room: Vec3): Vec3[] {
  const start = keys[0][0];
  const end = keys[keys.length - 1][0];
  const span = Math.max(0.001, end - start);
  const steps = Math.max(24, Math.min(320, Math.ceil(span * 40)));
  const points: Vec3[] = [];
  for (let index = 0; index <= steps; index += 1) {
    const [x, y, z] = samplePath(keys, start + (span * index) / steps);
    points.push([
      Math.min(room[0], Math.max(0, x)),
      Math.min(room[1], Math.max(0, y)),
      Math.min(room[2], Math.max(0, z)),
    ]);
  }
  return points;
}

/** Total walked distance of a motion path, in metres, rounded for a label. */
function pathLength(path: StagePath): number {
  let total = 0;
  for (let index = 1; index < path.keys.length; index += 1) {
    total += Math.hypot(
      path.keys[index][1] - path.keys[index - 1][1],
      path.keys[index][2] - path.keys[index - 1][2],
    );
  }
  return Math.round(total * 10) / 10;
}

/** Stage azimuth of `from` as seen by a listener at `to`: 0 ahead, +90 left. */
function bearingDeg(from: Vec3, to: Vec3): number {
  const dx = from[0] - to[0];
  const dy = from[1] - to[1];
  return Math.round((Math.atan2(dx, -dy) * 180) / Math.PI);
}
