import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  CaretRight,
  CheckCircle,
  DownloadSimple,
  FileAudio,
  FileText,
  ListChecks,
  MagicWand,
  MagnifyingGlass,
  MusicNotes,
  Pause,
  Play,
  Scissors,
  SlidersHorizontal,
  Sparkle,
  SpeakerHigh,
  Trash,
  UsersThree,
  WarningCircle,
  Waveform,
} from "@phosphor-icons/react";
import { api, waitForJob } from "../api";
import { MultiTrackEditor } from "../components/MultiTrackEditor";
import type {
  Character,
  EngineInfo,
  EngineName,
  Job,
  Line,
  Project,
  SfxItem,
  Stem,
  SunoLibraryClip,
  SunoPersona,
} from "../types";
import {
  autoEngine,
  engineLabel,
  formatDuration,
  initials,
  speakerStyle,
} from "../utils";

const STEPS = [
  { id: "script", label: "Script", icon: FileText },
  { id: "stems", label: "Stems", icon: Waveform },
  { id: "cut", label: "Cut", icon: Scissors },
  { id: "sound", label: "Sound", icon: MusicNotes },
  { id: "mix", label: "Mix", icon: SlidersHorizontal },
] as const;

function projectEngine(project: Project): EngineName {
  return project.default_engine ?? autoEngine(project.language);
}

/**
 * The useful line of a job error: the last non-traceback line.
 * Python tracebacks put the actionable message last, so `split("\n")[0]`
 * (the old behaviour) showed only "Traceback (most recent call last):".
 */
function actionableError(message: string): string {
  const lines = message
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    if (!/^File ".*", line \d+/.test(lines[i]) && !lines[i].startsWith("Traceback")) {
      return lines[i];
    }
  }
  return lines[0] ?? message;
}

function ErrorDialog({
  title,
  body,
  onClose,
  onRecheck,
  rechecking,
}: {
  title: string;
  body: string;
  onClose: () => void;
  onRecheck?: () => void;
  rechecking?: boolean;
}) {
  const captcha = /CAPTCHA_REQUIRED|turnstile|verify you are human/i.test(body);
  return (
    <div className="error-dialog-backdrop" role="presentation" onClick={onClose}>
      <div
        className="error-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="error-dialog-head">
          <span>
            <WarningCircle size={14} weight="fill" /> {title}
          </span>
          <button type="button" className="text-button" onClick={onClose}>
            Close
          </button>
        </div>
        {captcha ? (
          <p className="error-dialog-hint">
            Suno wants a human verification. Open <code>suno.com/create</code> in
            the browser where BetterSuno is installed, tick “Verify you are
            human”, then press the button below and retry.
          </p>
        ) : null}
        <pre className="error-dialog-body">{body}</pre>
        <div className="error-dialog-foot">
          {captcha && onRecheck ? (
            <button
              type="button"
              className="button button-primary button-sm"
              onClick={onRecheck}
              disabled={rechecking}
            >
              {rechecking ? "Checking…" : "Re-check verification"}
            </button>
          ) : null}
          <button type="button" className="button button-secondary button-sm" onClick={onClose}>
            Dismiss
          </button>
        </div>
      </div>
    </div>
  );
}

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

function SunoPersonaPicker({
  character,
  onSelect,
}: {
  character: Character;
  onSelect: (speaker: string, persona: SunoPersona | null) => void;
}) {
  const [personas, setPersonas] = useState<SunoPersona[] | null | undefined>(
    undefined,
  );
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);

  useEffect(() => {
    let live = true;
    api
      .sunoPersonas()
      .then((data) => {
        if (!live) return;
        const all = [
          ...(data.own ?? []),
          ...(data.followed ?? []),
          ...(data.loved ?? []),
        ];
        all.sort((a, b) =>
          a.persona_model === b.persona_model
            ? a.name.localeCompare(b.name)
            : a.persona_model === "voice_persona"
              ? -1
              : 1,
        );
        setPersonas(all);
      })
      .catch(() => {
        if (live) setPersonas(null);
      });
    return () => {
      live = false;
    };
  }, []);

  if (personas === undefined)
    return <span className="panel-sub">loading voices…</span>;
  // Bridge offline: hide the picker, the preset voice still renders.
  if (personas === null) return null;

  const selectedId = character.suno_persona_id ?? "";
  const selected =
    personas.find((item) => item.id === character.suno_persona_id) ?? null;

  async function preview(persona: SunoPersona) {
    if (!persona.id) return;
    setPreviewBusy(true);
    try {
      const data = await api.sunoPersonaClip(persona.id);
      const clip = (data.persona?.clip ?? {}) as {
        audio_url?: string;
        video_url?: string;
        media_urls?: Array<{ url?: string }>;
      };
      const media = Array.isArray(clip.media_urls)
        ? clip.media_urls[0]?.url
        : undefined;
      // Empty strings must fall through (?? does not). Preference: mp4
      // decodes everywhere with zero server cost; otherwise the transcoded
      // app preview (persona catalog m4a is AES-encrypted opus that Firefox
      // cannot demux); raw m4a last (Chrome may still play it).
      const mp4 = clip.video_url || undefined;
      const httpAudio =
        clip.audio_url && !clip.audio_url.includes("/forbidden")
          ? clip.audio_url
          : undefined;
      const proxied = persona.root_clip_id
        ? api.sunoPreviewUrl(persona.root_clip_id)
        : undefined;
      setPreviewUrl(mp4 || proxied || httpAudio || media || null);
    } catch {
      setPreviewUrl(null);
    } finally {
      setPreviewBusy(false);
    }
  }

  return (
    <div className="persona-picker">
      <select
        className="input"
        value={selectedId}
        onChange={(event) => {
          const next =
            personas.find((item) => item.id === event.target.value) ?? null;
          setPreviewUrl(null);
          onSelect(character.id, next);
        }}
        aria-label={`Suno voice for ${character.name}`}
      >
        <option value="">Preset voice (no persona)</option>
        {personas.map((persona) => (
          <option key={persona.id ?? persona.name} value={persona.id ?? ""}>
            {persona.name} ·{" "}
            {persona.persona_model === "style_persona" ? "style" : "voice"}
          </option>
        ))}
      </select>
      {selected?.root_clip_id ? (
        <button
          type="button"
          className="button button-ghost button-sm"
          onClick={() => void preview(selected)}
          disabled={previewBusy}
        >
          {previewBusy ? "Loading…" : "Preview voice"}
        </button>
      ) : null}
      {previewUrl ? (
        <audio
          controls
          preload="metadata"
          src={previewUrl}
          style={{ width: "100%" }}
        />
      ) : null}
    </div>
  );
}

function SunoImportRow({
  stem,
  disabled,
  onImport,
}: {
  stem: Stem;
  disabled: boolean;
  onImport: (stem: Stem, clipId: string) => void;
}) {
  const [clips, setClips] = useState<SunoLibraryClip[] | null | undefined>(
    undefined,
  );
  const [selectedId, setSelectedId] = useState("");
  // The full 50-entry list is unwieldy as a native dropdown: show the 10
  // latest, with a "show all" entry that loads the rest on demand.
  const [limit, setLimit] = useState(10);

  useEffect(() => {
    let live = true;
    setClips(undefined);
    api
      .sunoLibrary(limit)
      .then((data) => {
        if (live) setClips(data.clips ?? []);
      })
      .catch(() => {
        if (live) setClips(null);
      });
    return () => {
      live = false;
    };
  }, [limit]);

  if (clips === undefined)
    return <span className="panel-sub">loading Suno files…</span>;
  // Bridge offline: hide the picker, rendering still works.
  if (clips === null) return null;
  if (clips.length === 0)
    return <span className="panel-sub">no Suno files yet</span>;

  return (
    <div
      className="render-extract-row"
      style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 3, marginBottom: 10 }}
    >
      <select
        value={selectedId}
        onChange={(event) => {
          if (event.target.value === "__more") {
            setLimit(50);
            setSelectedId("");
            return;
          }
          setSelectedId(event.target.value);
        }}
        disabled={disabled}
        title="Pick one of your recent Suno generations to attach as a take"
        aria-label={`Import Suno file as take for ${stem.speaker}`}
        style={{ maxWidth: 280, textOverflow: "ellipsis" }}
      >
        <option value="">Import from Suno…</option>
        {clips.map((clip) => (
          <option
            key={clip.id ?? `${clip.title}-${clip.created_at}`}
            value={clip.id ?? ""}
            disabled={!clip.id}
          >
            {clip.title}
            {clip.created_at ? ` · ${clip.created_at.slice(0, 10)}` : ""}
            {clip.duration ? ` · ${formatDuration(clip.duration)}` : ""}
            {clip.status && clip.status !== "complete" ? ` · ${clip.status}` : ""}
          </option>
        ))}
        {limit < 50 ? (
          <option value="__more">Show all 50 recent files…</option>
        ) : null}
      </select>
      <button
        type="button"
        className="button button-secondary button-sm"
        onClick={() => {
          if (selectedId) onImport(stem, selectedId);
        }}
        disabled={disabled || !selectedId}
        title="Download this Suno file and attach it as the selected take"
      >
        <DownloadSimple size={14} />
        Import
      </button>
    </div>
  );
}

function SunoStemFields({
  stem,
  character,
  onSelectPersona,
  onSave,
  onSuggest,
}: {
  stem: Stem;
  character: Character;
  onSelectPersona: (speaker: string, persona: SunoPersona | null) => void;
  onSave: (stem: Stem, fields: {
    suno_title: string;
    suno_tags: string;
    suno_weirdness: number | null;
    suno_style_weight: number | null;
    suno_audio_weight: number | null;
    suno_mv: string | null;
    suno_vocal_gender: string | null;
  }) => void;
  onSuggest: (stem: Stem) => Promise<void>;
}) {
  const [title, setTitle] = useState(stem.suno_title ?? "");
  const [tags, setTags] = useState(stem.suno_tags ?? "");
  const [weirdness, setWeirdness] = useState<number | null>(stem.suno_weirdness ?? null);
  const [styleWeight, setStyleWeight] = useState<number | null>(stem.suno_style_weight ?? null);
  const [audioWeight, setAudioWeight] = useState<number | null>(stem.suno_audio_weight ?? null);
  const [mv, setMv] = useState(stem.suno_mv ?? "");
  const [vocalGender, setVocalGender] = useState(stem.suno_vocal_gender ?? "");
  const [suggesting, setSuggesting] = useState(false);
  useEffect(() => {
    setTitle(stem.suno_title ?? "");
    setTags(stem.suno_tags ?? "");
    setWeirdness(stem.suno_weirdness ?? null);
    setStyleWeight(stem.suno_style_weight ?? null);
    setAudioWeight(stem.suno_audio_weight ?? null);
    setMv(stem.suno_mv ?? "");
    setVocalGender(stem.suno_vocal_gender ?? "");
  }, [
    stem.suno_title,
    stem.suno_tags,
    stem.suno_weirdness,
    stem.suno_style_weight,
    stem.suno_audio_weight,
    stem.suno_mv,
    stem.suno_vocal_gender,
  ]);
  const dirty =
    title.trim() !== (stem.suno_title ?? "") ||
    tags.trim() !== (stem.suno_tags ?? "") ||
    weirdness !== (stem.suno_weirdness ?? null) ||
    styleWeight !== (stem.suno_style_weight ?? null) ||
    audioWeight !== (stem.suno_audio_weight ?? null) ||
    mv !== (stem.suno_mv ?? "") ||
    vocalGender !== (stem.suno_vocal_gender ?? "");
  // Auto-save: every edit persists after a short quiet period, no button.
  useEffect(() => {
    if (!dirty) return;
    const timer = setTimeout(() => {
      onSave(stem, {
        suno_title: title.trim(),
        suno_tags: tags.trim(),
        suno_weirdness: weirdness,
        suno_style_weight: styleWeight,
        suno_audio_weight: audioWeight,
        suno_mv: mv || null,
        suno_vocal_gender: vocalGender || null,
      });
    }, 700);
    return () => clearTimeout(timer);
  }, [
    dirty,
    onSave,
    stem,
    title,
    tags,
    weirdness,
    styleWeight,
    audioWeight,
    mv,
    vocalGender,
  ]);
  async function runSuggest() {
    setSuggesting(true);
    try {
      await onSuggest(stem);
    } finally {
      setSuggesting(false);
    }
  }
  return (
    <div className="suno-stem">
      <div className="field">
        <span className="label">
          Suno voice <span>persona for {character.name}</span>
        </span>
        <SunoPersonaPicker character={character} onSelect={onSelectPersona} />
      </div>
      <div className="field">
        <label className="label" htmlFor={`suno-title-${stem.id}`}>
          Song title <span>sent to Suno with this stem</span>
        </label>
        <input
          id={`suno-title-${stem.id}`}
          className="input"
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          placeholder={`dialogue ${stem.id}`}
          spellCheck={false}
        />
      </div>
      <div className="field">
        <label className="label" htmlFor={`suno-tags-${stem.id}`}>
          Style tags <span>music style prompt for this stem</span>
        </label>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <input
            id={`suno-tags-${stem.id}`}
            className="input"
            value={tags}
            onChange={(event) => setTags(event.target.value)}
            placeholder="audio drama radio play"
            spellCheck={false}
            style={{ flex: 1 }}
          />
          <button
            type="button"
            className="button button-secondary button-sm"
            onClick={() => void runSuggest()}
            disabled={suggesting}
            title="Let the local LLM propose style tags (title stays untouched)"
            aria-label="Suggest style tags with LLM"
          >
            {suggesting ? <span className="spinner" /> : <MagicWand size={14} weight="fill" />}
          </button>
        </div>
      </div>
      <div className="field">
        <label className="label" htmlFor={`suno-weird-${stem.id}`}>
          Weirdness{" "}
          <span>{weirdness === null ? "auto" : weirdness}</span>
        </label>
        <div className="slider-row">
          <input
            id={`suno-weird-${stem.id}`}
            type="range"
            min={0}
            max={100}
            step={1}
            value={weirdness ?? 50}
            onChange={(event) => setWeirdness(Number(event.target.value))}
          />
          {weirdness !== null ? (
            <button
              type="button"
              className="button button-ghost button-sm"
              onClick={() => setWeirdness(null)}
              title="Back to Suno default"
            >
              auto
            </button>
          ) : null}
        </div>
      </div>
      <div className="field">
        <label className="label" htmlFor={`suno-style-${stem.id}`}>
          Style influence{" "}
          <span>{styleWeight === null ? "auto" : styleWeight}</span>
        </label>
        <div className="slider-row">
          <input
            id={`suno-style-${stem.id}`}
            type="range"
            min={0}
            max={100}
            step={1}
            value={styleWeight ?? 20}
            onChange={(event) => setStyleWeight(Number(event.target.value))}
          />
          {styleWeight !== null ? (
            <button
              type="button"
              className="button button-ghost button-sm"
              onClick={() => setStyleWeight(null)}
              title="Back to engine default (20)"
            >
              auto
            </button>
          ) : null}
        </div>
      </div>
      <div className="field">
        <label className="label" htmlFor={`suno-audio-${stem.id}`}>
          Audio influence{" "}
          <span>{audioWeight === null ? "auto" : audioWeight}</span>
        </label>
        <div className="slider-row">
          <input
            id={`suno-audio-${stem.id}`}
            type="range"
            min={0}
            max={100}
            step={1}
            value={audioWeight ?? 50}
            onChange={(event) => setAudioWeight(Number(event.target.value))}
          />
          {audioWeight !== null ? (
            <button
              type="button"
              className="button button-ghost button-sm"
              onClick={() => setAudioWeight(null)}
              title="Back to Suno default"
            >
              auto
            </button>
          ) : null}
        </div>
      </div>
      <div className="field">
        <label className="label" htmlFor={`suno-mv-${stem.id}`}>
          Model <span>per-stem override</span>
        </label>
        <select
          id={`suno-mv-${stem.id}`}
          className="input"
          value={mv}
          onChange={(event) => setMv(event.target.value)}
        >
          <option value="">Character default</option>
          <option value="chirp-hawk">v6 · chirp-hawk</option>
          <option value="chirp-hawk-wild">v6 wild · chirp-hawk-wild</option>
          <option value="chirp-goose">v6 mini · chirp-goose</option>
          <option value="chirp-fenix">v5.5 · chirp-fenix</option>
          <option value="chirp-crow">v5 · chirp-crow</option>
          <option value="chirp-bluejay">v4.5+ · chirp-bluejay</option>
          <option value="chirp-auk">v4.5 · chirp-auk</option>
          <option value="chirp-v4">v4 · chirp-v4</option>
          <option value="chirp-v3-5">v3.5 · chirp-v3-5</option>
        </select>
      </div>
      <div className="field">
        <label className="label" htmlFor={`suno-gender-${stem.id}`}>
          Vocal gender <span>preset voices only</span>
        </label>
        <select
          id={`suno-gender-${stem.id}`}
          className="input"
          value={vocalGender}
          onChange={(event) => setVocalGender(event.target.value)}
        >
          <option value="">Auto</option>
          <option value="f">Female</option>
          <option value="m">Male</option>
        </select>
      </div>
    </div>
  );
}

function CastPanel({
  project,
  jobs,
  onDesign,
  onUpload,
}: {
  project: Project;
  jobs: Record<string, Job>;
  onDesign: (speaker: string, opts?: { name?: string; seed?: number }) => void;
  onUpload: (speaker: string, file: File, name?: string) => void;
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
            const engine = projectEngine(project);
            const isSuno = engine === "suno";
            const isChatterbox = engine === "chatterbox";
            const isBreeze = engine === "breeze" || engine === "breeze-de";
            return (
              <div className="cast-card" key={character.id}>
                <span className="avatar" style={speakerStyle(character.id)}>
                  {initials(character.name)}
                </span>
                <div className="cast-body">
                  <p className="cast-name">{character.name}</p>
                  <p className="cast-lines">
                    {character.count} line{character.count === 1 ? "" : "s"} ·{" "}
                    {isSuno
                      ? "Suno voice"
                      : isChatterbox
                        ? "Chatterbox clone"
                        : `${engineLabel(engine)} reference`}
                  </p>
                  {character.description ? (
                    <p className="cast-desc">{character.description}</p>
                  ) : null}
                  <div className="cast-voice">
                    {character.ref_file ? (
                      <span className="badge">voice sample ready</span>
                    ) : null}
                    {!character.ref_file && isChatterbox ? (
                      <label className="button button-secondary button-sm cast-upload">
                        <Waveform size={12} />
                        Upload voice
                        <input
                          type="file"
                          accept="audio/*"
                          onChange={(event) => {
                            const file = event.target.files?.[0];
                            if (file) onUpload(character.id, file);
                          }}
                        />
                      </label>
                    ) : null}
                    {character.ref_file && isChatterbox ? (
                      <label className="button button-ghost button-sm cast-upload">
                        <Waveform size={12} />
                        Upload voice
                        <input
                          type="file"
                          accept="audio/*"
                          onChange={(event) => {
                            const file = event.target.files?.[0];
                            if (file)
                              onUpload(
                                character.id,
                                file,
                                `Voice ${(character.references?.length ?? 0) + 1}`,
                              );
                          }}
                        />
                      </label>
                    ) : null}
                    {!character.ref_file && isBreeze ? (
                      <button
                        type="button"
                        className="button button-secondary button-sm"
                        onClick={() => onDesign(character.id)}
                        disabled={busy}
                      >
                        {busy ? <span className="spinner" /> : <Waveform size={12} />}
                        {busy ? "Designing…" : "Design voice"}
                      </button>
                    ) : null}
                    {character.ref_file && isBreeze ? (
                      <button
                        type="button"
                        className="button button-ghost button-sm"
                        onClick={() =>
                          onDesign(character.id, {
                            name: `Voice ${(character.references?.length ?? 0) + 1}`,
                            seed: 42 + (character.references?.length ?? 0),
                          })
                        }
                        disabled={busy}
                        title="Design an additional named voice for per-stem selection in Render"
                      >
                        Add voice
                      </button>
                    ) : null}
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

function RenderBoard({
  project,
  jobs,
  batch,
  building,
  sunoPlan,
  onBuild,
  onRender,
  onRenderAll,
  onShowError,
  onSelectTake,
  onRefVoiceChange,
  onSelectPersona,
  onSaveSunoFields,
  onSuggestSunoStyle,
  onExtractStem,
  onImportSuno,
  onRefresh,
}: {
  project: Project;
  jobs: Record<string, Job>;
  batch: {
    state: "idle" | "running" | "paused_captcha" | "done";
    current: Stem | null;
    done: Stem[];
    failed: { stem: Stem; message: string }[];
    skipped: Stem[];
    total: number;
  };
  building: boolean;
  sunoPlan: "free" | "pro";
  onBuild: () => void;
  onRender: (stem: Stem) => void;
  onRenderAll: (stems: Stem[], includeRendered: boolean) => void;
  onShowError: (title: string, body: string) => void;
  onSelectTake: (stem: Stem, takeId: string) => void;
  onRefVoiceChange: (stem: Stem, refVoiceId: string) => void;
  onSelectPersona: (speaker: string, persona: SunoPersona | null) => void;
  onSaveSunoFields: (
    stem: Stem,
    fields: {
      suno_title: string;
      suno_tags: string;
      suno_weirdness: number | null;
      suno_style_weight: number | null;
      suno_audio_weight: number | null;
      suno_mv: string | null;
      suno_vocal_gender: string | null;
    },
  ) => void;
  onSuggestSunoStyle: (stem: Stem) => Promise<void>;
  onExtractStem: (stem: Stem, method: "demucs" | "suno") => Promise<void>;
  onImportSuno: (stem: Stem, clipId: string) => void;
  onRefresh: () => void;
}) {
  const byScene = project.script.scenes
    .map((scene) => ({
      scene,
      stems: project.stems.filter((stem) => stem.scene === scene.id),
    }))
    .filter((entry) => entry.stems.length > 0);

  const [extractMethods, setExtractMethods] = useState<Record<string, "demucs" | "suno">>({});
  const defaultEngine = projectEngine(project);
  const voiceReady = (stem: Stem) => {
    const engine = defaultEngine;
    if (engine === "fake" || engine === "chatterbox" || engine === "suno") {
      return true;
    }
    return Boolean(project.script.characters[stem.speaker]?.ref_file);
  };
  const ready = project.stems.filter((stem) => voiceReady(stem)).length;

  return (
    <div>
      <section className="panel stems-toolbar">
        <div>
          <h2 className="panel-title">
            <Waveform size={15} weight="fill" />
            Render queue
          </h2>
          <p className="panel-sub">
            {ready}/{project.stems.length} stems ready ·{" "}
            {engineLabel(defaultEngine)} default{" "}
            {defaultEngine === "suno" ? "via BetterSuno" : "on GPU"} · Whisper on CPU
          </p>
        </div>
        <div className="panel-actions">
          <button
            type="button"
            className="button button-ghost button-sm"
            onClick={onBuild}
            disabled={building}
          >
            {building ? <span className="spinner" /> : <MagicWand size={14} weight="fill" />}
            {building ? "Building…" : project.stems.length ? "Rebuild stems" : "Build stems"}
          </button>
          {batch.state === "running" ? (
            <span className="panel-sub">
              batch {Math.min(batch.done.length + 1, batch.total)}/{batch.total}
              {batch.current ? ` · ${batch.current.speaker}` : ""}
            </span>
          ) : null}
          {batch.state === "paused_captcha" ? (
            <span className="render-warn">paused · verification needed</span>
          ) : null}
          <button
            type="button"
            className="button button-primary button-sm"
            disabled={batch.state === "running" || project.stems.length === 0}
            onClick={() => onRenderAll(project.stems, false)}
            title="Render every stem that has no takes yet, one after another"
          >
            {batch.state === "running" ? "Rendering…" : "Render all"}
          </button>
          <button type="button" className="button button-ghost button-sm" onClick={onRefresh}>
            Refresh
          </button>
        </div>
      </section>

      {byScene.length === 0 ? (
        <div className="empty-list">
          Build stems first, then give each character a voice.
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
                  const selectedEngine = defaultEngine;
                  const readyForStem = voiceReady(stem);
                  const character = project.script.characters[stem.speaker];
                  const refVoices = character?.references ?? [];
                  const showRefVoice =
                    (selectedEngine === "breeze" ||
                      selectedEngine === "breeze-de" ||
                      selectedEngine === "chatterbox") &&
                    Boolean(character) &&
                    (Boolean(character?.ref_file) || refVoices.length > 0);
                  const job = jobs[stem.id];
                  const realignJob = jobs[`${stem.id}:realign`];
                  const extractJob = jobs[`${stem.id}:extract`];
                  const importJob = jobs[`${stem.id}:import`];
                  const busy =
                    job?.status === "queued" ||
                    job?.status === "running" ||
                    realignJob?.status === "queued" ||
                    realignJob?.status === "running" ||
                    extractJob?.status === "queued" ||
                    extractJob?.status === "running" ||
                    importJob?.status === "queued" ||
                    importJob?.status === "running";
                  const failedJob =
                    job?.status === "failed"
                      ? job
                      : importJob?.status === "failed"
                        ? importJob
                        : null;
                  const alignment = project.alignments[stem.id];
                  const take = stem.takes.find((item) => item.selected) ?? stem.takes[0];
                  const isolatedTake =
                    (take && take.vocal_isolated ? take : null) ??
                    stem.takes.find((item) => item.vocal_isolated) ??
                    null;
                  const originalTake = isolatedTake
                    ? stem.takes.find(
                        (item) =>
                          !item.vocal_isolated &&
                          (isolatedTake.clip_id
                            ? item.clip_id === isolatedTake.clip_id
                            : Math.abs(item.duration_s - isolatedTake.duration_s) < 0.1),
                      ) ??
                      stem.takes.find((item) => !item.vocal_isolated) ??
                      null
                    : null;
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

                      <p className="stem-text">{stem.text.replace(/([.?!])\s+/g, "$1\n")}</p>
                      <div className="engine-row">
                        <span className="badge">{engineLabel(selectedEngine)}</span>
                        {showRefVoice ? (
                          <label className="cue-field">
                            <span>Reference</span>
                            <select
                              value={stem.ref_voice_id ?? ""}
                              onChange={(event) =>
                                onRefVoiceChange(stem, event.target.value)
                              }
                              disabled={busy}
                              title="Which of the speaker's designed voices to render with"
                            >
                              <option value="">Default voice</option>
                              {refVoices.map((ref) => (
                                <option key={ref.id} value={ref.id}>
                                  {ref.name}
                                </option>
                              ))}
                            </select>
                          </label>
                        ) : null}
                      </div>

                      <div className="render-meta">
                        <span>{stem.line_ids.length} lines</span>
                        <span>scene {stem.scene}</span>
                        {take ? <span>{formatDuration(take.duration_s)} audio</span> : null}
                        {take?.f0_hz ? <span>F0 {Math.round(take.f0_hz)} Hz</span> : null}
                        {take?.mfcc_sim ? <span>MFCC {take.mfcc_sim.toFixed(3)}</span> : null}
                      </div>

                      {selectedEngine === "suno" &&
                      project.script.characters[stem.speaker] ? (
                        <SunoStemFields
                          stem={stem}
                          character={project.script.characters[stem.speaker]}
                          onSelectPersona={onSelectPersona}
                          onSave={onSaveSunoFields}
                          onSuggest={onSuggestSunoStyle}
                        />
                      ) : null}

                      {selectedEngine === "suno" ? (
                        <SunoImportRow
                          stem={stem}
                          disabled={busy}
                          onImport={onImportSuno}
                        />
                      ) : null}

                      {stem.takes.length > 1 ? (
                        <div className="take-list">
                          {stem.takes.map((take, index) => (
                            <button
                              type="button"
                              key={take.id}
                              className={`take-pill${take.selected ? " active" : ""}`}
                              onClick={() => onSelectTake(stem, take.id)}
                              disabled={busy}
                            >
                              T{index + 1} · {take.engine}
                              {take.vocal_isolated ? " vocal" : ""} ·{" "}
                              {formatDuration(take.duration_s)}
                              {take.clip_id ? ` · clip ${take.clip_id.slice(-4)}` : ""}
                              {take.f0_hz ? ` · ${Math.round(take.f0_hz)} Hz` : ""}
                            </button>
                          ))}
                        </div>
                      ) : null}

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


                      {failedJob ? (
                        <div className="render-error-row">
                          <p className="render-error">
                            {actionableError(failedJob.error ?? "")}
                          </p>
                          <button
                            type="button"
                            className="text-button"
                            onClick={() => onShowError("Render failed", failedJob.error ?? "")}
                          >
                            Details
                          </button>
                        </div>
                      ) : null}

                      {selectedEngine === "suno" && take ? (
                        <div className="render-extract-row" style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 5, marginBottom: 5 }}>
                          {sunoPlan === "pro" ? (
                            <select
                              value={extractMethods[stem.id] ?? "demucs"}
                              onChange={(event) =>
                                setExtractMethods((prev) => ({
                                  ...prev,
                                  [stem.id]: event.target.value as "demucs" | "suno",
                                }))
                              }
                              disabled={busy}
                              title="Stem extraction method"
                            >
                              <option value="demucs">Demucs (local)</option>
                              <option value="suno">Suno stems (paid)</option>
                            </select>
                          ) : null}
                          <button
                            type="button"
                            className="button button-secondary button-sm"
                            onClick={() =>
                              void onExtractStem(
                                stem,
                                sunoPlan === "pro"
                                  ? (extractMethods[stem.id] ?? "demucs")
                                  : "demucs",
                              )
                            }
                            disabled={busy}
                            title="Extract vocals and instrumental from this take"
                          >
                            {extractJob?.status === "queued" || extractJob?.status === "running" ? (
                              <span className="spinner" />
                            ) : (
                              <Scissors size={14} />
                            )}
                            {extractJob?.status === "queued" || extractJob?.status === "running"
                              ? "Extracting…"
                              : "Extract Stem"}
                          </button>
                        </div>
                      ) : null}

                      {isolatedTake && originalTake ? (
                        <div
                          className="extract-preview"
                          style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 6 }}
                        >
                          <div>
                            <span style={{ fontSize: 12, color: "var(--muted)" }}>
                              Original (before extraction)
                            </span>
                            <audio
                              controls
                              preload="metadata"
                              src={api.fileUrl(project.id, originalTake.file)}
                              style={{ width: "100%" }}
                            />
                          </div>
                          <div>
                            <span style={{ fontSize: 12, color: "var(--muted)" }}>
                              Extracted voice
                            </span>
                            <audio
                              controls
                              preload="metadata"
                              src={api.fileUrl(project.id, isolatedTake.file)}
                              style={{ width: "100%" }}
                            />
                          </div>
                        </div>
                      ) : take ? (
                        <audio
                          controls
                          preload="metadata"
                          src={api.fileUrl(project.id, take.file)}
                          style={{ width: "100%", marginTop: 8 }}
                        />
                      ) : null}

                      <div className="render-actions">
                        {readyForStem ? (
                          <span className="badge">
                            {selectedEngine === "suno"
                              ? "preset voice"
                              : selectedEngine === "chatterbox"
                                ? "optional sample"
                                : "ref ready"}
                          </span>
                        ) : (
                          <span className="render-missing">no reference voice</span>
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
  jobs,
  onProject,
  onTrackJob,
  onError,
}: {
  project: Project;
  jobs: Record<string, Job>;
  onProject: (project: Project) => void;
  onTrackJob: (key: string, job: Job) => void;
  onError: (message: string) => void;
}) {
  const lines = project.script.scenes.flatMap((scene) => scene.lines);
  const [query, setQuery] = useState("");
  const [sunoStatus, setSunoStatus] = useState<{
    ok: boolean;
    extensionConnected?: boolean;
    error?: string;
  } | null>(null);
  const [sunoPrompt, setSunoPrompt] = useState("");
  const [sunoTitle, setSunoTitle] = useState("");
  const [sunoTags, setSunoTags] = useState("cinematic, slow, instrumental");
  const [sunoDuration, setSunoDuration] = useState(90);
  const [sunoKind, setSunoKind] = useState<"instrumental" | "song">("instrumental");
  const [items, setItems] = useState<SfxItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [playing, setPlaying] = useState<string | null>(null);
  const [anchorLineId, setAnchorLineId] = useState(lines[0]?.id ?? "");
  const [kind, setKind] = useState<"one_shot" | "bed" | "music">("one_shot");
  const [gain, setGain] = useState(-12);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    api.sunoStatus().then(setSunoStatus).catch(() => setSunoStatus({ ok: false }));
  }, []);

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

  async function generateSuno() {
    if (!sunoPrompt.trim()) {
      onError("Describe the music you want first.");
      return;
    }
    try {
      const job = await api.generateSuno(project.id, {
        kind: sunoKind,
        prompt: sunoPrompt,
        title: sunoTitle || project.title,
        tags: sunoTags,
        duration: sunoDuration,
      });
      onTrackJob("suno", job);
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Could not start Suno");
    }
  }

  async function addSunoCue(track: Project["suno"][number]) {
    if (!anchorLineId) {
      onError("Choose a line to anchor the music to.");
      return;
    }
    try {
      const updated = await api.addCue(project.id, {
        kind: "music",
        sfx_id: track.sfx_id,
        name: track.title,
        anchor_line_id: anchorLineId,
        gain_db: -18,
        loop: true,
        duck: true,
      });
      onProject(updated);
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Could not add Suno cue");
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

  const sunoJob = jobs["suno"];
  const sunoBusy =
    sunoJob?.status === "queued" || sunoJob?.status === "running";
  const sunoConnected = Boolean(sunoStatus?.ok && sunoStatus.extensionConnected);

  return (
    <>
      <section className="panel suno-panel">
        <div className="panel-head">
          <div>
            <h2 className="panel-title">
              <MusicNotes size={15} weight="fill" />
              Suno music
            </h2>
            <p className="panel-sub">
              Generate a theme, bed or song through BetterSuno ·{" "}
              {sunoConnected
                ? "extension connected"
                : sunoStatus?.ok
                  ? "extension not connected"
                  : "bridge offline"}
            </p>
          </div>
          <div className="panel-actions">
            <span className={`badge ${sunoConnected ? "badge-accent" : ""}`}>
              {sunoConnected ? "Suno ready" : "Suno offline"}
            </span>
            <button
              type="button"
              className="button button-ghost button-sm"
              onClick={() =>
                api.sunoStatus().then(setSunoStatus).catch(() => setSunoStatus({ ok: false }))
              }
            >
              Check
            </button>
          </div>
        </div>

        <div className="suno-body">
          <div className="suno-form">
            <label className="cue-field">
              <span>Music prompt</span>
              <textarea
                value={sunoPrompt}
                onChange={(event) => setSunoPrompt(event.target.value)}
                placeholder="Slow, rain-soaked noir jazz; muted trumpet, brushed drums, no vocals…"
              />
            </label>
            <div className="suno-inline">
              <label className="cue-field">
                <span>Title</span>
                <input
                  value={sunoTitle}
                  onChange={(event) => setSunoTitle(event.target.value)}
                  placeholder={project.title}
                />
              </label>
              <label className="cue-field">
                <span>Duration</span>
                <input
                  type="number"
                  min={10}
                  max={360}
                  step={5}
                  value={sunoDuration}
                  onChange={(event) => setSunoDuration(Number(event.target.value))}
                />
              </label>
              <label className="cue-field">
                <span>Kind</span>
                <select
                  value={sunoKind}
                  onChange={(event) =>
                    setSunoKind(event.target.value as "instrumental" | "song")
                  }
                >
                  <option value="instrumental">instrumental</option>
                  <option value="song">song</option>
                </select>
              </label>
            </div>
            <label className="cue-field">
              <span>Style tags</span>
              <input
                value={sunoTags}
                onChange={(event) => setSunoTags(event.target.value)}
              />
            </label>
            <div className="suno-actions">
              <button
                type="button"
                className="button button-primary"
                onClick={() => void generateSuno()}
                disabled={sunoBusy || !sunoConnected}
              >
                {sunoBusy ? <span className="spinner" /> : <MusicNotes size={15} weight="fill" />}
                {sunoBusy ? "Generating…" : "Generate with Suno"}
              </button>
              {!sunoConnected ? (
                <span className="render-missing">
                  start `node suno-bridge/server.mjs` + open suno.com
                </span>
              ) : null}
            </div>
          </div>

          <div className="suno-tracks">
            {sunoBusy ? (
              <div className="render-progress">
                <div className="render-progress-head">
                  <span>{sunoJob?.message || sunoJob?.status}</span>
                  <strong>{Math.round((sunoJob?.progress ?? 0) * 100)}%</strong>
                </div>
                <div className="coverage-bar">
                  <div
                    className="coverage-fill"
                    style={{ width: `${(sunoJob?.progress ?? 0) * 100}%` }}
                  />
                </div>
              </div>
            ) : null}
            {sunoJob?.status === "failed" ? (
              <p className="render-error">{sunoJob.error?.split("\n")[0]}</p>
            ) : null}
            {project.suno.length === 0 ? (
              <div className="empty-list">
                No Suno tracks yet. Generate a cue and add it to the scene.
              </div>
            ) : (
              project.suno.map((track) => (
                <div className="suno-track" key={track.clip_id}>
                  <div className="suno-track-head">
                    <strong>{track.title}</strong>
                    <span>
                      {formatDuration(track.duration_s ?? 0)} · {track.clip_id.slice(-6)}
                    </span>
                  </div>
                  <audio controls preload="metadata" src={api.sfxAudioUrl(track.sfx_id)} />
                  <div className="suno-track-actions">
                    <button
                      type="button"
                      className="button button-secondary button-sm"
                      onClick={() => void addSunoCue(track)}
                    >
                      Add as music cue
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </section>

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
    </>
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
            {project.mix_stale && mix && !busy ? (
              <span className="badge badge-danger" title="Cuts changed since the last mix">
                mix stale
              </span>
            ) : null}
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
  const [llmOutput, setLlmOutput] = useState("");
  const [buildingStems, setBuildingStems] = useState(false);
  const [activeStep, setActiveStep] = useState<
    "script" | "stems" | "cut" | "sound" | "mix"
  >("script");
  const [jobs, setJobs] = useState<Record<string, Job>>({});
  const [engines, setEngines] = useState<EngineInfo[]>([]);
  const [sunoPlan, setSunoPlan] = useState<"free" | "pro">("free");
  const [error, setError] = useState("");
  const [batch, setBatch] = useState<{
    state: "idle" | "running" | "paused_captcha" | "done";
    current: Stem | null;
    done: Stem[];
    failed: { stem: Stem; message: string }[];
    skipped: Stem[];
    total: number;
  }>({ state: "idle", current: null, done: [], failed: [], skipped: [], total: 0 });
  const [errorDialog, setErrorDialog] = useState<{ title: string; body: string } | null>(null);
  const [recheckingCaptcha, setRecheckingCaptcha] = useState(false);

  useEffect(() => {
    api.engines().then(setEngines).catch(() => setEngines([]));
    api
      .getConfig()
      .then((cfg) => {
        if (cfg.suno) setSunoPlan(cfg.suno.plan);
      })
      .catch(() => setSunoPlan("free"));
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
    setLlmOutput("");
    try {
      const saved = dirty
        ? await api.updateProject(project.id, { raw_script: rawScript })
        : project;
      const updated = await api.parseProjectStream(saved.id, (chunk) => {
        setLlmOutput((prev) => prev + chunk);
      });
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

  const changeProjectEngine = useCallback(
    async (value: string) => {
      if (!project) return;
      try {
        const updated = await api.updateProject(project.id, {
          default_engine: value === "auto" ? "auto" : (value as EngineName),
        });
        setProject(updated);
        setError("");
      } catch (reason) {
        setError(
          reason instanceof Error ? reason.message : "Could not change engine",
        );
      }
    },
    [project],
  );

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

  const extractStem = useCallback(
    async (stem: Stem, method: "demucs" | "suno" = "demucs") => {
      if (!project) return;
      setError("");
      const take =
        stem.takes.find((item) => item.selected) ?? stem.takes[0];
      if (!take) {
        setError("Render the stem first");
        return;
      }
      try {
        const job = await api.extractStem(project.id, stem.id, {
          take_id: take.id,
          method,
          align: true,
        });
        trackJob(`${stem.id}:extract`, job);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Could not start extraction");
      }
    },
    [project, trackJob],
  );

  const importSunoTake = useCallback(
    async (stem: Stem, clipId: string) => {
      if (!project) return;
      setError("");
      try {
        const job = await api.importSunoTake(project.id, stem.id, clipId);
        trackJob(`${stem.id}:import`, job);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Could not import Suno file");
      }
    },
    [project, trackJob],
  );

  const renderAll = useCallback(
    async (stems: Stem[], includeRendered: boolean) => {
      if (!project) return;
      const queue = stems.filter((stem) => includeRendered || stem.takes.length === 0);
      if (queue.length === 0) return;
      setBatch({ state: "running", done: [], skipped: [], failed: [], current: null, total: queue.length });
      for (const stem of queue) {
        setBatch((prev) => ({ ...prev, current: stem }));
        let job: Job;
        try {
          job = await api.renderStem(project.id, stem.id, {
            candidates: 2,
            cfg_scale: stem.cfg_scale,
            seed: stem.seed,
          });
        } catch (reason) {
          const message = reason instanceof Error ? reason.message : "render failed";
          setBatch((prev) => ({ ...prev, failed: [...prev.failed, { stem, message }] }));
          continue;
        }
        trackJob(stem.id, job);
        const finished = await waitForJob(job.id).catch(() => job);
        if (finished.status === "succeeded") {
          setBatch((prev) => ({ ...prev, done: [...prev.done, stem] }));
          await refreshProject();
          continue;
        }
        const message = finished.error ?? finished.status;
        if (message.includes("CAPTCHA_REQUIRED") || /turnstile|verify you are human/i.test(message)) {
          setBatch((prev) => ({
            ...prev,
            state: "paused_captcha",
            failed: [...prev.failed, { stem, message }],
          }));
          setErrorDialog({ title: `Verification needed · ${stem.speaker}`, body: message });
          return;
        }
        setBatch((prev) => ({ ...prev, failed: [...prev.failed, { stem, message }] }));
      }
      setBatch((prev) => ({ ...prev, state: "done", current: null }));
      await refreshProject();
    },
    [project, trackJob, refreshProject],
  );

  const changeRefVoice = useCallback(
    async (stem: Stem, refVoiceId: string) => {
      if (!project) return;
      try {
        const updated = await api.updateStem(project.id, stem.id, {
          ref_voice_id: refVoiceId,
        });
        setProject(updated);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Could not change reference voice");
      }
    },
    [project],
  );

  const saveSunoFields = useCallback(
    async (
      stem: Stem,
      fields: {
        suno_title: string;
        suno_tags: string;
        suno_weirdness: number | null;
        suno_style_weight: number | null;
        suno_audio_weight: number | null;
        suno_mv: string | null;
        suno_vocal_gender: string | null;
      },
    ) => {
      if (!project) return;
      setError("");
      try {
        const updated = await api.updateStem(project.id, stem.id, fields);
        setProject(updated);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Could not save Suno settings");
      }
    },
    [project],
  );

  const suggestSunoStyle = useCallback(
    async (stem: Stem) => {
      if (!project) return;
      setError("");
      try {
        const updated = await api.suggestSunoStyle(project.id, stem.id, {
          method: "llm",
          overwrite: true,
          only: ["tags"],
        });
        setProject(updated);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Could not suggest style tags");
      }
    },
    [project],
  );

  const selectTake = useCallback(
    async (stem: Stem, takeId: string) => {
      if (!project) return;
      try {
        const result = await api.selectTake(project.id, stem.id, takeId);
        setProject(result.project);
        // Per-clip take choices or manual cuts mean the server keeps the cut
        // sheet and skips the realign job.
        if (result.job) trackJob(`${stem.id}:realign`, result.job);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Could not select take");
      }
    },
    [project, trackJob],
  );

  const saveCuts = useCallback(
    async (
      stem: Stem,
      cuts: {
        line_id: string;
        take_id?: string | null;
        start_s: number;
        end_s: number;
      }[],
      rebuildMix = true,
    ) => {
      if (!project) return;
        try {
        const result = await api.updateAlignment(project.id, stem.id, cuts, rebuildMix);
        setProject(result.project);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Could not save cuts");
      }
    },
    [project],
  );

  const saveAllCuts = useCallback(
    async (
      stems: Record<
        string,
        { line_id: string; take_id?: string | null; start_s: number; end_s: number }[]
      >,
    ) => {
      if (!project) return;
      try {
        const result = await api.updateAlignmentsBatch(project.id, stems, true);
        setProject(result.project);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Could not save cuts");
      }
    },
    [project],
  );

  const designVoice = useCallback(
    async (speaker: string, opts?: { name?: string; seed?: number }) => {
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
          seed: opts?.seed ?? 42,
          name: opts?.name,
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

  const uploadVoice = useCallback(
    async (speaker: string, file: File, name?: string) => {
      if (!project) return;
      try {
        const updated = await api.uploadVoiceSample(project.id, speaker, file, name);
        setProject(updated);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Could not upload voice sample");
      }
    },
    [project],
  );

  const selectPersona = useCallback(
    async (speaker: string, persona: SunoPersona | null) => {
      if (!project) return;
      setError("");
      try {
        const updated = await api.updateCharacter(project.id, speaker, {
          suno_persona_id: persona?.id ?? "",
          suno_persona_model: persona?.persona_model ?? "",
        });
        setProject(updated);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Could not set Suno voice");
      }
    },
    [project],
  );

  const recheckCaptcha = useCallback(async () => {
    setRecheckingCaptcha(true);
    try {
      await api.requestSunoCaptcha();
      setErrorDialog(null);
      const paused = batch.state === "paused_captcha";
      setBatch((prev) => ({ ...prev, state: "idle" }));
      if (paused && project) {
        // Resume: rendered stems have takes and are skipped; the failed one
        // has none and is retried first.
        void renderAll(project.stems, false);
      }
    } catch (reason) {
      setErrorDialog({
        title: "Verification still blocked",
        body: reason instanceof Error ? reason.message : "Captcha request failed",
      });
    } finally {
      setRecheckingCaptcha(false);
    }
  }, [batch.state, project, renderAll]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (activeStep !== "script") return;
      if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
        event.preventDefault();
        void saveAndParse();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [saveAndParse, activeStep]);

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
            <span
              className={`badge ${project.language === "de" ? "badge-violet" : "badge-accent"}`}
            >
              {project.language === "de" ? "Deutsch" : "English"}
            </span>
            <label
              className="engine-picker"
              title="Project default engine"
              style={{ display: "inline-flex" }}
            >
              <select
                className="input"
                value={project.default_engine ?? "auto"}
                style={{ width: "auto", height: 32, fontSize: 13 }}
                onChange={(event) => void changeProjectEngine(event.target.value)}
                aria-label="Project default engine"
              >
                <option value="auto">
                  Auto · {engineLabel(autoEngine(project.language))}
                </option>
                {engines
                  .filter(
                    (engine) =>
                      engine.name !== "fake" &&
                      (engine.language_agnostic ||
                        engine.languages.includes(project.language)),
                  )
                  .map((engine) => (
                    <option key={engine.name} value={engine.name}>
                      {engineLabel(engine.name)}
                    </option>
                  ))}
              </select>
            </label>
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
            (step.id === "stems" && lineCount > 0) ||
            (step.id === "cut" && project.stems.length > 0) ||
            ((step.id === "sound" || step.id === "mix") &&
              project.stems.length > 0);
          const active = step.id === activeStep;
          return (
            <button
              key={step.id}
              type="button"
              className={`step${active ? " active" : ""}${available ? "" : " disabled"}`}
              disabled={!available || (step.id === "stems" && buildingStems)}
              onClick={() => {
                if (step.id === "script") {
                  setActiveStep("script");
                } else if (step.id === "stems") {
                  if (project.stems.length === 0) {
                    void buildStems();
                  } else {
                    setActiveStep("stems");
                  }
                } else if (step.id === "cut") {
                  setActiveStep("cut");
                } else if (step.id === "sound" || step.id === "mix") {
                  setActiveStep(step.id);
                }
              }}
              title={
                available
                  ? step.id === "stems" && project.stems.length === 0
                    ? "Build stems from the parsed script"
                    : undefined
                  : step.id === "stems"
                    ? "Parse a script with lines first"
                    : step.id === "cut"
                      ? "Render stems first"
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

      {/* <div className="stats-row">
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
      </div> */}

      {activeStep === "script" ? (
        <div className="workspace">
        <div
          className="script-toolbar"
          style={{
            gridColumn: "1 / -1",
            display: "flex",
            justifyContent: "flex-end",
            gap: 8,
          }}
        >
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

        {(llmParsing || llmOutput) ? (
          <section className="panel">
            <div className="panel-head">
              <div>
                <h2 className="panel-title">
                  <Sparkle size={15} weight="fill" />
                  LLM live output
                </h2>
                <p className="panel-sub">streaming tokens from the local model</p>
              </div>
            </div>
            <textarea
              className="raw-editor"
              value={llmOutput}
              readOnly
              spellCheck={false}
              aria-label="LLM live output"
              style={{ minHeight: 160 }}
            />
          </section>
        ) : null}

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
            onDesign={designVoice}
            onUpload={(speaker, file) => void uploadVoice(speaker, file)}
          />
        </div>
        </div>
      ) : activeStep === "cut" ? (
        <MultiTrackEditor
          project={project}
          onSaveCuts={(stem, cuts) => void saveCuts(stem, cuts)}
          onSaveAll={(stems) => void saveAllCuts(stems)}
          onRefresh={() => void refreshProject()}
        />
      ) : activeStep === "stems" ? (
        <RenderBoard
            project={project}
            jobs={jobs}
            batch={batch}
            building={buildingStems}
            sunoPlan={sunoPlan}
            onBuild={buildStems}
            onRender={renderStem}
            onExtractStem={extractStem}
            onImportSuno={(stem, clipId) => void importSunoTake(stem, clipId)}
            onRenderAll={(stems, includeRendered) => void renderAll(stems, includeRendered)}
            onShowError={(title, body) => setErrorDialog({ title, body })}
            onSelectTake={(stem, takeId) => void selectTake(stem, takeId)}
            onRefVoiceChange={(stem, refVoiceId) => void changeRefVoice(stem, refVoiceId)}
            onSelectPersona={(speaker, persona) => void selectPersona(speaker, persona)}
            onSaveSunoFields={(stem, fields) => void saveSunoFields(stem, fields)}
            onSuggestSunoStyle={suggestSunoStyle}
            onRefresh={() => void refreshProject()}
          />
      ) : activeStep === "sound" ? (
        <SoundBoard
          project={project}
          jobs={jobs}
          onProject={setProject}
          onTrackJob={trackJob}
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

      {errorDialog ? (
        <ErrorDialog
          title={errorDialog.title}
          body={errorDialog.body}
          onClose={() => setErrorDialog(null)}
          onRecheck={() => void recheckCaptcha()}
          rechecking={recheckingCaptcha}
        />
      ) : null}
    </div>
  );
}
