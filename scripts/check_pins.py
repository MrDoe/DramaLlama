"""CI check: dry-run resolve the pinned Python stack for every torch channel.

Two installer releases in a row shipped pins that `uv` could not resolve
(bare torch pins matching PyPI's CUDA wheel; a transposed imageio-ffmpeg
version). The installer unit tests mock `uv`, so nothing caught it. This
script runs `uv pip install --dry-run` with exactly the arguments
`install_stack()` uses, for every channel in pins.json, and fails when any
of them do not resolve. It downloads nothing and installs nothing.

Usage: python scripts/check_pins.py  (needs `uv` on PATH and network access)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def find_uv() -> str:
    """Return a working uv binary, honouring UV_BIN."""
    import os
    import shutil

    candidates = []
    override = os.environ.get("UV_BIN")
    if override:
        candidates.append(override)
    found = shutil.which("uv")
    if found:
        candidates.append(found)
    for candidate in candidates:
        try:
            result = subprocess.run(
                [candidate, "--version"], capture_output=True, text=True, timeout=30
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0:
            return candidate
    raise SystemExit(
        "no working `uv` found (tried: "
        + ", ".join(candidates or ["<nothing on PATH>"])
        + "). Install it with `pip install uv` or set UV_BIN."
    )


def run(command: list[str]) -> None:
    print("+", " ".join(command), flush=True)
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stdout[-4000:])
        print(result.stderr[-4000:], file=sys.stderr)
        raise SystemExit(f"resolution failed: {' '.join(command)}")


def main() -> int:
    uv = find_uv()
    print(f"using {uv}", flush=True)
    pins = json.loads((ROOT / "installer" / "pins.json").read_text(encoding="utf-8"))
    torch_meta = pins["torch"]
    for channel, meta in torch_meta["channels"].items():
        suffix = f"+{channel}"
        args = [
            "-e",
            ".[tts,breeze]",
            f"torch=={torch_meta['version']}{suffix}",
            f"torchaudio=={torch_meta['torchaudio']}{suffix}",
        ]
        run(
            [
                uv,
                "pip",
                "install",
                "--dry-run",
                "--python",
                sys.executable,
                "--index-url",
                str(meta["index"]),
                "--extra-index-url",
                "https://pypi.org/simple",
                "--index-strategy",
                "unsafe-best-match",
                *args,
            ]
        )
        print(f"channel {channel}: resolves", flush=True)
    print("all channels resolve")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
