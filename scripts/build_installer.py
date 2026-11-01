#!/usr/bin/env python3
"""Build the native Drama Llama installers.

Linux:  a PyInstaller onedir wizard wrapped as an AppImage (plus a tarball
        fallback and SHA-256 checksums).
Windows: a PyInstaller wizard wrapped by a thin Inno Setup installer that only
        registers shortcuts/uninstall; the wizard installs the app itself.

Run ``python scripts/build_installer.py --help`` for the options. The script is
intentionally stdlib-only and imports the installer core for payload staging so
a dev install and a released installer share the exact same layout.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from installer.core.app_payload import stage_payload
from installer.core.util import app_version, sha256_file

BUILD = ROOT / "build" / "installer"
DEFAULTS = {
    "payload": ROOT / "build" / "installer" / "payload",
    "dist": ROOT / "dist",
    "work": ROOT / "build" / "pyinstaller",
}
SPEC = ROOT / "installer" / "packaging" / "pyinstaller.spec"
INNO = ROOT / "installer" / "packaging" / "inno" / "DramaLlama.iss"
APPIMAGE = ROOT / "installer" / "packaging" / "appimage" / "build-appimage.sh"
ICON_SOURCE = ROOT / "web" / "public" / "llogo.png"


class BuildError(RuntimeError):
    pass


def run(command: list[str | Path], *, cwd: Path | None = None, env: dict | None = None) -> None:
    printable = " ".join(str(item) for item in command)
    print(f"+ {printable}", flush=True)
    result = subprocess.run(
        [str(item) for item in command],
        cwd=str(cwd) if cwd else None,
        env=env,
    )
    if result.returncode != 0:
        raise BuildError(f"command failed ({result.returncode}): {printable}")


def _tool(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _require_python_stack() -> None:
    missing = []
    for module in ("PyInstaller", "PySide6"):
        try:
            __import__(module)
        except ImportError:
            missing.append(module)
    if missing:
        raise BuildError(
            "missing build dependency/dependencies: "
            + ", ".join(missing)
            + "\nInstall with: pip install pyinstaller PySide6"
        )


def web_needs_build() -> bool:
    index = ROOT / "web" / "dist" / "index.html"
    if not index.is_file():
        return True
    newest = index.stat().st_mtime
    for base in (ROOT / "web" / "src", ROOT / "web" / "public"):
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            try:
                if path.is_file() and path.stat().st_mtime > newest:
                    return True
            except OSError:
                continue
    for name in ("package.json", "vite.config.ts", "index.html"):
        candidate = ROOT / "web" / name
        try:
            if candidate.is_file() and candidate.stat().st_mtime > newest:
                return True
        except OSError:
            continue
    return False


def ensure_web(*, skip: bool) -> None:
    if skip:
        if not (ROOT / "web" / "dist" / "index.html").is_file():
            raise BuildError(
                "web/dist is missing; cannot skip the frontend build (remove --skip-web)"
            )
        print("using the existing web/dist build", flush=True)
        return
    if not web_needs_build():
        print("web/dist is up to date", flush=True)
        return
    npm = _tool("npm")
    if npm is None:
        raise BuildError("npm not found; install Node.js 22 or pass --skip-web")
    if not (ROOT / "web" / "node_modules").is_dir():
        run([npm, "install"], cwd=ROOT / "web")
    run([npm, "run", "build"], cwd=ROOT / "web")


def stage(*, payload: Path) -> Path:
    print(f"staging payload at {payload}", flush=True)
    stage_payload(ROOT, payload)
    return payload


def make_windows_icon(target_dir: Path) -> Path | None:
    if not ICON_SOURCE.is_file():
        return None
    target_dir.mkdir(parents=True, exist_ok=True)
    icon = target_dir / "dramallama.ico"
    for tool in ("magick", "convert"):
        exe = _tool(tool)
        if exe is None:
            continue
        try:
            run(
                [
                    exe,
                    ICON_SOURCE,
                    "-background",
                    "none",
                    "-define",
                    "icon:auto-resize=256,128,64,48,32,16",
                    icon,
                ]
            )
        except BuildError:
            continue
        if icon.is_file():
            return icon
    print("warning: ImageMagick not found; the setup uses the default icon", flush=True)
    return None


def build_pyinstaller(*, payload: Path, dist: Path, work: Path) -> Path:
    _require_python_stack()
    environment = dict(os.environ)
    environment["DRAMA_LLAMA_PAYLOAD"] = str(payload)
    run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            dist,
            "--workpath",
            work,
            SPEC,
        ],
        cwd=ROOT,
        env=environment,
    )
    onedir = dist / "DramaLlama-Setup"
    exe = onedir / ("DramaLlama-Setup.exe" if os.name == "nt" else "DramaLlama-Setup")
    if not exe.is_file():
        raise BuildError(f"PyInstaller did not produce {exe}")
    return onedir


def _checksum(path: Path) -> Path:
    digest = sha256_file(path)
    target = path.with_name(path.name + ".sha256")
    target.write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    print(f"sha256 {digest}  {path.name}", flush=True)
    return target


def _tarball(onedir: Path, out: Path, version: str) -> Path:
    archive = out / f"DramaLlama-{version}-linux-x86_64.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(onedir, arcname=onedir.name)
    return archive


def build_linux(*, onedir: Path, out: Path, version: str, with_appimage: bool) -> list[Path]:
    artifacts: list[Path] = []
    archive = _tarball(onedir, out, version)
    artifacts.append(archive)
    if with_appimage:
        bash = _tool("bash")
        if bash is None:
            raise BuildError("bash is required to build the AppImage")
        run([bash, APPIMAGE, onedir, version, out])
        image = out / f"DramaLlama-{version}-x86_64.AppImage"
        if image.is_file():
            artifacts.append(image)
        else:
            fallback = out / f"DramaLlama-{version}-linux-x86_64.AppDir.tar.gz"
            if fallback.is_file():
                artifacts.append(fallback)
    for artifact in artifacts:
        _checksum(artifact)
    return artifacts


def find_iscc() -> str:
    candidates = [
        os.environ.get("INNO_SETUP_ISCC"),
        _tool("iscc"),
        _tool("ISCC"),
    ]
    for base in (
        os.environ.get("PROGRAMFILES(X86)"),
        os.environ.get("PROGRAMFILES"),
        os.environ.get("LOCALAPPDATA"),
    ):
        if base:
            candidates.append(str(Path(base) / "Inno Setup 6" / "ISCC.exe"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise BuildError(
        "Inno Setup compiler (ISCC.exe) not found. Install Inno Setup 6 or set "
        "INNO_SETUP_ISCC."
    )


def build_windows(*, onedir: Path, out: Path, version: str) -> list[Path]:
    iscc = find_iscc()
    icon = make_windows_icon(BUILD / "icon")
    command: list[str | Path] = [
        iscc,
        f"/DAppVersion={version}",
        f"/DSourceDir={onedir}",
        f"/DOutputDir={out}",
        f"/DOutputBaseFilename=DramaLlama-Setup-{version}",
    ]
    if icon is not None:
        command.append(f"/DSetupIconFile={icon}")
    command.append(INNO)
    run(command)
    setup = out / f"DramaLlama-Setup-{version}.exe"
    if not setup.is_file():
        raise BuildError(f"Inno Setup did not produce {setup}")
    _checksum(setup)
    return [setup]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        choices=("linux", "windows", "payload", "all"),
        default="linux" if os.name != "nt" else "windows",
        help="artifact to build on this machine (default: host OS)",
    )
    parser.add_argument("--version", default=app_version(ROOT))
    parser.add_argument("--payload-dir", type=Path, default=DEFAULTS["payload"])
    parser.add_argument("--output-dir", type=Path, default=DEFAULTS["dist"])
    parser.add_argument("--work-dir", type=Path, default=DEFAULTS["work"])
    parser.add_argument("--skip-web", action="store_true", help="use the existing web/dist")
    parser.add_argument(
        "--skip-appimage", action="store_true", help="only build the Linux tarball"
    )
    parser.add_argument("--skip-inno", action="store_true", help="only build the raw Windows exe")
    parser.add_argument(
        "--allow-cross-target",
        action="store_true",
        help="attempt a non-host target anyway (PyInstaller cannot cross-compile)",
    )
    args = parser.parse_args(argv)

    target = args.target
    if target == "all":
        target = "windows" if os.name == "nt" else "linux"
        print(f"--target all is not supported for cross-compilation; building {target}", flush=True)
    host = "windows" if os.name == "nt" else "linux"
    if target in {"linux", "windows"} and target != host and not args.allow_cross_target:
        print(
            f"error: cannot build a {target} installer on {host}; "
            "run the build on that OS (or in CI)",
            file=sys.stderr,
        )
        return 2

    try:
        ensure_web(skip=args.skip_web)
        payload = stage(payload=args.payload_dir)
        if target == "payload":
            print(f"payload ready at {payload}")
            return 0
        args.output_dir.mkdir(parents=True, exist_ok=True)
        onedir = build_pyinstaller(
            payload=payload, dist=args.output_dir, work=args.work_dir
        )
        if target == "windows":
            artifacts = (
                [onedir]
                if args.skip_inno
                else build_windows(onedir=onedir, out=args.output_dir, version=args.version)
            )
        else:
            artifacts = build_linux(
                onedir=onedir,
                out=args.output_dir,
                version=args.version,
                with_appimage=not args.skip_appimage,
            )
        print("\nArtifacts:")
        for artifact in artifacts:
            size = artifact.stat().st_size if artifact.is_file() else 0
            print(f"  {artifact}  ({size / (1024 * 1024):.1f} MiB)")
        return 0
    except BuildError as exc:
        print(f"build failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
