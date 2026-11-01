"""Persistent install state, installer version and uninstall bookkeeping."""

from __future__ import annotations

from pathlib import Path

from .paths import InstallPaths, default_prefix, resolve_prefix
from .util import read_json, utc_now, write_json

STATE_SCHEMA = 1


def new_state(
    *,
    app_version: str,
    installer_version: str,
    prefix: InstallPaths,
    platform: str,
    device: str,
    components: list[str],
) -> dict:
    now = utc_now()
    return {
        "schema": STATE_SCHEMA,
        "app_version": app_version,
        "installer_version": installer_version,
        "created_at": now,
        "updated_at": now,
        "prefix": str(prefix.prefix),
        "data_dir": str(prefix.data),
        "platform": platform,
        "device": device,
        "components": {name: {"status": "pending"} for name in components},
        "created_paths": [],
        "shortcuts": [],
        "docker": {},
        "breeze_de_cpu": False,
        "pending_reboot": False,
        "pending_reason": "",
        "completed": False,
    }


def load_state(prefix: str | Path | InstallPaths) -> dict | None:
    paths = prefix if isinstance(prefix, InstallPaths) else InstallPaths.for_prefix(prefix)
    data = read_json(paths.state_file, None)
    return data if isinstance(data, dict) else None


def save_state(prefix: str | Path | InstallPaths, state: dict) -> Path:
    paths = prefix if isinstance(prefix, InstallPaths) else InstallPaths.for_prefix(prefix)
    state["updated_at"] = utc_now()
    return write_json(paths.state_file, state)


def set_component(
    state: dict,
    name: str,
    status: str,
    *,
    message: str = "",
    **details: object,
) -> None:
    component = state.setdefault("components", {}).setdefault(name, {})
    component.update(details)
    component["status"] = status
    component["message"] = message
    component["updated_at"] = utc_now()
    if status == "pending_reboot":
        state["pending_reboot"] = True
        state["pending_reason"] = message or "A reboot or re-login is required."


def component_status(state: dict, name: str) -> str:
    return str(state.get("components", {}).get(name, {}).get("status", "missing"))


def add_created_path(state: dict, path: str | Path) -> None:
    value = str(path)
    paths = state.setdefault("created_paths", [])
    if value not in paths:
        paths.append(value)


def add_shortcut(state: dict, path: str | Path) -> None:
    value = str(path)
    shortcuts = state.setdefault("shortcuts", [])
    if value not in shortcuts:
        shortcuts.append(value)


def record_docker(state: dict, **details: object) -> None:
    state.setdefault("docker", {}).update(details)


def write_location_pointer(actual: str | Path) -> Path | None:
    """Let a later default-prefix uninstall find a custom install location."""
    target = default_prefix()
    pointer = target / "location.txt"
    try:
        if Path(actual).resolve() == target.resolve():
            pointer.unlink(missing_ok=True)
            return None
        pointer.parent.mkdir(parents=True, exist_ok=True)
        pointer.write_text(str(Path(actual).resolve()) + "\n", encoding="utf-8")
        return pointer
    except OSError:
        return None


def clear_location_pointer() -> None:
    try:
        (default_prefix() / "location.txt").unlink(missing_ok=True)
    except OSError:
        pass


def installed_prefix(explicit: str | Path | None = None) -> InstallPaths:
    return InstallPaths.for_prefix(resolve_prefix(explicit))
