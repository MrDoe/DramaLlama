"""Install layout and payload discovery for Linux and Windows."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .util import is_windows, read_json


def default_prefix(
    *,
    system: str | None = None,
    environ: dict[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Per-user install root. No administrator rights required."""
    env = dict(os.environ if environ is None else environ)
    override = env.get("DRAMA_LLAMA_PREFIX")
    if override:
        return Path(override).expanduser()
    user_home = Path(home or Path.home())
    sysname = system or os.name
    if is_windows(sysname):
        local = env.get("LOCALAPPDATA")
        base = Path(local) if local else user_home / "AppData" / "Local"
        return base / "DramaLlama"
    if str(sysname).lower() == "darwin":
        return user_home / "Library" / "Application Support" / "DramaLlama"
    xdg = env.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else user_home / ".local" / "share"
    return base / "drama-llama"


def resolve_prefix(explicit: str | Path | None = None) -> Path:
    """Resolve the install prefix, following a custom-location pointer.

    Inno Setup's uninstaller only knows the default prefix. When the wizard
    installs elsewhere it writes ``<default>/location.txt`` so an uninstall
    launched without ``--prefix`` still finds the real tree.
    """
    if explicit:
        return Path(explicit).expanduser()
    base = default_prefix()
    pointer = base / "location.txt"
    try:
        recorded = pointer.read_text(encoding="utf-8").strip()
    except OSError:
        recorded = ""
    return Path(recorded).expanduser() if recorded else base


@dataclass(frozen=True)
class InstallPaths:
    prefix: Path
    # ``None`` means "this host". Tests and cross-platform helpers pass
    # ``system="windows"`` to resolve Scripts/*.exe paths on any machine.
    system: str | None = None

    @classmethod
    def for_prefix(cls, prefix: str | Path, *, system: str | None = None) -> InstallPaths:
        return cls(prefix=Path(prefix).expanduser().resolve(), system=system)

    @property
    def app(self) -> Path:
        return self.prefix / "app"

    @property
    def data(self) -> Path:
        return self.prefix / "data"

    @property
    def runtime(self) -> Path:
        return self.prefix / "runtime"

    @property
    def venv(self) -> Path:
        return self.prefix / "venv"

    @property
    def logs(self) -> Path:
        return self.prefix / "logs"

    @property
    def bin(self) -> Path:
        return self.prefix / "bin"

    @property
    def cache(self) -> Path:
        return self.prefix / "cache"

    @property
    def state_file(self) -> Path:
        return self.prefix / "install-state.json"

    def windows(self) -> bool:
        return is_windows(self.system)

    @property
    def uv(self) -> Path:
        return self.runtime / "uv" / ("uv.exe" if self.windows() else "uv")

    @property
    def node_dir(self) -> Path:
        return self.runtime / "node"

    @property
    def node(self) -> Path:
        if self.windows():
            return self.node_dir / "node.exe"
        return self.node_dir / "bin" / "node"

    @property
    def npm(self) -> Path:
        if self.windows():
            return self.node_dir / "npm.cmd"
        return self.node_dir / "bin" / "npm"

    @property
    def ffmpeg(self) -> Path:
        return self.bin / ("ffmpeg.exe" if self.windows() else "ffmpeg")

    def python(self, *, system: str | None = None) -> Path:
        if is_windows(system or self.system):
            return self.venv / "Scripts" / "python.exe"
        return self.venv / "bin" / "python"

    def pythonw(self, *, system: str | None = None) -> Path:
        if is_windows(system or self.system):
            return self.venv / "Scripts" / "pythonw.exe"
        return self.python(system=system)

    def ensure(self) -> InstallPaths:
        for directory in (self.prefix, self.app, self.data, self.runtime, self.logs, self.bin):
            directory.mkdir(parents=True, exist_ok=True)
        return self

    def is_installed(self) -> bool:
        return (self.state_file).is_file() and (self.app / "pyproject.toml").is_file()


def legacy_breeze_repo(home: Path | None = None) -> Path:
    return Path(home or Path.home()) / "code" / "breeze-tts"


def legacy_breeze_model(home: Path | None = None) -> Path:
    return Path(home or Path.home()) / "models" / "Breeze-TTS-2"


def legacy_breeze_de_root(home: Path | None = None) -> Path:
    return Path(home or Path.home()) / "code" / "breeze-tts-cpp"


def legacy_breeze_de_binary(home: Path | None = None, *, windows: bool | None = None) -> Path:
    tag = "windows-x64-vulkan" if (is_windows() if windows is None else windows) else "linux-x64-vulkan"
    name = "breeze-server.exe" if (is_windows() if windows is None else windows) else "breeze-server"
    return legacy_breeze_de_root(home) / f"breeze-tts-2-v0.1.6-{tag}" / name


def legacy_breeze_de_model(home: Path | None = None) -> Path:
    return (
        Path(home or Path.home())
        / "models"
        / "Articulatio-DE-Stream-TTS-v1.5-Q8_0.gguf"
    )


def whisper_cache_root(home: Path | None = None) -> Path:
    """The exact cache layout ``vendor/breeze_stems.find_asr()`` expects.

    Deliberately independent of ``HF_HOME``: the vendored alignment code globs
    ``~/.cache/huggingface/hub/models--Systran--faster-whisper-large-v3/...``.
    """
    return Path(home or Path.home()) / ".cache" / "huggingface" / "hub"


def installer_dir() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return base / "installer"
    return Path(__file__).resolve().parents[1]


def pins_path() -> Path:
    candidates = [
        installer_dir() / "pins.json",
        Path(__file__).resolve().parents[1] / "pins.json",
    ]
    # Frozen wizard: the payload (and its installer/pins.json) lives inside
    # ``_MEIPASS/payload``; ``installer_dir()`` points at the PyInstaller
    # extraction root and does not contain the payload copy.
    try:
        candidates.append(payload_root() / "installer" / "pins.json")
    except (OSError, FileNotFoundError):
        pass
    candidates.append(Path(getattr(sys, "_MEIPASS", "")) / "installer" / "pins.json")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def load_pins() -> dict:
    path = pins_path()
    data = read_json(path, None)
    if not isinstance(data, dict):
        raise FileNotFoundError(f"installer pins not found: {path}")
    return data


def payload_root() -> Path:
    """Locate the directory containing ``src/`` and ``web/dist``.

    Dev checkout: the repository root. Frozen wizard (PyInstaller/AppImage):
    the bundled ``payload`` directory, either beside the executable or inside
    the PyInstaller extraction directory.
    """
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        bundle = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        candidates.extend(
            [
                bundle / "payload",
                Path(sys.executable).resolve().parent / "payload",
                Path(sys.executable).resolve().parent / "_internal" / "payload",
            ]
        )
    core_file = Path(__file__).resolve()
    candidates.extend([core_file.parents[2], core_file.parents[1]])
    for candidate in candidates:
        if (candidate / "src" / "drama_studio").is_dir() and (
            candidate / "pyproject.toml"
        ).is_file():
            return candidate
    raise FileNotFoundError(
        "Drama Llama payload not found (expected src/drama_studio and pyproject.toml)"
    )


def runtime_search_path(paths: InstallPaths, *, system: str | None = None) -> list[Path]:
    sep = "windows" if is_windows(system) else "posix"
    if sep == "windows":
        return [paths.bin, paths.node_dir, paths.venv / "Scripts"]
    return [paths.bin, paths.node_dir / "bin", paths.venv / "bin"]
