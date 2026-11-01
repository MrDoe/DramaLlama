"""Portable runtimes: uv, managed Python 3.12, virtualenv, Node 22 and ffmpeg."""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

from .detect import SystemInfo
from .download import download
from .paths import InstallPaths
from .progress import Progress
from .util import (
    InstallerError,
    command_output,
    ensure_dir,
    extract_archive,
    find_file,
    human_bytes,
    is_windows,
    remove_tree,
    run,
    run_streaming,
    temp_dir,
)


def _uv_env(paths: InstallPaths) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "UV_CACHE_DIR": str(paths.cache / "uv"),
            "UV_PYTHON_INSTALL_DIR": str(paths.runtime / "python"),
            "UV_LINK_MODE": "copy",
            "UV_HTTP_TIMEOUT": "120",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    env.pop("VIRTUAL_ENV", None)
    return env


def uv_version(uv: Path) -> str | None:
    if not uv.is_file():
        return None
    try:
        text = command_output([uv, "--version"], timeout=15)
    except (OSError, InstallerError):
        return None
    return text.replace("uv ", "").strip() or None


def ensure_uv(paths: InstallPaths, pins: dict, system: SystemInfo, progress: Progress) -> Path:
    asset = pins["uv"]["assets"].get(system.platform_key)
    if not asset:
        raise InstallerError(f"no uv build pinned for {system.platform_key}")
    expected = str(pins["uv"]["version"])
    if uv_version(paths.uv) == expected:
        progress.log(f"uv {expected} already installed")
        return paths.uv
    progress.update("runtimes", 0.05, f"Downloading uv {expected}…")
    archive = paths.cache / "downloads" / Path(str(asset["url"])).name
    download(
        str(asset["url"]),
        archive,
        sha256=str(asset.get("sha256") or None),
        size=asset.get("size"),
        label=f"uv {expected}",
        progress=lambda item: progress.download("runtimes", item),
        cancel=progress.cancelled,
    )
    progress.update("runtimes", 0.15, "Extracting uv…")
    staging = temp_dir("drama-uv-")
    try:
        extract_archive(archive, staging)
        binary = find_file(staging, "uv.exe" if is_windows(system.os_name) else "uv")
        if binary is None:
            raise InstallerError("uv archive did not contain the uv executable")
        install_dir = paths.runtime / "uv"
        remove_tree(install_dir)
        install_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(binary, install_dir / binary.name)
        if binary.name != "uv.exe":
            uvx = find_file(staging, "uvx")
            if uvx is not None:
                shutil.copy2(uvx, install_dir / "uvx")
        if not is_windows(system.os_name):
            (install_dir / binary.name).chmod(0o755)
            if (install_dir / "uvx").exists():
                (install_dir / "uvx").chmod(0o755)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    actual = uv_version(paths.uv)
    if actual is None:
        raise InstallerError("uv was installed but does not run")
    progress.log(f"uv {actual} installed")
    return paths.uv


def ensure_python(paths: InstallPaths, pins: dict, system: SystemInfo, progress: Progress) -> Path:
    series = str(pins.get("python", {}).get("series", "3.12"))
    env = _uv_env(paths)
    progress.update("runtimes", 0.35, f"Installing Python {series} (managed by uv)…")
    run_streaming(
        [paths.uv, "python", "install", series],
        env=env,
        timeout=900,
        on_line=progress.log,
    )
    found = command_output(
        [paths.uv, "python", "find", series],
        env=env,
        timeout=120,
    )
    if not found:
        raise InstallerError(f"uv could not install Python {series}")
    progress.log(f"Python {series}: {found}")
    return Path(found)


def ensure_venv(
    paths: InstallPaths,
    pins: dict,
    system: SystemInfo,
    progress: Progress,
) -> Path:
    series = str(pins.get("python", {}).get("series", "3.12"))
    python = paths.python(system=system.os_name)
    if python.is_file():
        version = command_output(
            [python, "-c", "import sys; print('.'.join(map(str, sys.version_info[:2])))"],
            timeout=30,
        )
        if version == series:
            progress.log(f"virtualenv already uses Python {version}")
            return python
        remove_tree(paths.venv)
    progress.update("runtimes", 0.55, f"Creating the Python {series} virtualenv…")
    env = _uv_env(paths)
    result = run_streaming(
        [
            paths.uv,
            "venv",
            "--python",
            series,
            "--python-preference",
            "only-managed",
            str(paths.venv),
        ],
        env=env,
        timeout=600,
        on_line=progress.log,
    )
    if result.returncode != 0 or not python.is_file():
        raise InstallerError("could not create the Drama Llama virtualenv")
    progress.log(f"virtualenv ready at {paths.venv}")
    return python


def node_version(node: Path) -> str | None:
    if not node.is_file():
        return None
    try:
        return command_output([node, "--version"], timeout=15).strip() or None
    except (OSError, InstallerError):
        return None


def ensure_node(paths: InstallPaths, pins: dict, system: SystemInfo, progress: Progress) -> Path:
    asset = pins["node"]["assets"].get(system.platform_key)
    if not asset:
        raise InstallerError(f"no Node build pinned for {system.platform_key}")
    expected = f"v{pins['node']['version']}"
    if node_version(paths.node) == expected:
        progress.log(f"Node {expected} already installed")
        return paths.node
    progress.update("runtimes", 0.7, f"Downloading Node {expected}…")
    archive = paths.cache / "downloads" / Path(str(asset["url"])).name
    download(
        str(asset["url"]),
        archive,
        sha256=str(asset.get("sha256") or None),
        size=asset.get("size"),
        label=f"Node {expected}",
        progress=lambda item: progress.download("runtimes", item),
        cancel=progress.cancelled,
    )
    progress.update("runtimes", 0.85, "Extracting Node…")
    staging = temp_dir("drama-node-")
    try:
        extract_archive(archive, staging)
        marker = "node.exe" if is_windows(system.os_name) else "node"
        binary = find_file(staging, marker)
        if binary is None:
            raise InstallerError("Node archive did not contain the node executable")
        root = binary.parent if is_windows(system.os_name) else binary.parent.parent
        remove_tree(paths.node_dir)
        paths.node_dir.mkdir(parents=True, exist_ok=True)
        for child in root.iterdir():
            destination = paths.node_dir / child.name
            if child.is_dir():
                shutil.copytree(child, destination, dirs_exist_ok=True)
            else:
                shutil.copy2(child, destination)
        if not is_windows(system.os_name):
            for candidate in (paths.node_dir / "bin").glob("*"):
                if candidate.is_file():
                    candidate.chmod(candidate.stat().st_mode | stat.S_IXUSR)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    actual = node_version(paths.node)
    if actual != expected:
        raise InstallerError(f"Node install failed (found {actual or 'nothing'})")
    progress.log(f"Node {actual} installed")
    return paths.node


def ensure_ffmpeg(
    paths: InstallPaths,
    system: SystemInfo,
    progress: Progress,
) -> Path:
    """Expose imageio-ffmpeg's static build as ``<prefix>/bin/ffmpeg``.

    The wheel is installed with the audio stack, so no separate download and
    no GPL binary is redistributed by the installer itself.
    """
    progress.update("ffmpeg", 0.1, "Locating the bundled ffmpeg…")
    python = paths.python(system=system.os_name)
    try:
        source = command_output(
            [
                python,
                "-c",
                "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())",
            ],
            timeout=120,
        )
    except InstallerError as exc:
        raise InstallerError(
            "imageio-ffmpeg is missing from the virtualenv; "
            "the audio stack did not install correctly"
        ) from exc
    source_path = Path(source)
    if not source_path.is_file():
        raise InstallerError(f"imageio-ffmpeg returned a missing binary: {source}")
    ensure_dir(paths.bin)
    shutil.copy2(source_path, paths.ffmpeg)
    if not is_windows(system.os_name):
        paths.ffmpeg.chmod(0o755)
    version = command_output([paths.ffmpeg, "-version"], timeout=30)
    if "ffmpeg version" not in version:
        raise InstallerError("the bundled ffmpeg does not run")
    progress.update("ffmpeg", 1.0, f"ffmpeg ready ({human_bytes(paths.ffmpeg.stat().st_size)})")
    return paths.ffmpeg


def uv_pip_install(
    paths: InstallPaths,
    pins: dict,
    args: list[str],
    *,
    progress: Progress,
    index_url: str | None = None,
    extra_index_url: str | None = None,
    timeout: float = 3600,
    system: SystemInfo | None = None,
) -> None:
    env = _uv_env(paths)
    command: list[str | Path] = [
        paths.uv,
        "pip",
        "install",
        "--python",
        paths.python(system=system.os_name if system else None),
    ]
    if index_url:
        command += ["--index-url", index_url]
    if extra_index_url:
        command += ["--extra-index-url", extra_index_url]
    command += args
    result = run_streaming(command, env=env, timeout=timeout, on_line=progress.log)
    if result.returncode != 0:
        raise InstallerError(
            "dependency installation failed; see the install log for the "
            "full uv output"
        )


def uv_pip_check(
    paths: InstallPaths,
    progress: Progress,
    *,
    system: SystemInfo | None = None,
) -> bool:
    result = run(
        [
            paths.uv,
            "pip",
            "check",
            "--python",
            paths.python(system=system.os_name if system else None),
        ],
        env=_uv_env(paths),
        timeout=300,
    )
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    if result.returncode != 0:
        progress.log(f"warning: pip check reported issues:\n{output}")
        return False
    progress.log("pip check: no broken requirements")
    return True


def ensure_runtimes(
    paths: InstallPaths,
    pins: dict,
    system: SystemInfo,
    progress: Progress,
) -> None:
    ensure_uv(paths, pins, system, progress)
    ensure_python(paths, pins, system, progress)
    ensure_venv(paths, pins, system, progress)
    ensure_node(paths, pins, system, progress)
    progress.update("runtimes", 1.0, "Python, uv and Node are ready")
