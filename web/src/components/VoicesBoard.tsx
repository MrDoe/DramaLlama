import type { Job, Project } from "../types";
import { initials, speakerStyle } from "../utils";
import { VoiceStudio } from "./VoiceStudio";

export function VoicesBoard({
  project,
  jobs,
  speaker,
  onSpeaker,
  onGenerate,
  onSaveReference,
  onDesign,
  onUpload,
}: {
  project: Project;
  jobs: Record<string, Job>;
  speaker: string | null;
  onSpeaker: (speaker: string) => void;
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
    opts?: { name?: string; seed?: number },
  ) => void;
  onUpload: (speaker: string, file: File, name?: string) => void;
}) {
  const lines = project.script.scenes.flatMap((scene) => scene.lines);
  const characters = Object.values(project.script.characters).sort((a, b) => {
    const countA = lines.filter((line) => line.speaker === a.id).length;
    const countB = lines.filter((line) => line.speaker === b.id).length;
    return countB - countA;
  });
  const active =
    speaker && project.script.characters[speaker]
      ? speaker
      : characters[0]?.id ?? null;

  return (
    <div className="voices-page">
      <aside className="panel voices-cast">
        <div className="panel-head">
          <div>
            <h2 className="panel-title">Cast</h2>
            <p className="panel-sub">
              {characters.length} character{characters.length === 1 ? "" : "s"}
            </p>
          </div>
        </div>
        <div className="voices-cast-list">
          {characters.length === 0 ? (
            <p className="cast-desc">Parse a script to meet the cast.</p>
          ) : (
            characters.map((character) => {
              const count = lines.filter(
                (line) => line.speaker === character.id,
              ).length;
              const voiceCount = character.references?.length ?? 0;
              return (
                <button
                  key={character.id}
                  type="button"
                  className={`voices-cast-item${active === character.id ? " active" : ""}`}
                  onClick={() => onSpeaker(character.id)}
                  aria-pressed={active === character.id}
                >
                  <span className="avatar" style={speakerStyle(character.id)}>
                    {initials(character.name)}
                  </span>
                  <span className="voices-cast-main">
                    <strong>{character.name}</strong>
                    <small>
                      {count} line{count === 1 ? "" : "s"} ·{" "}
                      {voiceCount} voice{voiceCount === 1 ? "" : "s"}
                    </small>
                  </span>
                </button>
              );
            })
          )}
        </div>
      </aside>

      <div className="voices-main">
        {active ? (
          <VoiceStudio
            key={active}
            project={project}
            speaker={active}
            jobs={jobs}
            onGenerate={onGenerate}
            onSaveReference={onSaveReference}
            onDesign={onDesign}
            onUpload={onUpload}
          />
        ) : (
          <section className="panel voice-page-panel">
            <div className="voice-empty">
              <p>No characters yet. Parse a script first.</p>
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
