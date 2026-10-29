import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { FolderOpen, Waveform } from "@phosphor-icons/react";

export function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="app">
      <header className="topbar">
        <NavLink to="/" className="brand" aria-label="Drama Studio home">
          <span className="brand-mark">
            <Waveform size={19} weight="bold" />
          </span>
          <span className="brand-text">
            <strong>Drama Studio</strong>
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
        </nav>

        <div className="topbar-right">
          <span className="status-pill">
            <span className="status-dot" />
            Local engine
          </span>
        </div>
      </header>

      <main>{children}</main>

      <footer className="footer">
        <span>Breeze TTS 2 · Qwen3-TTS · Whisper alignment</span>
        <span>Research / non-commercial weights</span>
      </footer>
    </div>
  );
}
