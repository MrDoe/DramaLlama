import { useEffect, useMemo, useState } from "react";
import {
  CheckCircle,
  FileAudio,
  SpeakerHigh,
  WarningCircle,
  Waveform,
} from "@phosphor-icons/react";
import { api } from "../api";
import type {
  EngineName,
  GeneratedReferenceResult,
  Job,
  Project,
  RefVoice,
} from "../types";
import { formatDuration } from "../utils";

const SOURCE_LABELS: Record<RefVoice["source"], string> = {
  unknown: "Legacy",
  designed: "Designed",
  generated: "From sample",
  uploaded: "Uploaded",
};

function voiceMeta(entry: RefVoice): string {
  const parts: string[] = [];
  if (entry.duration_s) parts.push(formatDuration(entry.duration_s));
  if (entry.f0_hz) {
    const range = entry.f0_range_hz
      ? ` (${Math.round(entry.f0_range_hz[0])}–${Math.round(entry.f0_range_hz[1])} Hz)`
      : "";
    parts.push(`F0 ${Math.round(entry.f0_hz)} Hz${range}`);
  }
  if (
    entry.transcript_source !== "edited" &&
    entry.transcript_confidence != null
  ) {
    parts.push(`${Math.round(entry.transcript_confidence * 100)}% confidence`);
  } else if (entry.transcript_source === "edited") {
    parts.push("transcript edited");
  }
  if (entry.created_at) {
    const created = new Date(entry.created_at);
    if (!Number.isNaN(created.getTime())) {
      parts.push(created.toLocaleDateString());
    }
  }
  return parts.join(" · ");
}

function sourceBadge(source: RefVoice["source"]) {
  const className =
    source === "generated"
      ? "badge badge-accent"
      : source === "designed"
        ? "badge badge-violet"
        : "badge";
  return <span className={className}>{SOURCE_LABELS[source]}</span>;
}

function projectEngine(project: Project): EngineName {
  return (
    project.default_engine ?? (project.language === "de" ? "chatterbox" : "breeze")
  );
}

function readAudioDuration(file: File): Promise<number> {
  const url = URL.createObjectURL(file);
  return new Promise((resolve, reject) => {
    const audio = new Audio();
    audio.preload = "metadata";
    audio.onloadedmetadata = () => {
      const duration = audio.duration;
      URL.revokeObjectURL(url);
      if (!Number.isFinite(duration)) {
        reject(new Error("Could not read the audio duration"));
      } else {
        resolve(duration);
      }
    };
    audio.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("This file could not be decoded as audio"));
    };
    audio.src = url;
  });
}

function Warnings({ warnings }: { warnings?: string[] }) {
  if (!warnings?.length) return null;
  return (
    <ul className="voice-warnings" role="status">
      {warnings.map((warning, index) => (
        <li key={`${index}-${warning}`}>
          <WarningCircle size={13} weight="fill" />
          <span>{warning}</span>
        </li>
      ))}
    </ul>
  );
}

function ReferenceEditor({
  entry,
  projectId,
  onCancel,
  onSave,
}: {
  entry: RefVoice;
  projectId: string;
  onCancel: () => void;
  onSave: (patch: {
    name?: string;
    ref_text?: string;
    make_default?: boolean;
  }) => Promise<void>;
}) {
  const [name, setName] = useState(entry.name);
  const [text, setText] = useState(entry.ref_text ?? "");
  const [makeDefault, setMakeDefault] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function save() {
    if (!text.trim()) {
      setError("The transcript must not be blank.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await onSave({
        name: name !== entry.name ? name : undefined,
        ref_text: text !== (entry.ref_text ?? "") ? text : undefined,
        make_default: makeDefault || undefined,
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save the voice");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="voice-editor">
      <div className="voice-editor-head">
        <div>
          <h3>Edit voice</h3>
          <p className="panel-note">
            The transcript must match the audio word-for-word. Do not paraphrase.
          </p>
        </div>
        <button type="button" className="text-button" onClick={onCancel}>
          Cancel
        </button>
      </div>
      <audio
        controls
        preload="metadata"
        src={api.fileUrl(projectId, entry.file)}
        aria-label={`Preview ${entry.name}`}
      />
      <label className="voice-field">
        <span>Name</span>
        <input
          className="input"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      </label>
      <label className="voice-field">
        <span>Transcript</span>
        <textarea
          className="input voice-transcript"
          value={text}
          onChange={(event) => setText(event.target.value)}
          spellCheck={false}
        />
      </label>
      <label className="voice-check">
        <input
          type="checkbox"
          checked={makeDefault}
          onChange={(event) => setMakeDefault(event.target.checked)}
        />
        Use as this character&apos;s default voice
      </label>
      {entry.warnings?.length ? <Warnings warnings={entry.warnings} /> : null}
      {error ? (
        <p className="voice-error" role="alert">
          {error}
        </p>
      ) : null}
      <div className="voice-editor-foot">
        <button
          type="button"
          className="button button-primary button-sm"
          onClick={() => void save()}
          disabled={saving}
        >
          {saving ? <span className="spinner" /> : <CheckCircle size={14} />}
          {saving ? "Saving…" : "Save changes"}
        </button>
      </div>
    </div>
  );
}

function GeneratedReview({
  result,
  projectId,
  onCancel,
  onSave,
}: {
  result: GeneratedReferenceResult;
  projectId: string;
  onCancel: () => void;
  onSave: (patch: {
    name?: string;
    ref_text?: string;
    make_default?: boolean;
  }) => Promise<void>;
}) {
  const [name, setName] = useState(result.reference.name);
  const [text, setText] = useState(result.ref_text);
  const [makeDefault, setMakeDefault] = useState(result.is_default);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function save() {
    if (!text.trim()) {
      setError("The transcript must not be blank.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await onSave({
        name: name !== result.reference.name ? name : undefined,
        ref_text: text !== result.ref_text ? text : undefined,
        make_default: makeDefault || undefined,
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save the voice");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="voice-review">
      <div className="voice-review-head">
        <div>
          <h3>
            <CheckCircle size={16} weight="fill" /> Voice generated
          </h3>
          <p className="panel-note">
            The voice is already saved. Listen once, correct any Whisper
            mistakes, then save the changes.
          </p>
        </div>
      </div>
      <audio
        controls
        preload="metadata"
        src={api.fileUrl(projectId, result.ref_file)}
        aria-label={`Preview ${name || result.reference.name}`}
      />
      <div className="voice-meta-grid">
        <span>
          <strong>{formatDuration(result.duration_s)}</strong>
          <small>duration</small>
        </span>
        <span>
          <strong>
            {result.f0_hz ? `${Math.round(result.f0_hz)} Hz` : "—"}
          </strong>
          <small>median F0</small>
        </span>
        <span>
          <strong>
            {result.transcript_confidence != null
              ? `${Math.round(result.transcript_confidence * 100)}%`
              : "—"}
          </strong>
          <small>confidence</small>
        </span>
        <span>
          <strong>
            {result.window_start_s != null && result.window_end_s != null
              ? `${result.window_start_s.toFixed(1)}–${result.window_end_s.toFixed(1)}s`
              : "—"}
          </strong>
          <small>source window</small>
        </span>
      </div>
      <Warnings warnings={result.warnings} />
      <label className="voice-field">
        <span>Name</span>
        <input
          className="input"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      </label>
      <label className="voice-field">
        <span>Transcript</span>
        <textarea
          className="input voice-transcript"
          value={text}
          onChange={(event) => setText(event.target.value)}
          spellCheck={false}
        />
        <small>
          Breeze clones from this text. It must match the audio word-for-word;
          punctuation is fine, paraphrasing is not.
        </small>
      </label>
      <label className="voice-check">
        <input
          type="checkbox"
          checked={makeDefault}
          onChange={(event) => setMakeDefault(event.target.checked)}
        />
        Use as this character&apos;s default voice
      </label>
      {error ? (
        <p className="voice-error" role="alert">
          {error}
        </p>
      ) : null}
      <div className="voice-editor-foot">
        <button type="button" className="button button-ghost button-sm" onClick={onCancel}>
          Back to voices
        </button>
        <button
          type="button"
          className="button button-primary button-sm"
          onClick={() => void save()}
          disabled={saving}
        >
          {saving ? <span className="spinner" /> : <CheckCircle size={14} />}
          {saving ? "Saving…" : "Save changes"}
        </button>
      </div>
    </div>
  );
}

export function VoiceStudio({
  project,
  speaker,
  jobs,
  onGenerate,
  onSaveReference,
  onDesign,
  onUpload,
  onDirection,
}: {
  project: Project;
  speaker: string;
  jobs: Record<string, Job>;
  onGenerate: (
    speaker: string,
    file: File,
    opts: { name?: string; makeDefault?: boolean },
  ) => Promise<void>;
  onSaveReference: (
    speaker: string,
    refId: string,
    patch: { name?: string; ref_text?: string; make_default?: boolean },
  ) => Promise<void>;
  onDesign: (
    speaker: string,
    opts?: { name?: string; seed?: number; description?: string },
  ) => void;
  onUpload: (speaker: string, file: File, name?: string) => void;
  onDirection: (speaker: string, text: string) => Promise<void>;
}) {
  const character = project.script.characters[speaker];
  const engine = projectEngine(project);
  const isBreeze = engine === "breeze" || engine === "breeze-de" || engine === "fake";
  const isChatterbox = engine === "chatterbox";
  const references = useMemo(
    () => character?.references ?? [],
    [character?.references],
  );
  // Legacy fallback: a character with `ref_file` that no reference list entry
  // points at. Adoption in `normalize_references` only runs for the
  // Breeze family, so a stray ref on a Chatterbox or Suno project still lands
  // here rather than disappearing from the UI. Do not delete without making
  // adoption engine-agnostic first.
  const legacyDefault =
    character?.ref_file &&
    !references.some((entry) => entry.file === character.ref_file)
      ? character.ref_file
      : null;
  const generateJob = jobs[`char:${speaker}:generate`];
  const designJob = jobs[`char:${speaker}`];
  const designBusy =
    designJob?.status === "queued" || designJob?.status === "running";
  const [mode, setMode] = useState<"list" | "generate" | "edit">("list");
  const [editing, setEditing] = useState<RefVoice | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [fileDuration, setFileDuration] = useState<number | null>(null);
  const [fileWarning, setFileWarning] = useState("");
  const [name, setName] = useState("");
  const [makeDefault, setMakeDefault] = useState(false);
  const [starting, setStarting] = useState(false);
  const [generationStarted, setGenerationStarted] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busyRef, setBusyRef] = useState<string | null>(null);
  const [dismissedDesignError, setDismissedDesignError] = useState<string | null>(
    null,
  );
  const [now, setNow] = useState(() => Date.now());
  // "Design from description" sends exactly this text to the TTS engine, so the
  // panel must expose it. A deterministic parse learns names and lines, never
  // voices, and without this field the button would design a generic narrator
  // for everybody.
  const [direction, setDirection] = useState(character?.description ?? "");
  const [directionBusy, setDirectionBusy] = useState(false);
  const directionDirty = direction.trim() !== (character?.description ?? "").trim();

  useEffect(() => {
    setDirection(character?.description ?? "");
  }, [speaker, character?.description]);

  async function saveDirection() {
    setDirectionBusy(true);
    try {
      await onDirection(speaker, direction.trim());
      setNotice("Voice direction saved. The next design uses it.");
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Could not save the voice direction",
      );
    } finally {
      setDirectionBusy(false);
    }
  }

  useEffect(() => {
    if (!designBusy) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [designBusy]);

  if (!character) return null;

  const designStartedAt = designJob?.started_at ?? designJob?.created_at ?? null;
  const designElapsed =
    designBusy && designStartedAt
      ? Math.max(0, Math.floor((now - new Date(designStartedAt).getTime()) / 1000))
      : 0;

  const result =
    generationStarted && generateJob?.status === "succeeded"
      ? (generateJob.result as GeneratedReferenceResult | undefined)
      : undefined;
  const generating =
    generationStarted &&
    (!generateJob ||
      generateJob.status === "queued" ||
      generateJob.status === "running");

  async function chooseFile(next: File | null) {
    setFile(next);
    setFileDuration(null);
    setFileWarning("");
    setError("");
    if (!next) return;
    try {
      const duration = await readAudioDuration(next);
      setFileDuration(duration);
      if (duration < 6) {
        setError(
          `This clip is ${duration.toFixed(1)} s. Choose a sample of at least 6 s.`,
        );
      } else if (duration > 120) {
        setError(
          "This clip is longer than 2 minutes. Trim it before generating.",
        );
      } else if (duration > 60) {
        setFileWarning(
          "Long clip — transcription may take a while. Only the best 6–11 s window will be kept.",
        );
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not read this file");
    }
  }

  async function startGeneration() {
    if (!file) {
      setError("Choose an audio file first.");
      return;
    }
    if (fileDuration != null && (fileDuration < 6 || fileDuration > 120)) {
      setError("Choose a sample of at least 6 s (up to 2 minutes).");
      return;
    }
    setStarting(true);
    setError("");
    setNotice("");
    try {
      await onGenerate(speaker, file, {
        name: name.trim() || undefined,
        makeDefault,
      });
      setGenerationStarted(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not start generation");
    } finally {
      setStarting(false);
    }
  }

  async function makeDefaultVoice(entry: RefVoice) {
    setBusyRef(entry.id);
    setError("");
    try {
      await onSaveReference(speaker, entry.id, { make_default: true });
      setNotice(`“${entry.name}” is now the default voice.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not set default");
    } finally {
      setBusyRef(null);
    }
  }

  async function saveGenerated(patch: {
    name?: string;
    ref_text?: string;
    make_default?: boolean;
  }) {
    if (!result) return;
    await onSaveReference(speaker, result.ref_voice_id, patch);
    setGenerationStarted(false);
    setMode("list");
    setFile(null);
    setFileDuration(null);
    setFileWarning("");
    setName("");
    setMakeDefault(false);
    setNotice("Voice saved.");
  }

  async function saveEdited(patch: {
    name?: string;
    ref_text?: string;
    make_default?: boolean;
  }) {
    if (!editing) return;
    await onSaveReference(speaker, editing.id, patch);
    setEditing(null);
    setMode("list");
    setNotice("Voice updated.");
  }

  return (
    <section className="panel voice-page-panel">
        <div className="voice-dialog-head">
          <div>
            <h2 id="voice-dialog-title">
              <SpeakerHigh size={16} weight="fill" /> Voices for {character.name}
            </h2>
            <p className="panel-note">
              {isBreeze
                ? "Breeze clones from a 6–11 s clip plus its exact transcript."
                : isChatterbox
                  ? "Chatterbox clones directly from an uploaded sample; no transcript is needed."
                  : "Manage this character's reference voices."}
            </p>
          </div>
        </div>

        <div className="voice-direction">
          <label className="voice-field">
            <span>Voice direction</span>
            <textarea
              className="input voice-transcript"
              rows={2}
              value={direction}
              onChange={(event) => setDirection(event.target.value)}
              placeholder="Woman in her fifties, gravelly, clipped military diction, barely-contained fear"
              spellCheck={false}
              aria-label={`Voice direction for ${character?.name ?? speaker}`}
            />
          </label>
          <button
            type="button"
            className="button button-secondary button-sm"
            onClick={() => void saveDirection()}
            disabled={!directionDirty || directionBusy}
          >
            {directionBusy ? "Saving…" : "Save direction"}
          </button>
        </div>

        <div className="voice-dialog-body">
          {designBusy ? (
            <section
              className="voice-design-status"
              role="status"
              aria-live="polite"
              aria-busy="true"
            >
              <div className="voice-design-status-head">
                <span>
                  <span className="spinner" /> Designing voice from description
                </span>
                <strong>{Math.round((designJob?.progress ?? 0) * 100)}%</strong>
              </div>
              <div
                className="coverage-bar"
                role="progressbar"
                aria-label="Voice design progress"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={Math.round((designJob?.progress ?? 0) * 100)}
              >
                <div
                  className="coverage-fill voice-progress-active"
                  style={{
                    width: `${Math.round((designJob?.progress ?? 0) * 100)}%`,
                  }}
                />
              </div>
              <p className="voice-design-stage">
                {designJob?.message || "Queued…"}
              </p>
              <p className="voice-file-meta">
                {designElapsed > 0
                  ? `Elapsed ${formatDuration(designElapsed)} · `
                  : ""}
                Breeze loads the model and generates a 6–11 s reference. This can
                take a minute or two.
              </p>
            </section>
          ) : null}
          {designJob?.status === "failed" &&
          designJob.id !== dismissedDesignError ? (
            <section className="voice-design-status voice-design-error" role="alert">
              <div className="voice-design-status-head">
                <span>
                  <WarningCircle size={14} weight="fill" /> Voice design failed
                </span>
                <button
                  type="button"
                  className="text-button"
                  onClick={() => setDismissedDesignError(designJob.id)}
                >
                  Dismiss
                </button>
              </div>
              <p className="voice-error">
                {designJob.error?.split("\n").filter(Boolean).slice(-1)[0] ??
                  "Breeze could not design the reference voice."}
              </p>
              <p className="voice-file-meta">
                Check the character description and reference text, then try
                again.
              </p>
            </section>
          ) : null}
          {mode === "list" ? (
            <>
              {notice ? (
                <p className="voice-notice" role="status">
                  {notice}
                </p>
              ) : null}
              {error ? (
                <p className="voice-error" role="alert">
                  {error}
                </p>
              ) : null}
              {references.length === 0 && !legacyDefault ? (
                <div className="voice-empty">
                  <Waveform size={18} />
                  <p>
                    {designBusy
                      ? "The first voice is being designed above."
                      : "No voices yet. Design one or generate one from a sample."}
                  </p>
                </div>
              ) : (
                <div className="voice-list">
                  {legacyDefault ? (
                    <div className="voice-row">
                      <div className="voice-row-main">
                        <div className="voice-row-head">
                          <strong>Default voice</strong>
                          <span className="badge">Legacy</span>
                          <span className="badge badge-accent">Default</span>
                        </div>
                        <p className="voice-row-meta">
                          Stored before voice metadata existed.
                          {character.ref_text
                            ? " The transcript is available."
                            : " No transcript is stored yet."}
                        </p>
                        <audio
                          controls
                          preload="metadata"
                          src={api.fileUrl(project.id, legacyDefault)}
                          aria-label="Preview the default voice"
                        />
                      </div>
                      <div className="voice-row-actions">
                        <span className="panel-note">Re-generate to edit it here.</span>
                      </div>
                    </div>
                  ) : null}
                  {references.map((entry) => {
                    const isDefault = character.ref_file === entry.file;
                    return (
                      <div className="voice-row" key={entry.id}>
                        <div className="voice-row-main">
                          <div className="voice-row-head">
                            <strong>{entry.name}</strong>
                            {sourceBadge(entry.source)}
                            {isDefault ? (
                              <span className="badge badge-accent">Default</span>
                            ) : null}
                          </div>
                          <p className="voice-row-meta">{voiceMeta(entry)}</p>
                          <audio
                            controls
                            preload="metadata"
                            src={api.fileUrl(project.id, entry.file)}
                            aria-label={`Preview ${entry.name}`}
                          />
                          <Warnings warnings={entry.warnings} />
                        </div>
                        <div className="voice-row-actions">
                          {!isDefault ? (
                            <button
                              type="button"
                              className="button button-ghost button-sm"
                              onClick={() => void makeDefaultVoice(entry)}
                              disabled={busyRef === entry.id}
                            >
                              {busyRef === entry.id ? (
                                <span className="spinner" />
                              ) : null}
                              Use as default
                            </button>
                          ) : null}
                          <button
                            type="button"
                            className="button button-ghost button-sm"
                            onClick={() => {
                              setEditing(entry);
                              setMode("edit");
                              setNotice("");
                            }}
                          >
                            Edit
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </>
          ) : null}

          {mode === "edit" && editing ? (
            <ReferenceEditor
              entry={editing}
              projectId={project.id}
              onCancel={() => {
                setEditing(null);
                setMode("list");
              }}
              onSave={saveEdited}
            />
          ) : null}

          {mode === "generate" ? (
            <>
              {!generationStarted ? (
                <div className="voice-generate">
                  <h3>Generate from a sample</h3>
                  <p className="panel-note">
                    Upload one speaker with clean speech and no music.
                    Recommended 15–30 s. Clips up to 2 minutes are cut
                    automatically to the best 6–11 s window.
                  </p>
                  <label className="button button-secondary button-sm voice-file">
                    <FileAudio size={14} />
                    {file ? file.name : "Choose audio file"}
                    <input
                      type="file"
                      accept="audio/*"
                      onChange={(event) => void chooseFile(event.target.files?.[0] ?? null)}
                    />
                  </label>
                  {fileDuration != null ? (
                    <p className="voice-file-meta">
                      {formatDuration(fileDuration)} · {Math.round((file?.size ?? 0) / 1024)} KB
                    </p>
                  ) : null}
                  {fileWarning ? (
                    <p className="voice-file-warning" role="status">
                      {fileWarning}
                    </p>
                  ) : null}
                  <label className="voice-field">
                    <span>Name (optional)</span>
                    <input
                      className="input"
                      value={name}
                      placeholder={`Sample ${references.length + 1}`}
                      onChange={(event) => setName(event.target.value)}
                    />
                  </label>
                  <label className="voice-check">
                    <input
                      type="checkbox"
                      checked={makeDefault}
                      onChange={(event) => setMakeDefault(event.target.checked)}
                    />
                    Use as this character&apos;s default voice
                  </label>
                  {error ? (
                    <p className="voice-error" role="alert">
                      {error}
                    </p>
                  ) : null}
                  <div className="voice-editor-foot">
                    <button
                      type="button"
                      className="button button-ghost button-sm"
                      onClick={() => {
                        setMode("list");
                        setError("");
                      }}
                    >
                      Back
                    </button>
                    <button
                      type="button"
                      className="button button-primary button-sm"
                      onClick={() => void startGeneration()}
                      disabled={!file || starting}
                    >
                      {starting ? <span className="spinner" /> : <Waveform size={14} />}
                      {starting ? "Starting…" : "Generate voice"}
                    </button>
                  </div>
                </div>
              ) : null}

              {generating ? (
                <div className="voice-generate" role="status" aria-live="polite">
                  <h3>Preparing the reference</h3>
                  <div className="render-progress">
                    <div className="render-progress-head">
                      <span>{generateJob?.message || "Queued…"}</span>
                      <strong>{Math.round((generateJob?.progress ?? 0) * 100)}%</strong>
                    </div>
                    <div
                      className="coverage-bar"
                      role="progressbar"
                      aria-label="Reference generation progress"
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-valuenow={Math.round((generateJob?.progress ?? 0) * 100)}
                    >
                      <div
                        className="coverage-fill"
                        style={{ width: `${Math.round((generateJob?.progress ?? 0) * 100)}%` }}
                      />
                    </div>
                  </div>
                  <p className="panel-note">
                    This runs Whisper on the CPU. You can leave this page; the
                    voice will appear in the list when it is ready.
                  </p>
                </div>
              ) : null}

              {generationStarted && generateJob?.status === "failed" ? (
                <div className="voice-generate">
                  <h3>Generation failed</h3>
                  <p className="voice-error" role="alert">
                    {generateJob.error?.split("\n").filter(Boolean).slice(-1)[0] ??
                      "The reference could not be generated."}
                  </p>
                  <div className="voice-editor-foot">
                    <button
                      type="button"
                      className="button button-ghost button-sm"
                      onClick={() => setGenerationStarted(false)}
                    >
                      Try again
                    </button>
                  </div>
                </div>
              ) : null}

              {result ? (
                <GeneratedReview
                  key={generateJob?.id}
                  result={result}
                  projectId={project.id}
                  onCancel={() => {
                    setGenerationStarted(false);
                    setMode("list");
                  }}
                  onSave={saveGenerated}
                />
              ) : null}
            </>
          ) : null}
        </div>

        {mode === "list" ? (
          <div className="voice-dialog-foot">
            {isBreeze ? (
              <>
                <button
                  type="button"
                  className="button button-secondary button-sm"
                  onClick={() => {
                    setMode("generate");
                    setError("");
                    setNotice("");
                  }}
                  disabled={designBusy}
                >
                  <Waveform size={14} /> Generate from sample
                </button>
                <button
                  type="button"
                  className="button button-ghost button-sm"
                  onClick={async () => {
                    // Persist first, and hand the design the text on screen:
                    // project state has not caught up with the PATCH yet, and a
                    // design that used the previous description would silently
                    // produce the wrong voice.
                    if (directionDirty) await saveDirection();
                    onDesign(speaker, {
                      name: `Voice ${references.length + 1}`,
                      seed: 42 + references.length,
                      description: direction.trim(),
                    });
                    setNotice("Voice design started; it will appear in the list when ready.");
                  }}
                  disabled={designBusy}
                >
                  <SpeakerHigh size={14} /> Design from description
                </button>
              </>
            ) : null}
            {isChatterbox ? (
              <label className="button button-secondary button-sm voice-file">
                <FileAudio size={14} /> Upload raw sample
                <input
                  type="file"
                  accept="audio/*"
                  onChange={(event) => {
                    const next = event.target.files?.[0];
                    if (next) {
                      onUpload(
                        speaker,
                        next,
                        `Sample ${(character.references?.length ?? 0) + 1}`,
                      );
                      setNotice("Sample uploaded.");
                    }
                  }}
                />
              </label>
            ) : null}
            <span className="voice-foot-spacer" />
          </div>
        ) : null}
    </section>
  );
}
