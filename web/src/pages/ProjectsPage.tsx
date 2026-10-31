import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  Brain,
  Clock,
  GlobeHemisphereWest,
  MicrophoneStage,
  MusicNotes,
  Plus,
  WarningCircle,
  Waveform,
} from "@phosphor-icons/react";
import { api } from "../api";
import type { ProjectSummary } from "../types";
import { formatDate, formatDuration, waveBars } from "../utils";

function ProjectCard({ project }: { project: ProjectSummary }) {
  const bars = waveBars(project.id, 34);
  return (
    <Link to={`/projects/${project.id}`} className="project-card">
      <div className="project-card-top">
        <div>
          <h3>{project.title}</h3>
          <div className="project-card-sub">
            {project.scenes} scenes · {project.lines} lines
          </div>
        </div>
        <span className={`badge ${project.language === "de" ? "badge-violet" : "badge-accent"}`}>
          {project.language === "de" ? "Deutsch" : "English"}
        </span>
      </div>

      <div className="card-wave" aria-hidden="true">
        {bars.map((height, index) => (
          <span
            key={index}
            style={{
              height: `${Math.max(10, height)}%`,
              ["--i" as string]: index,
            }}
          />
        ))}
      </div>

      <div className="card-meta">
        <span>
          <Clock size={11} style={{ verticalAlign: "-1px", marginRight: 4 }} />
          {formatDuration(project.runtime_estimate_s)}
        </span>
        <span>
          coverage <strong>{Math.round(project.coverage * 100)}%</strong>
        </span>
        <span>{formatDate(project.updated_at)}</span>
      </div>
    </Link>
  );
}

function EngineCard({
  icon,
  name,
  detail,
  badge,
  tone = "accent",
}: {
  icon: React.ReactNode;
  name: string;
  detail: string;
  badge: string;
  tone?: "accent" | "violet" | "teal";
}) {
  return (
    <div className={`engine-card tone-${tone}`}>
      <div className="engine-left">
        <span className="engine-icon">{icon}</span>
        <span>
          <div className="engine-name">{name}</div>
          <div className="engine-detail">{detail}</div>
        </span>
      </div>
      <span className="engine-badge">{badge}</span>
    </div>
  );
}

export function ProjectsPage() {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [llm, setLlm] = useState<{
    model: string;
    provider: "local" | "cloud";
  } | null>(null);

  useEffect(() => {
    let active = true;
    api
      .listProjects()
      .then((items) => {
        if (active) setProjects(items);
      })
      .catch((reason: Error) => {
        if (active) setError(reason.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    api
      .getConfig()
      .then((config) => {
        if (active) setLlm(config.llm);
      })
      .catch(() => {
        // keep the static fallback row below
      });
    return () => {
      active = false;
    };
  }, []);

  return (
    <div className="page">
      <section className="hero">
        <div style={{ width: 200, textAlign: "center" }}>
          <img src="/llogo.png" alt="Drama Llama mascot" width={200} height={200} />
          <span className="brand-text">
            <strong>Drama Llama</strong>
            <span>the audio drama lab</span>
          </span>
        </div>
        <div className="hero-copy">
          <h1>
            Scripts in.
            <br />
            <em>Drama out.</em>
          </h1>
          <p>
            Paste a script. The LLM splits it into a scene stem and
            local TTS engines or Suno render the lines. 
            Whisper aligns the stems to the script.
            Then you can add SFX and mix the master.
          </p>
          <div className="hero-actions">
            <Link to="/new" className="button button-primary">
              <Plus size={16} weight="bold" />
              New drama
            </Link>
            <a href="#projects" className="button button-secondary">
              Open a project
              <ArrowRight size={15} />
            </a>
          </div>
        </div>

        <aside className="hero-aside" aria-label="Engines">
          <div className="studio-panel">
            <div className="studio-engines">
              <EngineCard
                icon={<Brain size={19} weight="bold" />}
                name="LLM"
                detail={
                  llm
                    ? `${llm.model} · script parsing & casting`
                    : "Script parsing & casting"
                }
                badge={llm && llm.provider === "cloud" ? "WEB" : "GPU"}
              />
              <EngineCard
                icon={<Waveform size={19} weight="bold" />}
                name="Breeze TTS 2"
                detail="English · reference design → clone"
                badge="GPU"
              />
              <EngineCard
                icon={<MicrophoneStage size={19} weight="bold" />}
                name="Chatterbox 0.5B"
                detail="German · voice-sample cloning"
                badge="GPU"
                tone="violet"
              />
              <EngineCard
                icon={<GlobeHemisphereWest size={19} weight="bold" />}
                name="Whisper large-v3"
                detail="CPU int8 · word-level stem alignment"
                badge="CPU"
              />
              <EngineCard
                icon={<MusicNotes size={19} weight="bold" />}
                name="Suno"
                detail="Music · prompt → score via bridge"
                badge="WEB"
                tone="teal"
              />
            </div>
          </div>
        </aside>
      </section>

      {error ? (
        <div className="error-banner" role="alert">
          <WarningCircle size={16} weight="fill" />
          {error}
        </div>
      ) : null}

      <section id="projects">
        <div className="section-head">
          <div>
            <h2>Your dramas</h2>
            <p>
              {loading
                ? "Reading the project shelf…"
                : `${projects.length} project${projects.length === 1 ? "" : "s"} on this machine`}
            </p>
          </div>
        </div>

        {loading ? (
          <div className="project-grid">
            <div className="skeleton" />
            <div className="skeleton" />
            <div className="skeleton" />
          </div>
        ) : projects.length > 0 ? (
          <div className="project-grid">
            {projects.map((project) => (
              <ProjectCard key={project.id} project={project} />
            ))}
          </div>
        ) : (
          <div className="empty-panel">
            <div>
              <h3>The shelf is empty.</h3>
              <p>
                Start with a script you already have — a screenplay, a Markdown
                drama, or a plain text scene. The deterministic parser keeps
                every line; the LLM can handle the messy attribution later.
              </p>
            </div>
            <div className="empty-steps">
              <div className="empty-step">
                <span>01</span> Paste the script
              </div>
              <div className="empty-step">
                <span>02</span> Review scenes and speakers
              </div>
              <div className="empty-step">
                <span>03</span> Design the cast and render stems
              </div>
              <div className="empty-step">
                <span>04</span> Add SFX and mix the master
              </div>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
