from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _env(name: str, default: str) -> str:
    """Read DRAMA_LLAMA_<name>, falling back to the legacy DRAMA_STUDIO_<name>."""
    return os.environ.get(f"DRAMA_LLAMA_{name}") or os.environ.get(
        f"DRAMA_STUDIO_{name}", default
    )


DATA_DIR = Path(_env("DATA", str(REPO_ROOT / "data"))).expanduser()
PROJECTS_DIR = DATA_DIR / "projects"
DB_PATH = DATA_DIR / "jobs.sqlite3"
WEB_DIST = REPO_ROOT / "web" / "dist"

HOST = _env("HOST", "127.0.0.1")
PORT = int(_env("PORT", "8770"))

# Ollama / local LLM defaults. The app never runs the LLM while Breeze is loaded.
# Any Ollama-compatible endpoint works here, including hosted/cloud offerings
# (e.g. Ollama Cloud) when combined with DRAMA_LLAMA_LLM_API_KEY.
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
LLM_URL = _env("LLM_URL", OLLAMA_URL)
LLM_MODEL = _env("LLM", "gemma4:12b-16k")
LLM_API_KEY = _env("LLM_API_KEY", "")
# LLM wire protocol: "ollama" (local Ollama or Ollama-compatible endpoint) or
# "openai" (OpenAI Chat Completions compatible — e.g. OpenCode Go at
# https://opencode.ai/inference/go/openai/v1/chat/completions).
LLM_API = _env("LLM_API", "ollama").lower()
LLM_KEEP_ALIVE = _env("LLM_KEEP_ALIVE", "0")
SUNO_BRIDGE_URL = os.environ.get("SUNO_BRIDGE_URL", "http://127.0.0.1:9430")
CHATTERBOX_URL = os.environ.get("CHATTERBOX_URL", "http://127.0.0.1:4123")
BREEZE_DE_URL = os.environ.get("BREEZE_DE_URL", "http://127.0.0.1:8137")

# Local SFX library roots. Personal subfolders can be excluded in settings.
SFX_LIBRARY_ROOTS = [
    Path(p).expanduser()
    for p in _env(
        "SFX_ROOTS",
        f"{Path.home() / 'Sounds'}:{REPO_ROOT / 'data' / 'sfx'}",
    ).split(":")
    if p.strip()
]


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
