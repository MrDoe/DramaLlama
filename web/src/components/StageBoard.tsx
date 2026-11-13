import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowCounterClockwise,
  HardDrives,
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
  const dragRef = useRef<{ speaker: string } | null>(null);
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

  const sceneKey = activeScene ?? String(scenes[0]?.id ?? "");
  const scene = stage.scenes[sceneKey];
  const roomDims = (scene?.room ?? stage.default_room).dims_m;

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
        const existing = current.scenes[key];
        if (!existing) return current;
        return { ...current, scenes: { ...current.scenes, [key]: updater(existing) } };
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
      const layout = await api.layoutScene(project.id, Number(sceneKey));
      patchScene(sceneKey, () => layout);
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

  function onPointerDown(event: React.PointerEvent, speaker: string) {
    event.preventDefault();
    (event.target as Element).setPointerCapture?.(event.pointerId);
    dragRef.current = { speaker };
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
    patchActor(drag.speaker, {
      position_m: [
        Math.round(metresX * 100) / 100,
        Math.round(metresY * 100) / 100,
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
                const [cx, cy] = toPx(actor.position_m);
                const distance = planDistance(actor.position_m, scene.listener.position_m);
                return (
                  <g
                    key={actor.speaker}
                    className={selected === actor.speaker ? "stage-actor stage-actor--on" : "stage-actor"}
                    style={{ color: speakerColor(actor.speaker) }}
                    transform={`translate(${cx} ${cy})`}
                    onPointerDown={(event) => onPointerDown(event, actor.speaker)}
                    tabIndex={0}
                    role="button"
                    aria-label={`${actor.speaker}, ${distance.toFixed(1)} metres`}
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
            </svg>
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
                disabled={!scene.actors.length || !available}
                onClick={async () => {
                  setPreviewUrl(null);
                  setError(null);
                  try {
                    setPreviewJob(await api.previewScene(project.id, Number(sceneKey)));
                  } catch (exc) {
                    setError(exc instanceof ApiError ? exc.message : "preview failed");
                  }
                }}
              >
                <Play size={14} /> Audition scene
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

            {previewUrl ? (
              <audio className="stage-preview" src={previewUrl} controls data-testid="stage-preview" />
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
                listener={scene.listener.position_m}
                room={scene.room.dims_m}
                onChange={(changes) => patchActor(selectedActor.speaker, changes)}
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
  listener,
  room,
  onChange,
  onRemove,
}: {
  actor: StageActor;
  listener: Vec3;
  room: Vec3;
  onChange: (changes: Partial<StageActor>) => void;
  onRemove: () => void;
}) {
  const distance = planDistance(actor.position_m, listener);
  const bearing = bearingDeg(actor.position_m, listener);
  return (
    <div className="stage-panel" style={{ borderColor: speakerColor(actor.speaker) }}>
      <span className="stage-side__title" style={{ color: speakerColor(actor.speaker) }}>
        {actor.speaker}
      </span>
      <p className="stage-readout">
        {distance.toFixed(2)} m away, {bearing}° {Math.abs(bearing) < 12 ? "ahead" : bearing > 0 ? "left" : "right"}
      </p>
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

/** Stage azimuth of `from` as seen by a listener at `to`: 0 ahead, +90 left. */
function bearingDeg(from: Vec3, to: Vec3): number {
  const dx = from[0] - to[0];
  const dy = from[1] - to[1];
  return Math.round((Math.atan2(dx, -dy) * 180) / Math.PI);
}
