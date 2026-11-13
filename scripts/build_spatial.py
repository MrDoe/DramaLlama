"""Build the native spatial renderer (SAF ambi_roomsim + the Drama Llama shim).

    python scripts/build_spatial.py                  # auto-detect SAF
    python scripts/build_spatial.py --saf /path/to/Spatial_Audio_Framework
    python scripts/build_spatial.py --clean

Output: build/spatial/libdrama_llama_spatial.{so,dylib,dll}, which
drama_studio.spatial picks up automatically. Set DRAMA_LLAMA_SPATIAL_LIB to
point at a different location.

SAF (ISC) is a submodule of SPARTA upstream; a checkout of it is required to
build. Nothing from SPARTA's GPLv3 plug-in sources is compiled here.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = ROOT / "src" / "drama_studio" / "spatial"
BUILD_DIR = ROOT / "build" / "spatial"

# Places SAF may already live, relative to this repo's usual neighbourhood.
SAF_CANDIDATES = (
    ROOT.parent / "SPARTA" / "SDKs" / "Spatial_Audio_Framework",
    ROOT / "third_party" / "Spatial_Audio_Framework",
)


def find_saf(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit).expanduser().resolve()
    elif env := os.environ.get("DRAMA_LLAMA_SAF_DIR"):
        path = Path(env).expanduser().resolve()
    else:
        path = next(
            (candidate for candidate in SAF_CANDIDATES if (candidate / "framework" / "include" / "saf.h").is_file()),
            None,
        )
        if path is None:
            tried = "\n  ".join(str(candidate) for candidate in SAF_CANDIDATES)
            raise SystemExit(
                "Spatial_Audio_Framework not found. Looked in:\n  "
                f"{tried}\nPass --saf PATH or set DRAMA_LLAMA_SAF_DIR."
            )

    if not (path / "framework" / "include" / "saf.h").is_file():
        raise SystemExit(f"{path} is not a Spatial_Audio_Framework checkout (no framework/include/saf.h)")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--saf", help="path to a Spatial_Audio_Framework checkout")
    parser.add_argument("--clean", action="store_true", help="discard the build directory first")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    args = parser.parse_args(argv)

    if shutil.which("cmake") is None:
        raise SystemExit("cmake is required to build the spatial renderer")

    saf = find_saf(args.saf)
    if args.clean and BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)

    configured = subprocess.run(
        ["cmake", "-S", str(SOURCE_DIR), "-B", str(BUILD_DIR), "-DCMAKE_BUILD_TYPE=Release", f"-DSAF_DIR={saf}"],
        capture_output=True,
        text=True,
    )
    if configured.returncode != 0:
        print(configured.stderr or configured.stdout, file=sys.stderr)
        raise SystemExit("cmake configure failed")

    built = subprocess.run(
        ["cmake", "--build", str(BUILD_DIR), "--parallel", str(args.jobs)],
        capture_output=True,
        text=True,
    )
    if built.returncode != 0:
        print(built.stderr or built.stdout, file=sys.stderr)
        raise SystemExit("build failed")

    libraries = sorted(BUILD_DIR.glob("libdrama_llama_spatial.*"))
    library = next((p for p in libraries if p.suffix in {".so", ".dylib", ".dll"}), None)
    if library is None:
        raise SystemExit("build produced no loadable library")

    print(f"SAF:     {saf}")
    print(f"library: {library.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
