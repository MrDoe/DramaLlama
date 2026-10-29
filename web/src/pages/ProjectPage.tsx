import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  CaretRight,
  CheckCircle,
  Clock,
  DownloadSimple,
  FileAudio,
  FileText,
  ListChecks,
  MagicWand,
  MagnifyingGlass,
  MusicNotes,
  Pause,
  Play,
  SlidersHorizontal,
  Sparkle,
  SpeakerHigh,
  Trash,
  UsersThree,
  WarningCircle,
  Waveform,
} from "@phosphor-icons/react";
import { api } from "../api";
import type { Job, Line, Project, SfxItem, Stem } from "../types";
import { formatDuration, initials, speakerStyle } from "../utils";

const STEPS = [
  { id: "script", label: "Script", icon: FileText },
  { id: "stems", label: "Stems", icon: UsersThree },
  { id: "render", label: "Render", icon: Waveform },
  { id: "sound", label: "Sound", icon: MusicNotes },
  { id: "mix", label: "Mix", icon: SlidersHorizontal },
] as const;

function LineCard({ line }: { line: Line }) {
  return (
    <div className="line-card">
      <span className="speaker-badge" style={speakerStyle(line.speaker)}>
        {line.speaker}
      </span>
      <div className="line-body">
        <p className="line-text">
          {line.text}
          <span className="line-id">{line.id}</span>
        </p>
        {line.direction ? <p className="line-direction">{line.direction}</p> : null}
      </div>
    </div>
  );
}

function CoveragePanel({ project }: { project: Project }) {
  const report = project.report;
  const coverage = Math.round((report?.coverage ?? 0) * 100);
  return (
    <section className="panel">
      <div className="panel-head">
        <div>
          <h2 className="panel-title">
            <CheckCircle size={15} weight="fill" />
            Parse coverage
          </h2>
          <p className="panel-sub">dialogue accounted for</p>
        </div>
        <span className={`badge ${coverage === 100 ? "badge-accent" : ""}`}>
          {coverage}%
        </span>
      </div>
      <div className="coverage">
        <div className="coverage-head">
          <span>{report?.unknown_lines ?? 0} unparsed</span>
          <strong>{coverage}%</strong>
        </div>
        <div
          className="coverage-bar"
          role="progressbar"
          aria-valuenow={coverage}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div className="coverage-fill" style={{ width: `${coverage}%` }} />
        </div>

        {report && report.warnings.length > 0 ? (
          <div className="warnings">
            {report.warnings.slice(0, 4).map((warning, index) => (
              <div className="warning-item" key={`${warning.source_line}-${index}`}>
                <WarningCircle size={11} weight="fill" />
                <span>
                  line {warning.source_line}: {warning.message}
                </span>
              </div>
            ))}
            {report.warnings.length > 4 ? (
              <div className="warning-item">
                <span>+ {report.warnings.length - 4} more warnings</span>
              </div>
            ) : null}
          </div>
        ) : (
          <div className="warnings">
            <div className="warning-item">
              <CheckCircle size={11} weight="fill" style={{ color: "var(--green)" }} />
              <span>Every dialogue line was accounted for.</span>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

function CastPanel({
  project,
  jobs,
  presets,
  onDesign,
  onPreset,
}: {
  project: Project;
  jobs: Record<string, Job>;
  presets: string[];
  onDesign: (speaker: string) => void;
  onPreset: (speaker: string, preset: string) => void;
}) {
  const lines = project.script.scenes.flatMap((scene) => scene.lines);
  const cast = useMemo(
    () =>
      Object.values(project.script.characters)
        .map((character) => ({
          ...character,
          count: lines.filter((line) => line.speaker === character.id).length,
        }))
        .sort((a, b) => b.count - a.count),
    [project.script.characters, lines],
  );

  return (
    <section className="panel">
      <div className="panel-head">
        <div>
          <h2 className="panel-title">
            <UsersThree size={15} weight="fill" />
            Cast
          </h2>
          <p className="panel-sub">{cast.length} characters detected</p>
        </div>
      </div>
      <div className="cast-panel">
        {cast.length === 0 ? (
          <p className="cast-desc">No speakers yet.</p>
        ) : (
          cast.map((character) => {
            const job = jobs[`char:${character.id}`];
            const busy = job?.status === "queued" || job?.status === "running";
            return (
              <div className="cast-card" key={character.id}>
                <span className="avatar" style={speakerStyle(character.id)}>
                  {initials(character.name)}
                </span>
                <div className="cast-body">
                  <p className="cast-name">{character.name}</p>
                  <p className="cast-lines">
                    {character.count} line{character.count === 1 ? "" : "s"} ·{" "}
                    {project.language === "de" || character.engine === "qwen3"
                      ? "Qwen3 preset"
                      : "Breeze reference"}
                  </p>
                  {character.description ? (
                    <p className="cast-desc">{character.description}</p>
                  ) : null}
                  <div className="cast-voice">
                    {project.language === "de" ? (
                      <label className="cast-preset">
                        <span>Qwen3 preset</span>
                        <select
                          value={character.qwen_speaker ?? ""}
                          onChange={(event) =>
                            onPreset(character.id, event.target.value)
                          }
                        >
                          <option value="">Choose…</option>
                          {presets.map((preset) => (
                            <option key={preset} value={preset}>
                              {preset}
                            </option>
                          ))}
                        </select>
                      </label>
                    ) : character.ref_file ? (
                      <span className="badge">voice ready</span>
                    ) : (
                      <button
                        type="button"
                        className="button button-secondary button-sm"
                        onClick={() => onDesign(character.id)}
                        disabled={busy}
                      >
                        {busy ? <span className="spinner" /> : <Waveform size={12} />}
                        {busy ? "Designing…" : "Design voice"}
                      </button>
                    )}
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>
    </section>
  );
}

function StemsBoard({
  project,
  building,
  onBuild,
}: {
  project: Project;
  building: boolean;
  onBuild: () => void;
}) {
  const byScene = project.script.scenes
    .map((scene) => ({
      scene,
      stems: project.stems.filter((stem) => stem.scene === scene.id),
    }))
    .filter((entry) => entry.stems.length > 0);

  return (
    <div>
      <section className="panel stems-toolbar">
        <div>
          <h2 className="panel-title">
            <UsersThree size={15} weight="fill" />
            Stem scripts
          </h2>
          <p className="panel-sub">
            one continuous performance per character per scene ·{" "}
            {project.stems.length} stems
          </p>
        </div>
        <div className="panel-actions">
          <span className="badge">{project.stems.length} stems</span>
          <button
            type="button"
            className="button button-primary button-sm"
            onClick={onBuild}
            disabled={building}
          >
            {building ? <span className="spinner" /> : <MagicWand size={14} weight="fill" />}
            {building ? "Building…" : project.stems.length ? "Rebuild" : "Build stems"}
          </button>
        </div>
      </section>

      {byScene.length === 0 ? (
        <div className="empty-list" style={{ marginTop: 16 }}>
          No stems yet. Build them from the parsed script — lines are grouped by
          scene and speaker, ready for one Breeze take per character.
        </div>
      ) : (
        <div className="stems-board">
          {byScene.map(({ scene, stems }) => (
            <section className="stem-scene" key={scene.id}>
              <div className="scene-head">
                <span className="scene-number">{String(scene.id).padStart(2, "0")}</span>
                <span className="scene-heading">{scene.heading || `Scene ${scene.id}`}</span>
                <span className="badge" style={{ marginLeft: "auto" }}>
                  {stems.length} stems
                </span>
              </div>
              <div className="stem-grid">
                {stems.map((stem) => (
                  <StemCard key={stem.id} stem={stem} />
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}

function StemCard({ stem }: { stem: Stem }) {
  const seconds = Math.max(1, stem.text.split(/\s+/).length / 2.6);
  const status = stem.takes.length
    ? `${stem.takes.length} take${stem.takes.length === 1 ? "" : "s"}`
    : "not rendered";
  return (
    <article className="panel stem-card">
      <div className="stem-card-head">
        <span className="speaker-badge" style={speakerStyle(stem.speaker)}>
          {stem.speaker}
        </span>
        <span className={`badge ${stem.takes.length ? "badge-accent" : ""}`}>
          {status}
        </span>
      </div>
      <p className="stem-text">{stem.text}</p>
      <p className="stem-instruction">{stem.instruction}</p>
      <div className="stem-foot">
        <span>{stem.line_ids.length} lines</span>
        <span>~{formatDuration(seconds)}</span>
        <span>cfg {stem.cfg_scale}</span>
        <span className="stem-id">{stem.id}</span>
      </div>
    </article>
  );
}

function RenderBoard({
  project,
  jobs,
  onRender,
  onRefresh,
}: {
  project: Project;
  jobs: Record<string, Job>;
  onRender: (stem: Stem) => void;
  onRefresh: () => void;
}) {
  const byScene = project.script.scenes
    .map((scene) => ({
      scene,
      stems: project.stems.filter((stem) => stem.scene === scene.id),
    }))
    .filter((entry) => entry.stems.length > 0);

  const voiceReady = (speaker: string) => {
    const character = project.script.characters[speaker];
    return project.language === "de"
      ? Boolean(character?.qwen_speaker)
      : Boolean(character?.ref_file);
  };
  const ready = project.stems.filter((stem) => voiceReady(stem.speaker)).length;

  return (
    <div>
      <section className="panel stems-toolbar">
        <div>
          <h2 className="panel-title">
            <Waveform size={15} weight="fill" />
            Render queue
          </h2>
          <p className="panel-sub">
            {ready}/{project.stems.length} stems have a reference voice · Breeze
            on GPU · Whisper on CPU
          </p>
        </div>
        <div className="panel-actions">
          <button type="button" className="button button-ghost button-sm" onClick={onRefresh}>
            Refresh
          </button>
        </div>
      </section>

      {byScene.length === 0 ? (
        <div className="empty-list">
          Build stems first, then design a reference voice for each character.
        </div>
      ) : (
        <div className="stems-board">
          {byScene.map(({ scene, stems }) => (
            <section className="stem-scene" key={scene.id}>
              <div className="scene-head">
                <span className="scene-number">{String(scene.id).padStart(2, "0")}</span>
                <span className="scene-heading">{scene.heading || `Scene ${scene.id}`}</span>
              </div>
              <div className="render-grid">
                {stems.map((stem) => {
                  const character = project.script.characters[stem.speaker];
                  const readyForStem = voiceReady(stem.speaker);
                  const job = jobs[stem.id];
                  const busy =
                    job?.status === "queued" || job?.status === "running";
                  const alignment = project.alignments[stem.id];
                  const take = stem.takes.find((item) => item.selected) ?? stem.takes[0];
                  const lowConfidence =
                    alignment?.lines.filter((line) => line.fallback).length ?? 0;
                  const averageConfidence = alignment?.lines.length
                    ? alignment.lines.reduce(
                        (sum, line) => sum + (line.confidence ?? 0),
                        0,
                      ) / alignment.lines.length
                    : null;
                  return (
                    <article className="panel render-card" key={stem.id}>
                      <div className="render-card-head">
                        <span className="speaker-badge" style={speakerStyle(stem.speaker)}>
                          {stem.speaker}
                        </span>
                        <span
                          className={`badge ${
                            job?.status === "succeeded"
                              ? "badge-accent"
                              : job?.status === "failed"
                                ? "badge-danger"
                                : ""
                          }`}
                        >
                          {busy
                            ? job?.status
                            : take
                              ? `${stem.takes.length} take${stem.takes.length === 1 ? "" : "s"}`
                              : "not rendered"}
                        </span>
                      </div>

                      {busy ? (
                        <div className="render-progress">
                          <div className="render-progress-head">
                            <span>{job?.message || job?.status}</span>
                            <strong>{Math.round((job?.progress ?? 0) * 100)}%</strong>
                          </div>
                          <div className="coverage-bar">
                            <div
                              className="coverage-fill"
                              style={{ width: `${(job?.progress ?? 0) * 100}%` }}
                            />
                          </div>
                        </div>
                      ) : null}

                      <div className="render-meta">
                        <span>{stem.line_ids.length} lines</span>
                        <span>scene {stem.scene}</span>
                        {take ? <span>{formatDuration(take.duration_s)} audio</span> : null}
                        {take?.f0_hz ? <span>F0 {Math.round(take.f0_hz)} Hz</span> : null}
                        {take?.mfcc_sim ? <span>MFCC {take.mfcc_sim.toFixed(3)}</span> : null}
                      </div>

                      {alignment ? (
                        <div className="render-align">
                          <span>
                            align {alignment.lines.length}/{stem.line_ids.length} lines
                          </span>
                          <span>
                            conf{" "}
                            {averageConfidence !== null
                              ? `${Math.round(averageConfidence * 100)}%`
                              : "n/a"}
                          </span>
                          {lowConfidence > 0 ? (
                            <span className="render-warn">{lowConfidence} low</span>
                          ) : null}
                        </div>
                      ) : null}

                      {job?.status === "failed" ? (
                        <p className="render-error">{job.error?.split("\n")[0]}</p>
                      ) : null}

                      <div className="render-actions">
                        {readyForStem ? (
                          <span className="badge">
                            {project.language === "de"
                              ? character?.qwen_speaker
                              : "ref ready"}
                          </span>
                        ) : (
                          <span className="render-missing">
                            {project.language === "de"
                              ? "choose a preset voice"
                              : "no reference voice"}
                          </span>
                        )}
                        <button
                          type="button"
                          className="button button-primary button-sm"
                          onClick={() => onRender(stem)}
                          disabled={!readyForStem || busy}
                        >
                          {busy ? <span className="spinner" /> : <Waveform size={14} weight="bold" />}
                          {busy ? "Rendering…" : take ? "Re-render" : "Render"}
                        </button>
                      </div>
                    </article>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}

function SoundBoard({
  project,
  onProject,
  onError,
}: {
  project: Project;
  onProject: (project: Project) => void;
  onError: (message: string) => void;
}) {
  const lines = project.script.scenes.flatMap((scene) => scene.lines);
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<SfxItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [playing, setPlaying] = useState<string | null>(null);
  const [anchorLineId, setAnchorLineId] = useState(lines[0]?.id ?? "");
  const [kind, setKind] = useState<"one_shot" | "bed" | "music">("one_shot");
  const [gain, setGain] = useState(-12);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    const handle = window.setTimeout(() => {
      setLoading(true);
      api
        .searchSfx(query)
        .then(setItems)
        .catch((reason: Error) => onError(reason.message))
        .finally(() => setLoading(false));
    }, 220);
    return () => window.clearTimeout(handle);
  }, [query, onError]);

  useEffect(() => {
    if (!anchorLineId && lines[0]) setAnchorLineId(lines[0].id);
  }, [anchorLineId, lines]);

  useEffect(() => {
    return () => {
      audioRef.current?.pause();
    };
  }, []);

  function togglePreview(item: SfxItem) {
    if (playing === item.id) {
      audioRef.current?.pause();
      setPlaying(null);
      return;
    }
    if (!audioRef.current) {
      audioRef.current = new Audio();
      audioRef.current.onended = () => setPlaying(null);
    }
    audioRef.current.src = api.sfxAudioUrl(item.id);
    audioRef.current
      .play()
      .then(() => setPlaying(item.id))
      .catch((reason: Error) => onError(reason.message));
  }

  async function addCue(item: SfxItem) {
    if (!anchorLineId) {
      onError("Choose a line to anchor the cue to.");
      return;
    }
    try {
      const updated = await api.addCue(project.id, {
        kind,
        sfx_id: item.id,
        name: item.name,
        anchor_line_id: anchorLineId,
        gain_db: gain,
        loop: kind === "bed",
        duck: true,
      });
      onProject(updated);
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Could not add cue");
    }
  }

  async function removeCue(cueId: string) {
    try {
      await api.deleteCue(project.id, cueId);
      const updated = await api.getProject(project.id);
      onProject(updated);
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Could not remove cue");
    }
  }

  const lineLabel = (lineId: string) => {
    const line = lines.find((item) => item.id === lineId);
    return line ? `${line.speaker}: ${line.text.slice(0, 46)}` : lineId;
  };

  return (
    <div className="sound-grid">
      <section className="panel">
        <div className="panel-head">
          <div>
            <h2 className="panel-title">
              <SpeakerHigh size={15} weight="fill" />
              SFX library
            </h2>
            <p className="panel-sub">
              {items.length} results · local folders, no upload needed
            </p>
          </div>
          <button
            type="button"
            className="button button-ghost button-sm"
            onClick={() => {
              setLoading(true);
              api
                .scanSfx()
                .then(() => api.searchSfx(query))
                .then(setItems)
                .catch((reason: Error) => onError(reason.message))
                .finally(() => setLoading(false));
            }}
          >
            Rescan
          </button>
        </div>

        <div className="sfx-search">
          <MagnifyingGlass size={15} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="rain, door, telephone, clock…"
            aria-label="Search sound effects"
          />
        </div>

        <div className="sfx-list">
          {loading ? (
            <div className="loading-page" style={{ minHeight: 160 }}>
              <span className="spinner" />
            </div>
          ) : items.length === 0 ? (
            <div className="empty-list">
              No matches. Check the SFX roots in settings or upload a file.
            </div>
          ) : (
            items.map((item) => (
              <div className="sfx-row" key={item.id}>
                <button
                  type="button"
                  className="sfx-play"
                  onClick={() => togglePreview(item)}
                  aria-label={playing === item.id ? "Stop preview" : "Preview sound"}
                >
                  {playing === item.id ? <Pause size={13} weight="fill" /> : <Play size={13} weight="fill" />}
                </button>
                <div className="sfx-name">
                  <strong>{item.name}</strong>
                  <span>
                    {item.folder} · {formatDuration(item.duration_s)}
                  </span>
                </div>
                <button
                  type="button"
                  className="button button-secondary button-sm"
                  onClick={() => void addCue(item)}
                >
                  Add
                </button>
              </div>
            ))
          )}
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <div>
            <h2 className="panel-title">
              <MusicNotes size={15} weight="fill" />
              Cue sheet
            </h2>
            <p className="panel-sub">{project.cues.length} cues anchored to lines</p>
          </div>
        </div>

        <div className="cue-controls">
          <label className="cue-field">
            <span>Anchor line</span>
            <select
              value={anchorLineId}
              onChange={(event) => setAnchorLineId(event.target.value)}
            >
              {lines.map((line) => (
                <option key={line.id} value={line.id}>
                  {lineLabel(line.id)}
                </option>
              ))}
            </select>
          </label>
          <div className="cue-inline">
            <label className="cue-field">
              <span>Type</span>
              <select value={kind} onChange={(event) => setKind(event.target.value as typeof kind)}>
                <option value="one_shot">one-shot</option>
                <option value="bed">bed</option>
                <option value="music">music</option>
              </select>
            </label>
            <label className="cue-field">
              <span>Gain</span>
              <input
                type="number"
                value={gain}
                min={-60}
                max={6}
                step={1}
                onChange={(event) => setGain(Number(event.target.value))}
              />
            </label>
          </div>
        </div>

        <div className="cue-list">
          {project.cues.length === 0 ? (
            <div className="empty-list">
              Search the library, pick a line, then add a cue.
            </div>
          ) : (
            project.cues.map((cue) => (
              <div className="cue-row" key={cue.id}>
                <span className={`badge ${cue.kind === "bed" ? "badge-violet" : "badge-accent"}`}>
                  {cue.kind}
                </span>
                <div className="cue-body">
                  <strong>{cue.name}</strong>
                  <span>
                    {lineLabel(cue.anchor_line_id)} · {cue.gain_db} dB
                  </span>
                </div>
                <button
                  type="button"
                  className="button button-icon button-sm"
                  onClick={() => void removeCue(cue.id)}
                  aria-label="Remove cue"
                >
                  <Trash size={13} />
                </button>
              </div>
            ))
          )}
        </div>
      </section>
    </div>
  );
}

function MixBoard({
  project,
  job,
  onRender,
  onRefresh,
}: {
  project: Project;
  job?: Job;
  onRender: () => void;
  onRefresh: () => void;
}) {
  const mix = project.mix;
  const busy = job?.status === "queued" || job?.status === "running";
  return (
    <div className="mix-layout">
      <section className="panel mix-hero">
        <div className="mix-hero-copy">
          <p className="eyebrow">Final master</p>
          <h2>Put the scene in a room.</h2>
          <p>
            Dialogue is assembled from the aligned line clips, then beds and
            one-shots are placed, ducked under speech, EQ'd and loudness-normalised.
          </p>
          <div className="hero-actions">
            <button
              type="button"
              className="button button-primary"
              onClick={onRender}
              disabled={busy}
            >
              {busy ? <span className="spinner" /> : <FileAudio size={16} weight="fill" />}
              {busy ? "Mixing…" : mix ? "Re-render mix" : "Render mix"}
            </button>
            {mix ? (
              <button type="button" className="button button-secondary" onClick={onRefresh}>
                Refresh
              </button>
            ) : null}
          </div>
        </div>

        <div className="mix-status">
          {busy ? (
            <>
              <div className="coverage-head">
                <span>{job?.message || job?.status}</span>
                <strong>{Math.round((job?.progress ?? 0) * 100)}%</strong>
              </div>
              <div className="coverage-bar">
                <div
                  className="coverage-fill"
                  style={{ width: `${(job?.progress ?? 0) * 100}%` }}
                />
              </div>
            </>
          ) : mix ? (
            <>
              <div className="mix-metric">
                <span>duration</span>
                <strong>{formatDuration(mix.duration_s)}</strong>
              </div>
              <div className="mix-metric">
                <span>target</span>
                <strong>{mix.loudness_target}</strong>
              </div>
              <div className="mix-metric">
                <span>lines</span>
                <strong>{mix.line_count}</strong>
              </div>
              <div className="mix-metric">
                <span>cues</span>
                <strong>{mix.cue_count}</strong>
              </div>
            </>
          ) : (
            <div className="empty-list">
              No master yet. Render the aligned stems, add cues, then mix.
            </div>
          )}
          {job?.status === "failed" ? (
            <p className="render-error">{job.error?.split("\n")[0]}</p>
          ) : null}
        </div>
      </section>

      {mix ? (
        <section className="panel mix-player">
          <div className="panel-head">
            <div>
              <h2 className="panel-title">
                <FileAudio size={15} weight="fill" />
                Master
              </h2>
              <p className="panel-sub">
                {mix.file} · {mix.loudness_target}
              </p>
            </div>
            <div className="panel-actions">
              <a
                className="button button-secondary button-sm"
                href={api.fileUrl(project.id, mix.file)}
                download
              >
                <DownloadSimple size={13} />
                WAV
              </a>
              {mix.mp3 ? (
                <a
                  className="button button-secondary button-sm"
                  href={api.fileUrl(project.id, mix.mp3)}
                  download
                >
                  <DownloadSimple size={13} />
                  MP3
                </a>
              ) : null}
            </div>
          </div>
          <div className="mix-audio">
            <audio controls preload="metadata" src={api.fileUrl(project.id, mix.file)} />
          </div>
          {mix.warnings.length > 0 ? (
            <div className="warnings" style={{ padding: "0 18px 18px" }}>
              {mix.warnings.map((warning, index) => (
                <div className="warning-item" key={index}>
                  <WarningCircle size={11} weight="fill" />
                  <span>{warning}</span>
                </div>
              ))}
            </div>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}

export function ProjectPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [project, setProject] = useState<Project | null>(null);
  const [rawScript, setRawScript] = useState("");
  const [loading, setLoading] = useState(true);
  const [parsing, setParsing] = useState(false);
  const [llmParsing, setLlmParsing] = useState(false);
  const [buildingStems, setBuildingStems] = useState(false);
  const [activeStep, setActiveStep] = useState<
    "script" | "stems" | "render" | "sound" | "mix"
  >("script");
  const [jobs, setJobs] = useState<Record<string, Job>>({});
  const [qwenVoices, setQwenVoices] = useState<string[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    api.qwenVoices().then(setQwenVoices).catch(() => setQwenVoices([]));
  }, []);

  useEffect(() => {
    if (!id) return;
    let active = true;
    setLoading(true);
    api
      .getProject(id)
      .then((loaded) => {
        if (!active) return;
        setProject(loaded);
        setRawScript(loaded.raw_script);
        setError("");
      })
      .catch((reason: Error) => {
        if (active) setError(reason.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [id]);

  const dirty = Boolean(project && rawScript !== project.raw_script);

  const saveAndParse = useCallback(async () => {
    if (!project || parsing) return;
    setParsing(true);
    setError("");
    try {
      const updated = await api.updateProject(project.id, {
        raw_script: rawScript,
      });
      setProject(updated);
      setRawScript(updated.raw_script);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not parse the script");
    } finally {
      setParsing(false);
    }
  }, [project, parsing, rawScript]);

  const parseWithLLM = useCallback(async () => {
    if (!project || llmParsing) return;
    setLlmParsing(true);
    setError("");
    try {
      const saved = dirty
        ? await api.updateProject(project.id, { raw_script: rawScript })
        : project;
      const updated = await api.parseProject(saved.id, "llm");
      setProject(updated);
      setRawScript(updated.raw_script);
      setActiveStep("script");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "LLM parse failed");
    } finally {
      setLlmParsing(false);
    }
  }, [project, llmParsing, dirty, rawScript]);

  const buildStems = useCallback(async () => {
    if (!project || buildingStems) return;
    setBuildingStems(true);
    setError("");
    try {
      const saved = dirty
        ? await api.updateProject(project.id, { raw_script: rawScript })
        : project;
      const updated = await api.buildStems(saved.id);
      setProject(updated);
      setRawScript(updated.raw_script);
      setActiveStep("stems");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not build stems");
    } finally {
      setBuildingStems(false);
    }
  }, [project, buildingStems, dirty, rawScript]);

  const refreshProject = useCallback(async () => {
    if (!project) return;
    try {
      const updated = await api.getProject(project.id);
      setProject(updated);
      setRawScript(updated.raw_script);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not refresh project");
    }
  }, [project]);

  const trackJob = useCallback(
    (key: string, job: Job) => {
      setJobs((previous) => ({ ...previous, [key]: job }));
      api.watchJob(job.id, (update) => {
        setJobs((previous) => ({ ...previous, [key]: update }));
        if (update.status === "succeeded") {
          void refreshProject();
        }
      });
    },
    [refreshProject],
  );

  const renderStem = useCallback(
    async (stem: Stem) => {
      if (!project) return;
      setError("");
      try {
        const job = await api.renderStem(project.id, stem.id, {
          candidates: 2,
          cfg_scale: stem.cfg_scale,
          seed: stem.seed,
        });
        trackJob(stem.id, job);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Could not start render");
      }
    },
    [project, trackJob],
  );

  const designVoice = useCallback(
    async (speaker: string) => {
      if (!project) return;
      const character = project.script.characters[speaker];
      if (!character) return;
      const lines = project.script.scenes
        .flatMap((scene) => scene.lines)
        .filter((line) => line.speaker === speaker)
        .slice(0, 3)
        .map((line) => line.text);
      const refText =
        lines.join(" ") ||
        `I am ${character.name}. This is the voice you will hear for the whole scene.`;
      setError("");
      try {
        const job = await api.designReference(project.id, speaker, {
          ref_text: refText,
          ref_instruction:
            (character.description
              ? `${character.description} `
              : "") +
            "Speak in the character's baseline mood, clearly and without music or room noise.",
          cfg_scale: 4.0,
          seed: 42,
        });
        trackJob(`char:${speaker}`, job);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Could not design voice");
      }
    },
    [project, trackJob],
  );

  const renderMix = useCallback(async () => {
    if (!project) return;
    setError("");
    try {
      const job = await api.renderMix(project.id);
      trackJob("mix", job);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not start mix");
    }
  }, [project, trackJob]);

  const updatePreset = useCallback(
    async (speaker: string, preset: string) => {
      if (!project) return;
      try {
        const updated = await api.updateCharacter(project.id, speaker, {
          qwen_speaker: preset || null,
        });
        setProject(updated);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Could not set voice preset");
      }
    },
    [project],
  );

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
        event.preventDefault();
        void saveAndParse();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [saveAndParse]);

  async function removeProject() {
    if (!project) return;
    if (!window.confirm(`Delete “${project.title}”? This removes its project folder.`)) {
      return;
    }
    try {
      await api.deleteProject(project.id);
      navigate("/");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not delete project");
    }
  }

  if (loading) {
    return (
      <div className="page">
        <div className="loading-page">
          <span className="spinner" />
        </div>
      </div>
    );
  }

  if (!project) {
    return (
      <div className="page">
        <div className="error-banner" role="alert">
          <WarningCircle size={16} weight="fill" />
          {error || "Project not found"}
        </div>
        <Link to="/" className="button button-secondary">
          Back to projects
        </Link>
      </div>
    );
  }

  const report = project.report;
  const lineCount = project.script.scenes.reduce(
    (sum, scene) => sum + scene.lines.length,
    0,
  );

  return (
    <div className="page">
      <Link to="/" className="back-link">
        <ArrowLeft size={13} />
        Projects
      </Link>

      <header className="project-header">
        <div>
          <div className="project-title-row">
            <h1 className="project-title">{project.title}</h1>
            <span className={`badge ${project.language === "de" ? "badge-violet" : "badge-accent"}`}>
              {project.language === "de" ? "Deutsch · Qwen3" : "English · Breeze"}
            </span>
          </div>
          <p className="project-sub">
            {project.id} · updated {new Date(project.updated_at).toLocaleString()}
          </p>
        </div>
        <div className="project-actions">
          <button
            type="button"
            className="button button-icon"
            onClick={removeProject}
            aria-label="Delete project"
            title="Delete project"
          >
            <Trash size={16} />
          </button>
          <button
            type="button"
            className="button button-secondary"
            onClick={() => void parseWithLLM()}
            disabled={llmParsing || parsing}
            title="Use the local LLM for speaker attribution and scene parsing"
          >
            {llmParsing ? <span className="spinner" /> : <Sparkle size={16} weight="fill" />}
            {llmParsing ? "LLM parsing…" : "LLM parse"}
          </button>
          <button
            type="button"
            className="button button-primary"
            onClick={() => void saveAndParse()}
            disabled={parsing}
          >
            {parsing ? <span className="spinner" /> : <MagicWand size={16} weight="fill" />}
            {parsing ? "Parsing…" : "Save & parse"}
          </button>
        </div>
      </header>

      {error ? (
        <div className="error-banner" role="alert">
          <WarningCircle size={16} weight="fill" />
          {error}
        </div>
      ) : null}

      <nav className="step-rail" aria-label="Production steps">
        {STEPS.map((step, index) => {
          const Icon = step.icon;
          const available =
            step.id === "script" ||
            ((step.id === "stems" ||
              step.id === "render" ||
              step.id === "sound" ||
              step.id === "mix") &&
              project.stems.length > 0);
          const active = step.id === activeStep;
          return (
            <button
              key={step.id}
              type="button"
              className={`step${active ? " active" : ""}${available ? "" : " disabled"}`}
              disabled={!available}
              onClick={() => {
                if (
                  step.id === "script" ||
                  step.id === "stems" ||
                  step.id === "render" ||
                  step.id === "sound" ||
                  step.id === "mix"
                ) {
                  setActiveStep(step.id);
                }
              }}
              title={
                available
                  ? undefined
                  : step.id === "stems"
                    ? "Build stems from the script first"
                    : "Next milestone"
              }
            >
              <span className="step-num">{String(index + 1).padStart(2, "0")}</span>
              <Icon size={14} />
              {step.label}
              {active ? <CaretRight size={12} /> : null}
            </button>
          );
        })}
      </nav>

      <div className="stats-row">
        <span className="stat">
          <strong>{project.script.scenes.length}</strong> scenes
        </span>
        <span className="stat">
          <strong>{lineCount}</strong> lines
        </span>
        <span className="stat">
          <Clock size={12} />
          <strong>{formatDuration(report?.runtime_estimate_s ?? 0)}</strong> est.
        </span>
        <span className="stat">
          <strong>{report?.stage_directions ?? 0}</strong> stage beats
        </span>
        <span className="stat">
          <strong>{project.cues.length}</strong> audio cues
        </span>
        <span className="stat">
          <strong>{Object.keys(project.script.characters).length}</strong> speakers
        </span>
        <span className="stat">
          <strong>{project.stems.length}</strong> stems
        </span>
      </div>

      {activeStep === "script" ? (
        <div className="workspace">
        <section className="panel editor-wrap">
          <div className="panel-head">
            <div>
              <h2 className="panel-title">
                <FileText size={15} weight="fill" />
                Source script
              </h2>
              <p className="panel-sub">raw text · dialogue is preserved</p>
            </div>
          </div>
          <textarea
            className="raw-editor"
            value={rawScript}
            onChange={(event) => setRawScript(event.target.value)}
            spellCheck={false}
            aria-label="Source script"
          />
          <div className="editor-foot">
            <span>
              {dirty ? <span className="dirty-dot" /> : null}
              {dirty ? "Unsaved edits" : "Synced"}
            </span>
            <span>⌘/Ctrl + Enter to parse</span>
          </div>
        </section>

        <section className="panel">
          <div className="panel-head">
            <div>
              <h2 className="panel-title">
                <ListChecks size={15} weight="fill" />
                Parsed script
              </h2>
              <p className="panel-sub">
                {lineCount} lines · {report?.unknown_lines ?? 0} unparsed
              </p>
            </div>
            <div className="panel-actions">
              <button
                type="button"
                className="button button-ghost button-sm"
                onClick={() => void saveAndParse()}
                disabled={parsing}
              >
                Reparse
              </button>
              <button
                type="button"
                className="button button-secondary button-sm"
                onClick={() => void buildStems()}
                disabled={buildingStems || lineCount === 0}
              >
                {buildingStems ? <span className="spinner" /> : <UsersThree size={13} />}
                {buildingStems ? "Building…" : "Stems"}
              </button>
            </div>
          </div>

          <div className="parsed">
            {project.script.scenes.length === 0 ? (
              <div className="empty-list">
                No scenes yet. Paste a script and press <strong>Save &amp; parse</strong>.
              </div>
            ) : (
              project.script.scenes.map((scene) => (
                <div className="scene-block" key={scene.id}>
                  <div className="scene-head">
                    <span className="scene-number">{String(scene.id).padStart(2, "0")}</span>
                    <span className="scene-heading">{scene.heading || `Scene ${scene.id}`}</span>
                  </div>

                  {scene.stage_directions.length > 0 ? (
                    <div className="stage-list">
                      {scene.stage_directions.map((direction, index) => (
                        <span className="stage-chip" key={`${scene.id}-${index}`} title={direction}>
                          <MusicNotes size={9} />
                          {direction}
                        </span>
                      ))}
                    </div>
                  ) : null}

                  {scene.lines.map((line) => (
                    <LineCard key={line.id} line={line} />
                  ))}
                </div>
              ))
            )}
          </div>
        </section>

        <div>
          <CoveragePanel project={project} />
          <div style={{ height: 16 }} />
          <CastPanel
            project={project}
            jobs={jobs}
            presets={qwenVoices}
            onDesign={designVoice}
            onPreset={(speaker, preset) => void updatePreset(speaker, preset)}
          />
        </div>
        </div>
      ) : activeStep === "stems" ? (
        <StemsBoard
          project={project}
          building={buildingStems}
          onBuild={buildStems}
        />
      ) : activeStep === "render" ? (
        <RenderBoard
          project={project}
          jobs={jobs}
          onRender={renderStem}
          onRefresh={() => void refreshProject()}
        />
      ) : activeStep === "sound" ? (
        <SoundBoard
          project={project}
          onProject={setProject}
          onError={setError}
        />
      ) : (
        <MixBoard
          project={project}
          job={jobs["mix"]}
          onRender={() => void renderMix()}
          onRefresh={() => void refreshProject()}
        />
      )}
    </div>
  );
}
