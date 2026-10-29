from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("DRAMA_STUDIO_DATA", REPO_ROOT / "data")).expanduser()
PROJECTS_DIR = DATA_DIR / "projects"
DB_PATH = DATA_DIR / "jobs.sqlite3"
WEB_DIST = REPO_ROOT / "web" / "dist"

HOST = os.environ.get("DRAMA_STUDIO_HOST", "127.0.0.1")
PORT = int(os.environ.get("DRAMA_STUDIO_PORT", "8770"))

# Ollama / local LLM defaults. The app never runs the LLM while Breeze is loaded.
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
SUNO_BRIDGE_URL = os.environ.get("SUNO_BRIDGE_URL", "http://127.0.0.1:9430")
CHATTERBOX_URL = os.environ.get("CHATTERBOX_URL", "http://127.0.0.1:4123")
LLM_MODEL = os.environ.get("DRAMA_STUDIO_LLM", "gemma4:12b-16k")
LLM_KEEP_ALIVE = os.environ.get("DRAMA_STUDIO_LLM_KEEP_ALIVE", "0")

# Local SFX library roots. Personal subfolders can be excluded in settings.
SFX_LIBRARY_ROOTS = [
    Path(p).expanduser()
    for p in os.environ.get(
        "DRAMA_STUDIO_SFX_ROOTS",
        f"{Path.home() / 'Sounds'}:{REPO_ROOT / 'data' / 'sfx'}",
    ).split(":")
    if p.strip()
]


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
