"""Small, dependency-free helpers shared by the installer core.

Everything in this module has to work on a bare Python installation: the
installer downloads and creates the runtime it later uses, so it cannot import
third-party packages itself.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import zipfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path

CHUNK = 1024 * 1024


class InstallerError(RuntimeError):
    """A user-facing installation failure."""


class DownloadError(InstallerError):
    """A download failed after all retries."""


class Cancelled(InstallerError):
    """The user cancelled the installation."""


def is_windows(system: str | None = None) -> bool:
    return (system or os.name) in {"windows", "nt", "win32"}


def platform_key(system: str | None = None, machine: str | None = None) -> str:
    """Return the pin key for this OS/arch pair."""
    sysname = (system or os.name).lower()
    if sysname in {"windows", "nt", "win32"}:
        sysname = "windows"
    elif sysname == "darwin":
        sysname = "macos"
    else:
        sysname = "linux"
    arch = (machine or os.uname().machine if hasattr(os, "uname") else "").lower()
    machine_value = (machine or os.environ.get("PROCESSOR_ARCHITECTURE", "")).lower()
    if not arch and machine_value:
        arch = "x86_64" if "64" in machine_value else machine_value
    if arch in {"amd64", "x64", "x86_64"}:
        arch = "x86_64"
    elif arch in {"aarch64", "arm64"}:
        arch = "arm64"
    return f"{sysname}-{arch or 'x86_64'}"


def human_bytes(value: float) -> str:
    value = float(max(0, value))
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    index = 0
    while value >= 1024 and index < len(units) - 1:
        value /= 1024
        index += 1
    if index == 0:
        return f"{int(value)} B"
    return f"{value:.1f} {units[index]}"


def human_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, rest = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {rest:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def sha256_file(
    path: str | Path,
    *,
    chunk: int = CHUNK,
    cancel: Callable[[], bool] | None = None,
) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            if cancel is not None and cancel():
                raise Cancelled("cancelled")
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def verify_file(
    path: str | Path,
    *,
    size: int | None = None,
    sha256: str | None = None,
    cancel: Callable[[], bool] | None = None,
) -> bool:
    """Return True when an existing file matches the pin (size and/or hash)."""
    target = Path(path)
    try:
        if not target.is_file():
            return False
        if size is not None and target.stat().st_size != int(size):
            return False
        if sha256 and sha256_file(target, cancel=cancel).lower() != sha256.lower():  # noqa: SIM103
            return False
        return True
    except OSError:
        return False


def read_json(path: str | Path, default: object = None) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: str | Path, value: object) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".tmp")
    temp.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    temp.replace(target)
    return target


def write_text(path: str | Path, value: str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".tmp")
    temp.write_text(value, encoding="utf-8", newline="\n")
    temp.replace(target)
    return target


def split_paths(value: str, sep: str | None = None) -> list[Path]:
    """Split a path list with the platform separator.

    ``sep`` is exposed for tests that need to simulate Windows drive letters
    while running on Linux/macOS.
    """
    return [
        Path(item).expanduser()
        for item in value.split(sep if sep is not None else os.pathsep)
        if item.strip()
    ]


def join_paths(paths: Iterable[str | Path], sep: str | None = None) -> str:
    return (sep if sep is not None else os.pathsep).join(str(path) for path in paths)


def app_version(root: str | Path | None = None) -> str:
    """Read the app version from ``src/drama_studio/__init__.py``."""
    root_path = Path(root) if root else Path(__file__).resolve().parents[2]
    init = root_path / "src" / "drama_studio" / "__init__.py"
    try:
        text = init.read_text(encoding="utf-8")
    except OSError:
        return "0.0.0"
    match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
    return match.group(1) if match else "0.0.0"


def _safe_member_path(destination: Path, name: str) -> Path:
    candidate = (destination / name).resolve()
    base = destination.resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise InstallerError(f"archive member escapes destination: {name!r}") from exc
    return candidate


def safe_extract_tar(archive: str | Path, destination: str | Path) -> Path:
    """Extract a tar archive without allowing path traversal or absolute links."""
    dest = Path(destination)
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:*") as bundle:
        for member in bundle.getmembers():
            target = _safe_member_path(dest, member.name)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if member.issym() or member.islnk():
                link_target = Path(member.linkname)
                resolved = (
                    link_target if link_target.is_absolute() else (target.parent / link_target)
                ).resolve()
                try:
                    resolved.relative_to(dest.resolve())
                except ValueError as exc:
                    raise InstallerError(
                        f"archive symlink escapes destination: {member.name!r}"
                    ) from exc
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists() or target.is_symlink():
                    target.unlink()
                os.symlink(member.linkname, target)
                continue
            if not member.isfile():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = bundle.extractfile(member)
            if source is None:
                continue
            with source, open(target, "wb") as handle:
                shutil.copyfileobj(source, handle, CHUNK)
            mode = member.mode & 0o777
            if mode:
                try:
                    target.chmod(mode)
                except OSError:
                    pass
    return dest


def safe_extract_zip(archive: str | Path, destination: str | Path) -> Path:
    dest = Path(destination)
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            target = _safe_member_path(dest, info.filename)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as source, open(target, "wb") as handle:
                shutil.copyfileobj(source, handle, CHUNK)
            mode = (info.external_attr >> 16) & 0o777
            if mode:
                try:
                    target.chmod(mode)
                except OSError:
                    pass
    return dest


def extract_archive(archive: str | Path, destination: str | Path) -> Path:
    name = str(archive).lower()
    if name.endswith((".zip",)):
        return safe_extract_zip(archive, destination)
    return safe_extract_tar(archive, destination)


def find_file(root: str | Path, names: str | Sequence[str]) -> Path | None:
    wanted = {names} if isinstance(names, str) else set(names)
    for path in sorted(Path(root).rglob("*")):
        if path.is_file() and path.name in wanted:
            return path
    return None


def find_dir(root: str | Path, name: str) -> Path | None:
    for path in sorted(Path(root).rglob(name)):
        if path.is_dir():
            return path
    return None


def which(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def run(
    command: Sequence[str | Path],
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    check: bool = False,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    args = [str(item) for item in command]
    kwargs: dict[str, object] = {
        "cwd": str(cwd) if cwd else None,
        "env": dict(env) if env is not None else None,
        "timeout": timeout,
        "text": True,
    }
    if capture:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    result = subprocess.run(args, **kwargs)
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise InstallerError(
            f"command failed ({result.returncode}): {' '.join(args)}"
            + (f"\n{detail}" if detail else "")
        )
    return result


def run_streaming(
    command: Sequence[str | Path],
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    on_line: Callable[[str], None] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command and forward combined output line by line."""
    args = [str(item) for item in command]
    process = subprocess.Popen(
        args,
        cwd=str(cwd) if cwd else None,
        env=dict(env) if env is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    lines: list[str] = []
    started = time.monotonic()
    assert process.stdout is not None
    try:
        for line in process.stdout:
            clean = line.rstrip("\r\n")
            if clean:
                lines.append(clean)
                if on_line is not None:
                    on_line(clean)
            if timeout is not None and time.monotonic() - started > timeout:
                process.kill()
                raise InstallerError(
                    f"command timed out after {timeout:.0f}s: {' '.join(args)}"
                )
        returncode = process.wait(timeout=timeout)
    finally:
        if process.poll() is None:
            process.kill()
    return subprocess.CompletedProcess(args, returncode, "\n".join(lines), "")


def command_output(command: Sequence[str | Path], **kwargs: object) -> str:
    result = run(command, **kwargs)
    return (result.stdout or "").strip()


def ensure_dir(path: str | Path) -> Path:
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    return target


def remove_tree(path: str | Path) -> None:
    target = Path(path)
    # ``Path.is_junction`` exists on 3.12. A Windows junction is not a
    # symlink, but rmtree would follow it and delete the *link target* (the
    # bundled bettersuno-mcp tree), so remove the junction itself.
    if target.is_symlink() or target.is_file():
        target.unlink(missing_ok=True)
    elif hasattr(target, "is_junction") and target.is_junction():
        os.rmdir(target)
    elif target.exists():
        shutil.rmtree(target, ignore_errors=False)


def temp_dir(prefix: str = "drama-llama-") -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))


def same_path(left: str | Path, right: str | Path) -> bool:
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:
        return os.path.normcase(os.path.abspath(str(left))) == os.path.normcase(
            os.path.abspath(str(right))
        )


def copy_tree(
    source: str | Path,
    destination: str | Path,
    *,
    ignore: Callable[[str, list[str]], set[str]] | None = None,
) -> Path:
    shutil.copytree(
        str(source),
        str(destination),
        dirs_exist_ok=True,
        ignore=ignore,
        symlinks=False,
    )
    return Path(destination)


def make_dir_link_or_copy(source: str | Path, destination: str | Path) -> Path:
    """Expose a directory under another path without duplicating it when possible.

    The Suno bridge resolves ``bettersuno-mcp`` from its own
    ``node_modules``. The AppImage/zip payload cannot carry the source
    repository's relative symlink, so create a real symlink/junction or fall
    back to a copy.
    """
    src = Path(source).resolve()
    dest = Path(destination)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() or dest.is_symlink():
        remove_tree(dest)
    if is_windows():
        junction = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(dest), str(src)],
            capture_output=True,
            text=True,
        )
        if junction.returncode == 0:
            return dest
    else:
        try:
            os.symlink(src, dest, target_is_directory=True)
            return dest
        except OSError:
            pass
    shutil.copytree(src, dest)
    return dest


def unique(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def utc_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).replace(microsecond=0).isoformat()


def tail(path: str | Path, lines: int = 40) -> str:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])
