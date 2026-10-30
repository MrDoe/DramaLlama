import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  MagicWand,
  MicrophoneStage,
  Sparkle,
  WarningCircle,
} from "@phosphor-icons/react";
import { api } from "../api";
import type { EngineInfo, EngineName, Language } from "../types";
import { autoEngine, engineLabel } from "../utils";

const SAMPLE = `# THE HOUSE ON BELLWEATHER ROAD

**An audio drama in one scene** · English

## SCENE 1 — LIVING ROOM, 11:48 PM

*[AMBIENCE: rain against the windows, a clock ticking]*
*[SFX: a cardboard box dragged across bare floor]*

**MARA** *(cold, measured; controlled anger)*
You're late.

**ELIAS** *(out of breath, apologetic)*
Traffic. The bridge was—

**MARA** *(cutting him off, sharper)*
I don't need the weather report, Elias. I need you to pack.

**ELIAS** *(quiet, defensive)*
I came, didn't I?

**MARA** *(a thin, humourless laugh)*
<laugh> You came. Four days after the funeral. To the house you swore you'd never set foot in again.
`;

export function NewProjectPage() {
  const navigate = useNavigate();
  const [title, setTitle] = useState("");
  const [language, setLanguage] = useState<Language>("en");
  const [defaultEngine, setDefaultEngine] = useState<EngineName | null>(null);
  const [engines, setEngines] = useState<EngineInfo[]>([]);
  const [scriptText, setScriptText] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api.engines().then(setEngines).catch(() => setEngines([]));
  }, []);

  function changeLanguage(next: Language) {
    setLanguage(next);
    if (defaultEngine) {
      const info = engines.find((engine) => engine.name === defaultEngine);
      if (info && !info.language_agnostic && !info.languages.includes(next))
        setDefaultEngine(null);
    }
  }

  async function createProject() {
    if (!scriptText.trim()) {
      setError("Paste a script first — even a single scene is enough.");
      return;
    }
    setCreating(true);
    setError("");
    try {
      const project = await api.createProject({
        title,
        language,
        script_text: scriptText,
        default_engine: defaultEngine,
      });
      navigate(`/projects/${project.id}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not create project");
      setCreating(false);
    }
  }

  function loadSample() {
    setTitle("The House on Bellweather Road");
    setLanguage("en");
    setDefaultEngine(null);
    setScriptText(SAMPLE);
    setError("");
  }

  return (
    <div className="page">
      <Link to="/" className="back-link">
        <ArrowLeft size={13} />
        Projects
      </Link>

      <div className="project-header">
        <div>
          <p className="eyebrow">New drama</p>
          <h1 className="project-title">Start with the page.</h1>
          <p className="project-sub">
            Paste a screenplay, Markdown drama, or plain text scene
          </p>
        </div>
      </div>

      {error ? (
        <div className="error-banner" role="alert">
          <WarningCircle size={16} weight="fill" />
          {error}
        </div>
      ) : null}

      <div className="new-grid">
        <section className="panel form-card">
          <div className="field">
            <label className="label" htmlFor="project-title">
              Title <span>optional — detected from the script</span>
            </label>
            <input
              id="project-title"
              className="input"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="The House on Bellweather Road"
              autoComplete="off"
            />
          </div>

          <div className="field">
            <span className="label">
              Language <span>parsing and Whisper alignment</span>
            </span>
            <div className="segmented" role="group" aria-label="Language">
              <button
                type="button"
                className={`segment${language === "en" ? " active" : ""}`}
                onClick={() => changeLanguage("en")}
                aria-pressed={language === "en"}
              >
                <MicrophoneStage size={14} />
                English
              </button>
              <button
                type="button"
                className={`segment${language === "de" ? " active" : ""}`}
                onClick={() => changeLanguage("de")}
                aria-pressed={language === "de"}
              >
                <MicrophoneStage size={14} />
                Deutsch
              </button>
            </div>
          </div>

          <div className="field">
            <label className="label" htmlFor="project-engine">
              Engine <span>project default · can be changed per stem</span>
            </label>
            <select
              id="project-engine"
              className="input"
              value={defaultEngine ?? "auto"}
              onChange={(event) =>
                setDefaultEngine(
                  event.target.value === "auto"
                    ? null
                    : (event.target.value as EngineName),
                )
              }
            >
              <option value="auto">
                Auto · {engineLabel(autoEngine(language))}
              </option>
              {engines
                .filter(
                  (engine) =>
                    engine.name !== "fake" &&
                    (engine.language_agnostic ||
                      engine.languages.includes(language)),
                )
                .map((engine) => (
                  <option key={engine.name} value={engine.name}>
                    {engineLabel(engine.name)}
                    {engine.voice_mode === "preset"
                      ? " · preset / persona voices"
                      : " · reference voices"}
                  </option>
                ))}
            </select>
          </div>

          <div className="field">
            <label className="label" htmlFor="script-text">
              Script <span>dialogue is kept verbatim</span>
            </label>
            <textarea
              id="script-text"
              className="paste"
              value={scriptText}
              onChange={(event) => setScriptText(event.target.value)}
              placeholder={`# MY DRAMA\n\n## SCENE 1 — A ROOM, NIGHT\n\n**MARA** *(cold, measured)*\nYou're late.\n\n**ELIAS** *(out of breath)*\nTraffic.`}
              spellCheck={false}
            />
            <div className="sample-row">
              <button type="button" className="text-button" onClick={loadSample}>
                Load a sample scene
              </button>
              <span className="panel-sub">
                {scriptText ? `${scriptText.split("\n").length} lines` : "UTF-8 text"}
              </span>
            </div>
          </div>

          <div className="hero-actions">
            <button
              className="button button-primary"
              onClick={createProject}
              disabled={creating}
            >
              {creating ? <span className="spinner" /> : <Sparkle size={16} weight="fill" />}
              {creating ? "Reading the script…" : "Create project"}
            </button>
            <Link to="/" className="button button-ghost">
              Cancel
            </Link>
          </div>
        </section>

        <aside className="panel how-card">
          <h3>From page to speakers</h3>
          <p>One parser, one cast, one finished mix.</p>

          <div className="how-step">
            <span className="step-num-lg">01</span>
            <div>
              <h4>Parse the page</h4>
              <p>
                Scene headings, speaker labels, parentheticals and stage
                directions become structured lines. The raw text is never
                rewritten.
              </p>
            </div>
          </div>
          <div className="how-step">
            <span className="step-num-lg">02</span>
            <div>
              <h4>Build the stems</h4>
              <p>
                Lines are grouped by scene and speaker. Each character gets one
                continuous performance per scene, not a fresh voice per line.
              </p>
            </div>
          </div>
          <div className="how-step">
            <span className="step-num-lg">03</span>
            <div>
              <h4>Perform and cut</h4>
              <p>
                The chosen engine renders the stem. Whisper finds every word
                on CPU, then the stem is cut back into individual line clips.
              </p>
            </div>
          </div>
          <div className="how-step">
            <span className="step-num-lg">04</span>
            <div>
              <h4>Sound and mix</h4>
              <p>
                Search the local SFX library, place beds and one-shots, then
                duck, EQ and master to podcast or broadcast loudness.
              </p>
            </div>
          </div>

          <div className="engine-note">
            <MagicWand size={15} weight="fill" />
            <span>
              The engine is a project default, not a language: Breeze designs
              reference voices for English, Breeze-DE for German, Chatterbox
              clones a sample in either language, and Suno renders dialogue
              through BetterSuno with preset or persona voices. All share the
              same stem and alignment pipeline, and each stem can override it.
            </span>
          </div>
        </aside>
      </div>
    </div>
  );
}
