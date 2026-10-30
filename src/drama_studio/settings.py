"""Layered runtime settings persisted to ``data/settings.json``.

Precedence per key: **settings file > environment > built-in default**.
The GUI writes the file; env vars keep working for every key the file does
not pin, so a fresh install behaves exactly like before. ``GET /api/config``
reports which layer each effective value came from.
"""

from __future__ import annotations

import json
import os
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel

from .config import DATA_DIR

SETTINGS_PATH = DATA_DIR / "settings.json"

LlmApi = Literal["ollama", "openai"]
_VALID_APIS = ("ollama", "openai")


# Env names consulted per key (first hit wins). The url key also honors the
# legacy bare OLLAMA_URL, mirroring config.py.
_ENV_NAMES = {
    "api": ("DRAMA_LLAMA_LLM_API", "DRAMA_STUDIO_LLM_API"),
    "url": ("DRAMA_LLAMA_LLM_URL", "DRAMA_STUDIO_LLM_URL", "OLLAMA_URL"),
    "model": ("DRAMA_LLAMA_LLM", "DRAMA_STUDIO_LLM"),
    "api_key": ("DRAMA_LLAMA_LLM_API_KEY", "DRAMA_STUDIO_LLM_API_KEY"),
}

_DEFAULTS = {
    "api": "ollama",
    "url": "http://127.0.0.1:11434",
    "model": "gemma4:12b-16k",
    "api_key": "",
}


def _env_value(key: str) -> str | None:
    for name in _ENV_NAMES[key]:
        value = os.environ.get(name)
        if value:
            return value
    return None


class LlmSettings(BaseModel):
    api: LlmApi = "ollama"
    url: str = ""
    model: str = ""
    api_key: str = ""


SunoPlan = Literal["free", "pro"]
_VALID_PLANS = ("free", "pro")
# Advanced generation knobs (style/weirdness/audio influence) and voice
# personas are paid-plan features on Suno's side; the plan decides whether
# they are sent at all.
_PLAN_DEFAULT: SunoPlan = "free"


class SunoSettings(BaseModel):
    plan: SunoPlan = "free"


def resolve_suno() -> tuple[SunoSettings, dict[str, str]]:
    """Effective Suno settings (plan) plus per-key source."""
    try:
        raw = json.loads(SETTINGS_PATH.read_text())
        section = raw.get("suno") if isinstance(raw, dict) else None
    except (OSError, ValueError):
        section = None
    section = section if isinstance(section, dict) else {}
    plan = section.get("plan")
    if isinstance(plan, str) and plan.strip().lower() in _VALID_PLANS:
        return SunoSettings(plan=plan.strip().lower()), {"plan": "file"}
    env_plan = os.environ.get("DRAMA_LLAMA_SUNO_PLAN") or os.environ.get(
        "DRAMA_STUDIO_SUNO_PLAN"
    )
    if env_plan and env_plan.strip().lower() in _VALID_PLANS:
        return SunoSettings(plan=env_plan.strip().lower()), {"plan": "env"}
    return SunoSettings(plan=_PLAN_DEFAULT), {"plan": "default"}


def write_suno(patch: dict[str, Any]) -> tuple[SunoSettings, dict[str, str]]:
    unknown = set(patch) - {"plan"}
    if unknown:
        raise ValueError(f"unknown Suno settings: {sorted(unknown)}")
    plan = patch.get("plan")
    if plan is not None and str(plan).strip().lower() not in _VALID_PLANS:
        raise ValueError("Suno plan must be 'free' or 'pro'")
    try:
        raw = json.loads(SETTINGS_PATH.read_text())
        if not isinstance(raw, dict):
            raw = {}
    except (OSError, ValueError):
        raw = {}
    section = raw.get("suno")
    if not isinstance(section, dict):
        section = {}
    if plan is not None:
        section["plan"] = str(plan).strip().lower()
    raw["suno"] = section
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(raw, indent=2) + "\n")
    return resolve_suno()


def _file_llm() -> dict[str, Any]:
    try:
        raw = json.loads(SETTINGS_PATH.read_text())
    except (OSError, ValueError):
        return {}
    llm = raw.get("llm") if isinstance(raw, dict) else None
    return llm if isinstance(llm, dict) else {}


def _valid_file_value(key: str, value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if key == "api" and text.lower() not in _VALID_APIS:
        return None
    return text.lower() if key == "api" else text


def resolve_llm() -> tuple[LlmSettings, dict[str, str]]:
    """Effective LLM settings plus per-key source (``file``/``env``/``default``).

    Invalid file values are ignored (env/default win) so a hand-edited file
    can never wedge the app; strict validation happens on write.
    """
    file_llm = _file_llm()
    values: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for key in ("api", "url", "model", "api_key"):
        valid = _valid_file_value(key, file_llm.get(key))
        if valid is not None:
            values[key] = valid
            sources[key] = "file"
            continue
        env_value = _env_value(key)
        if env_value is not None:
            values[key] = (
                env_value.strip().lower() if key == "api" else env_value
            )
            sources[key] = "env"
        else:
            values[key] = _DEFAULTS[key]
            sources[key] = "default"
    return LlmSettings(**values), sources


def _check_patch(patch: dict[str, Any]) -> dict[str, str]:
    allowed = {"api", "url", "model", "api_key"}
    unknown = set(patch) - allowed
    if unknown:
        raise ValueError(f"unknown LLM settings: {sorted(unknown)}")
    clean: dict[str, str] = {}
    for key, value in patch.items():
        if value is None:
            continue  # absent/null: keep the stored value
        if not isinstance(value, str):
            raise ValueError(f"LLM {key} must be a string")
        if key == "api":
            normalized = value.strip().lower()
            if normalized not in _VALID_APIS:
                raise ValueError("LLM api must be 'ollama' or 'openai'")
            clean[key] = normalized
        elif key == "api_key":
            clean[key] = value  # "" clears; caller strips file key
        else:
            if not value.strip():
                raise ValueError(f"LLM {key} must be a non-empty string")
            if key == "url":
                parsed = urlparse(value.strip())
                if parsed.scheme not in ("http", "https") or not parsed.hostname:
                    raise ValueError("LLM url must be an http(s) URL")
            clean[key] = value.strip() if key == "url" else value
    return clean


def preview_llm(patch: dict[str, Any]) -> LlmSettings:
    """Effective settings with an unpersisted patch applied (for testing)."""
    settings, _ = resolve_llm()
    data = settings.model_dump()
    for key, value in _check_patch(patch).items():
        data[key] = "" if (key == "api_key" and value == "") else value
    return LlmSettings(**data)


def write_llm(patch: dict[str, Any]) -> tuple[LlmSettings, dict[str, str]]:
    """Merge a validated partial update into the settings file."""
    clean = _check_patch(patch)
    try:
        raw = json.loads(SETTINGS_PATH.read_text())
        if not isinstance(raw, dict):
            raw = {}
    except (OSError, ValueError):
        raw = {}
    file_llm = raw.get("llm")
    if not isinstance(file_llm, dict):
        file_llm = {}
    for key, value in clean.items():
        if key == "api_key" and value == "":
            file_llm.pop("api_key", None)
        else:
            file_llm[key] = value
    raw["llm"] = file_llm
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(raw, indent=2) + "\n")
    return resolve_llm()
