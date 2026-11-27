"""Build ``cosyvoice-cli`` for Linux from the pinned sources.

    python scripts/build_cosyvoice.py --install          # build into ~/code/cosyvoice-cpp
    python scripts/build_cosyvoice.py --output dist      # release tarball + .sha256

Upstream's Linux binaries need a very recent glibc, so Drama Llama builds its
own (see the ``cosyvoice`` job in installer.yml). Compiling needs GCC 13: Ubuntu
24.04 for a local ``--install``, and 22.04 with the Ubuntu toolchain PPA for a
release asset (``--output``), which must run from 22.04 on. Every
input comes from ``installer/pins.json``: the cosyvoice.cpp commit, the GGML
commit and the ONNX Runtime NuGet package. Needs git, cmake >= 3.28, ninja and
a C++20 compiler (set CXX/CC to pick one). Only the command-line tool is built;
GGML backend libraries are not shipped, the app uses installed or pinned ones.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from installer.core.download import download
from installer.core.paths import legacy_cosyvoice_root, load_pins

ASSET_NAME = "dramallama-cosyvoice-linux-x86_64.tar.gz"
#: Ubuntu 22.04, the oldest distro the AppImage supports.
MAX_GLIBC = (2, 35)
#: Files in the asset, besides the licences.
RUNTIME_FILES = (
    "cosyvoice-cli",
    "libcosyvoice.so.0",
    "libonnxruntime.so.1",
    "libonnxruntime_providers_shared.so",
)


STATIC_RUNTIME = "-static-libstdc++ -static-libgcc -Wl,--exclude-libs,ALL"


def run(*command: str, cwd: Path | None = None, env: dict | None = None) -> str:
    done = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True)
    if done.returncode != 0:
        print((done.stdout + done.stderr)[-6000:], file=sys.stderr)
        raise SystemExit(f"command failed: {' '.join(command)}")
    return done.stdout


def checkout(url: str, commit: str, target: Path) -> None:
    if not (target / ".git").is_dir():
        run("git", "init", "--quiet", str(target))
        run("git", "remote", "add", "origin", url, cwd=target)
    run("git", "fetch", "--quiet", "--depth", "1", "origin", commit, cwd=target)
    run("git", "checkout", "--quiet", "--force", commit, cwd=target)


def unpack_onnxruntime(package: Path, target: Path) -> None:
    """The NuGet package carries the headers and the same library upstream ships."""
    shutil.rmtree(target, ignore_errors=True)
    (target / "include").mkdir(parents=True)
    (target / "lib").mkdir()
    with zipfile.ZipFile(package) as bundle:
        for info in bundle.infolist():
            name = PurePosixPath(info.filename)
            if info.filename.startswith("build/native/include/") and name.suffix == ".h":
                (target / "include" / name.name).write_bytes(bundle.read(info))
            elif info.filename.startswith("runtimes/linux-x64/native/") and name.suffix == ".so":
                (target / "lib" / name.name).write_bytes(bundle.read(info))
            elif info.filename in {"LICENSE", "ThirdPartyNotices.txt"}:
                (target / name.name).write_bytes(bundle.read(info))
    # The linker resolves the SONAME.
    shutil.copy2(target / "lib" / "libonnxruntime.so", target / "lib" / "libonnxruntime.so.1")


def build(work: Path, jobs: int) -> Path:
    """Return a directory holding exactly the files of the release asset."""
    meta = load_pins()["cosyvoice"]["build"]
    source, ggml, ort, out = work / "src", work / "ggml", work / "onnxruntime", work / "build"
    checkout(meta["repo"], meta["commit"], source)
    run("git", "submodule", "update", "--quiet", "--init", "--depth", "1", "vendor/pcre2", cwd=source)
    checkout(meta["ggml_repo"], meta["ggml_commit"], ggml)
    package = work / "onnxruntime.nupkg"
    ort_meta = meta["onnxruntime"]
    download(ort_meta["url"], package, sha256=ort_meta["sha256"], size=ort_meta["size"])
    unpack_onnxruntime(package, ort)

    shutil.rmtree(out, ignore_errors=True)
    run(
        "cmake", "-S", str(source), "-B", str(out), "-G", "Ninja",
        "-DCMAKE_BUILD_TYPE=Release",
        "-DCOSYVOICE_NO_ICU=ON",  # no system ICU; ICU would also read German numbers in English
        "-DCOSYVOICE_SERVER_NO_WEBUI=ON",
        f"-DGGML_SOURCE_DIR={ggml}",
        f"-DORT_PREBUILT_DIR={ort}",
        "-DGGML_BACKEND_DL=ON",
        "-DGGML_NATIVE=OFF",
        "-DCMAKE_BUILD_WITH_INSTALL_RPATH=ON",
        # GCC 13 is needed to compile, but older supported distros ship an older
        # libstdc++, so ours is linked in (GCC runtime library exception).
        # --exclude-libs keeps that copy from clashing with the system one that
        # ONNX Runtime and the GGML backends load.
        f"-DCMAKE_SHARED_LINKER_FLAGS={STATIC_RUNTIME}",
        # With only $ORIGIN as rpath the linker still has to find ONNX Runtime.
        f"-DCMAKE_EXE_LINKER_FLAGS={STATIC_RUNTIME} -Wl,-rpath-link,{ort / 'lib'}",
    )
    run("cmake", "--build", str(out), "--target", "cosyvoice-cli", "--parallel", str(jobs))

    stage = work / "stage"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir()
    sources = {
        "cosyvoice-cli": out / "bin" / "cosyvoice-cli",
        "libcosyvoice.so.0": out / "lib" / "libcosyvoice.so.0",
        "libonnxruntime.so.1": ort / "lib" / "libonnxruntime.so",
        "libonnxruntime_providers_shared.so": ort / "lib" / "libonnxruntime_providers_shared.so",
        "COSYVOICE-LICENSE": source / "LICENSE",
        "COSYVOICE-THIRD-PARTY.md": source / "THIRD_PARTY_NOTICES.md",
        "ONNXRUNTIME-LICENSE": ort / "LICENSE",
        "ONNXRUNTIME-THIRD-PARTY.txt": ort / "ThirdPartyNotices.txt",
    }
    for name, path in sources.items():
        if not path.is_file():
            raise SystemExit(f"build produced no {path}")
        shutil.copy2(path, stage / name)  # follows the library symlink
    (stage / "cosyvoice-cli").chmod(0o755)

    # Smoke test: it must start. The GGML libraries come from this build here.
    ggml_libs = sorted({str(path.parent) for path in out.rglob("libggml*.so*")})
    env = {**os.environ, "LD_LIBRARY_PATH": os.pathsep.join([str(stage), *ggml_libs])}
    run(str(stage / "cosyvoice-cli"), "--help", env=env)
    return stage


def require_baseline(stage: Path, *, strict: bool) -> None:
    """Report what the binaries need; refuse to release ones that need too much."""
    symbols = run("objdump", "-T", str(stage / "cosyvoice-cli"), str(stage / "libcosyvoice.so.0"))
    glibc = max(tuple(map(int, v.split("."))) for v in re.findall(r"GLIBC_(\d+\.\d+)", symbols))
    dynamic = sorted(set(re.findall(r"\*UND\*.*?(GLIBCXX_[\d.]+)", symbols)))
    print("requires glibc", ".".join(map(str, glibc)), "libstdc++", dynamic or "linked in")
    if strict and (glibc > MAX_GLIBC or dynamic):
        raise SystemExit(f"binaries need a newer system than supported: {glibc} {dynamic}")


def package_asset(stage: Path, output: Path) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    asset = output / ASSET_NAME
    with tarfile.open(asset, "w:gz") as bundle:
        for path in sorted(stage.iterdir()):
            bundle.add(path, arcname=path.name)
    digest = hashlib.sha256(asset.read_bytes()).hexdigest()
    asset.with_name(asset.name + ".sha256").write_text(f"{digest}  {asset.name}\n")
    print(f"asset:  {asset} ({asset.stat().st_size} bytes)\nsha256: {digest}")
    return asset


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--work", type=Path, default=ROOT / "build" / "cosyvoice")
    parser.add_argument("--output", type=Path, help="write the release tarball here")
    parser.add_argument("--install", action="store_true", help="copy into the app's runtime dir")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    args = parser.parse_args(argv)
    if sys.platform != "linux":
        raise SystemExit("only Linux is built here; Windows uses the pinned upstream release")
    for tool in ("git", "cmake", "ninja", "objdump"):
        if shutil.which(tool) is None:
            raise SystemExit(f"{tool} is required to build cosyvoice-cli")

    stage = build(args.work.resolve(), args.jobs)
    require_baseline(stage, strict=args.output is not None)
    if args.output:
        package_asset(stage, args.output.resolve())
    if args.install:
        target = legacy_cosyvoice_root() / "bin"
        target.mkdir(parents=True, exist_ok=True)
        for path in stage.iterdir():
            # Replace rather than overwrite, so a running copy is not disturbed.
            (target / path.name).unlink(missing_ok=True)
            shutil.copy2(path, target / path.name)
        print(f"installed: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
