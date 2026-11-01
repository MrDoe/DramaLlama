"""Copy the application payload into the install prefix and write notices."""

from __future__ import annotations

import shutil
from pathlib import Path

from .paths import InstallPaths, payload_root
from .progress import Progress
from .util import InstallerError, ensure_dir, make_dir_link_or_copy, remove_tree, write_text

COPY_FILES = ("pyproject.toml", "README.md", "INSTALL.md", "NOTICE.md", "start-ui.sh")
COPY_DIRS = ("src", "vendor", "web/dist", "installer")


def _ignore_python(_directory: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name in {"__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache"}
        or name.endswith((".pyc", ".pyo", ".egg-info"))
    }


def _copy_path(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(
            source,
            destination,
            dirs_exist_ok=True,
            ignore=_ignore_python,
            symlinks=False,
        )
    elif source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def stage_payload(source_root: str | Path, destination: str | Path) -> Path:
    """Assemble the minimal runnable app tree (used by dev installs and builds)."""
    source = Path(source_root)
    dest = Path(destination)
    if not (source / "src" / "drama_studio").is_dir():
        raise InstallerError(f"not a Drama Llama checkout: {source}")
    if not (source / "web" / "dist" / "index.html").is_file():
        raise InstallerError(
            "web/dist/index.html is missing; run `cd web && npm run build` first"
        )
    remove_tree(dest)
    ensure_dir(dest)
    for name in COPY_DIRS:
        origin = source / name
        if origin.exists():
            _copy_path(origin, dest / name)
    for name in COPY_FILES:
        origin = source / name
        if origin.is_file():
            shutil.copy2(origin, dest / name)
    # Keep the data directory present and writable; project content stays out.
    ensure_dir(dest / "data")
    bridge = dest / "suno-bridge"
    ensure_dir(bridge)
    for name in ("server.mjs", "package.json", "README.md"):
        origin = source / "suno-bridge" / name
        if origin.is_file():
            shutil.copy2(origin, bridge / name)
    bettersuno = source / "third_party" / "bettersuno-mcp"
    if (bettersuno / "package.json").is_file():
        _copy_path(bettersuno, dest / "third_party" / "bettersuno-mcp")
        make_dir_link_or_copy(
            dest / "third_party" / "bettersuno-mcp",
            dest / "suno-bridge" / "node_modules" / "bettersuno-mcp",
        )
    return dest


def install_payload(
    paths: InstallPaths,
    progress: Progress,
    *,
    source_root: str | Path | None = None,
) -> Path:
    source = Path(source_root) if source_root else payload_root()
    if source.resolve() == paths.app.resolve():
        progress.log("running from the installed payload; nothing to copy")
        progress.update("payload", 1.0, "App payload already in place")
        return paths.app
    progress.update("payload", 0.05, "Copying the application payload…")
    staging = paths.prefix / ".app-new"
    stage_payload(source, staging)
    remove_tree(paths.app)
    staging.replace(paths.app)
    progress.update("payload", 1.0, "App payload ready")
    return paths.app


def write_notices(paths: InstallPaths, pins: dict) -> Path:
    text = f"""# Drama Llama third-party notices

Drama Llama downloads and bundles components with their own licences. This
file is a summary, not a replacement for the upstream licence texts.

- Drama Llama application code: see the upstream repository.
- uv {pins.get('uv', {}).get('version', '')} (Apache-2.0 / MIT), Astral.
- Node.js {pins.get('node', {}).get('version', '')} (MIT and bundled third-party licences).
- CPython {pins.get('python', {}).get('series', '')} (PSF licence), installed by uv.
- PyTorch / torchaudio {pins.get('torch', {}).get('version', '')}
  (BSD-style, plus NVIDIA redistributable CUDA libraries for CUDA wheels).
- faster-whisper and CTranslate2 (MIT / Apache-2.0).
- Whisper large-v3 model (MIT), Systran/faster-whisper-large-v3.
- Breeze TTS 2 code (Apache-2.0) and weights (research/non-commercial),
  BreezeBlue. The weights are only downloaded when the user selects Breeze
  English and accepts the licence.
- Breeze-TTS-2.cpp (MIT) and Articulatio-DE GGUF (research/non-commercial),
  only downloaded when Breeze German is selected.
- Chatterbox and chatterbox-tts-api (MIT and upstream model licences),
  pulled as a Docker image only when selected.
- BetterSuno MCP (MIT), bundled with its pure-JavaScript dependencies.
- imageio-ffmpeg {pins.get('ffmpeg', {}).get('pip_package', '')}: the Python
  package is BSD-2-Clause; the static ffmpeg binary it downloads is a GPL
  build from johnvansickle.com and is fetched by pip, not redistributed by
  the Drama Llama installer.
- PySide6 / Qt (LGPLv3) is used only by the installer wizard, not by the
  installed application.
"""
    target = paths.prefix / "NOTICE.md"
    write_text(target, text)
    return target
