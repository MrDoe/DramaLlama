"""Download and unpack the pinned Windows OpenBLAS (used to build staging).

    python scripts/fetch_openblas.py third_party/openblas
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from installer.core.download import download
from installer.core.paths import load_pins
from installer.core.util import extract_archive


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        raise SystemExit("usage: fetch_openblas.py TARGET_DIRECTORY")
    target = Path(arguments[0]).resolve()
    meta = load_pins()["spatial"]["openblas_windows"]
    archive = download(
        meta["url"], target.with_suffix(".zip"), sha256=meta["sha256"], size=meta["size"]
    )
    extract_archive(archive, target)
    licence = meta["license"]
    download(
        licence["url"], target / "LICENSE", sha256=licence["sha256"], size=licence["size"]
    )
    for needed in ("lib/libopenblas.lib", "bin/libopenblas.dll", "include/lapacke.h"):
        if not (target / needed).is_file():
            raise SystemExit(f"the OpenBLAS package has no {needed}")
    print(f"OpenBLAS {meta['version']} in {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
