import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { FolderOpen, GearSix } from "@phosphor-icons/react";
import { api } from "../api";
import type { EngineInfo } from "../types";
import { engineLabel } from "../utils";

export function Shell({ children }: { children: React.ReactNode }) {
  const [engines, setEngines] = useState<EngineInfo[]>([]);

  useEffect(() => {
    let active = true;
    const load = () => {
      api
        .engines()
        .then((items) => {
          if (active) setEngines(items);
        })
        .catch(() => {
          // keep last known state
        });
    };
    load();
    const id = window.setInterval(load, 15000);
    return () => {
      active = false;
      window.clearInterval(id);
    };
  }, []);

  return (
    <div className="app">
      <header className="topbar">
        <NavLink to="/" className="brand" aria-label="Drama Llama home">
          <span className="brand-mark">
            <img src="/llogo.png" alt="" width={38} height={38} />
          </span>
          <span className="brand-text">
            <strong>Drama Llama</strong>
            <span>local audio drama lab</span>
          </span>
        </NavLink>

        <nav className="topbar-nav" aria-label="Primary">
          <NavLink
            to="/"
            end
            className={({ isActive }) =>
              `topbar-link${isActive ? " active" : ""}`
            }
          >
            <FolderOpen size={15} />
            Projects
          </NavLink>
          <NavLink
            to="/settings"
            className={({ isActive }) =>
              `topbar-link${isActive ? " active" : ""}`
            }
          >
            <GearSix size={15} />
            Settings
          </NavLink>
        </nav>

        <div className="topbar-right">
          <div className="engine-status" role="status" aria-label="TTS engine connection status">
            {engines
              .filter((engine) => engine.name !== "fake")
              .map((engine) => (
              <span
                key={engine.name}
                className={`engine-status-item ${engine.available ? "ok" : "bad"}`}
                title={`${engineLabel(engine.name)}: ${engine.available ? "connected" : "not connected"}`}
              >
                <span className="status-dot" />
                {engineLabel(engine.name)}
              </span>
            ))}
          </div>
        </div>
      </header>

      <main>{children}</main>

      <footer className="footer">
        <span>Breeze · Chatterbox · Suno · Whisper alignment</span>
        <span>Research / non-commercial weights</span>
      </footer>
    </div>
  );
}