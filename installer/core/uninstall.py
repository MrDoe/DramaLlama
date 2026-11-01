"""Uninstall: shortcuts, Docker images, the prefix and (optionally) models."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .engines import uninstall_chatterbox
from .paths import (
    InstallPaths,
)
from .progress import Progress
from .state import clear_location_pointer, load_state
from .util import InstallerError, human_bytes, remove_tree


def _remove_file(path: Path) -> bool:
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
            return True
        if path.is_dir():
            shutil.rmtree(path)
            return True
    except OSError as exc:
        raise InstallerError(f"could not remove {path}: {exc}") from exc
    return False


def _prune_empty(path: Path) -> None:
    try:
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    except OSError:
        pass


def _remove_prefix(paths: InstallPaths, *, keep_data: bool) -> list[Path]:
    removed: list[Path] = []
    if not paths.prefix.exists():
        return removed
    if not keep_data:
        remove_tree(paths.prefix)
        removed.append(paths.prefix)
        return removed
    for child in list(paths.prefix.iterdir()):
        if child.name == "data":
            continue
        remove_tree(child)
        removed.append(child)
    return removed


def _purge_models(paths: InstallPaths, state: dict, progress: Progress) -> list[Path]:
    """Delete only assets the installer downloaded or created.

    ``created_paths`` is recorded while installing (Breeze repo checkout,
    checkpoints, the Whisper snapshot). Anything the user supplied before the
    installer ran is deliberately left alone — deleting a shared Breeze or
    Whisper cache would break sibling checkouts.
    """
    removed: list[Path] = []
    candidates: list[Path] = []
    seen: set[str] = set()
    for entry in state.get("created_paths", []):
        candidate = Path(str(entry))
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)
    # Remove parents before children so a nested entry is not visited twice.
    candidates.sort(key=lambda path: len(path.parts))
    for candidate in candidates:
        try:
            inside_prefix = paths.prefix in candidate.parents or candidate == paths.prefix
        except OSError:
            inside_prefix = False
        if inside_prefix:
            continue
        if candidate.exists():
            size = _tree_size(candidate)
            remove_tree(candidate)
            removed.append(candidate)
            progress.log(f"removed {candidate} ({human_bytes(size)})")
    return removed


def _tree_size(path: Path) -> int:
    total = 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def uninstall(
    paths: InstallPaths,
    *,
    keep_data: bool = True,
    purge_models: bool = False,
    progress: Progress | None = None,
    remove_shortcuts: bool = True,
) -> list[str]:
    progress = progress or Progress()
    state = load_state(paths) or {}
    removed: list[str] = []
    progress.update("uninstall", 0.05, "Reading the install state…")

    if remove_shortcuts:
        for entry in state.get("shortcuts", []):
            shortcut = Path(str(entry))
            try:
                if _remove_file(shortcut):
                    removed.append(str(shortcut))
            except InstallerError as exc:
                progress.log(f"warning: {exc}")
        home = Path.home()
        _prune_empty(home / ".local" / "bin")
        _prune_empty(home / ".local" / "share" / "applications")

    if "chatterbox" in state.get("components", {}):
        progress.update("uninstall", 0.15, "Removing the Chatterbox container…")
        try:
            from .paths import load_pins

            uninstall_chatterbox(
                load_pins(), purge_images=purge_models, progress=progress
            )
        except (InstallerError, OSError) as exc:
            progress.log(f"warning: could not remove Chatterbox: {exc}")

    progress.update("uninstall", 0.3, "Removing the application…")
    for path in _remove_prefix(paths, keep_data=keep_data):
        removed.append(str(path))
    if purge_models:
        removed.extend(str(path) for path in _purge_models(paths, state, progress))
    clear_location_pointer()
    progress.update("uninstall", 1.0, "Uninstall complete")
    return removed


def uninstall_summary(removed: list[str], *, keep_data: bool, data: Path) -> str:
    lines = [f"Removed {len(removed)} item(s)."]
    if keep_data:
        lines.append(f"Projects and settings kept in {data}.")
    else:
        lines.append("Projects and settings were removed as well.")
    return "\n".join(lines)
