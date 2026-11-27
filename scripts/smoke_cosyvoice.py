"""Install the CosyVoice component into a throwaway directory and render a line.

    python scripts/smoke_cosyvoice.py --root /tmp/cosy
    python scripts/smoke_cosyvoice.py --root /tmp/cosy --cli-asset dist/dramallama-cosyvoice-linux-x86_64.tar.gz

Runs the real installer step (about 2 GB of downloads unless the files are
already under ``--home``), then synthesizes one instructed German line on the
CPU with the app's own runtime code. CI uses it on Windows and on the oldest
supported Linux, where no GPU and no Whisper are available: it checks that the
pieces load, accept non-ASCII text and produce audio, not how it sounds.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from installer.core import engines
from installer.core.detect import detect_system
from installer.core.download import download
from installer.core.paths import (
    InstallPaths,
    legacy_cosyvoice_models,
    legacy_cosyvoice_root,
    load_pins,
)
from installer.core.progress import Progress
from installer.core.util import extract_archive

# Runs inside the app's virtualenv, like a render would.
RENDER = """
import sys
from pathlib import Path
from drama_studio.audio import read_mono
from drama_studio.engines import cosyvoice_runtime as runtime
from drama_studio.speech_check import is_silent

clip, transcript, work = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
resolved = runtime.resolve_runtime()
print("ggml:", resolved.ggml_source, "backend:", resolved.backend, flush=True)
voice = runtime.prepare_voice(resolved, clip, transcript, work / "voices")
output = work / "smoke.wav"
runtime.synthesize(
    resolved, voice, "Grüße aus Köln.", output, seed=42, instruction="请非常开心地说一句话。"
)
audio, rate = read_mono(output)
seconds = len(audio) / rate
print(f"rendered {seconds:.2f} s at {rate} Hz", flush=True)
if is_silent(audio, rate) or not 0.4 < seconds < 30:
    raise SystemExit("the rendered audio is silent or has an implausible length")
"""


def run(*command: object, **kwargs) -> None:
    subprocess.run([str(part) for part in command], check=True, **kwargs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, required=True, help="throwaway directory")
    parser.add_argument("--home", type=Path, help="home holding code/ and models/")
    parser.add_argument("--cli-asset", type=Path, help="local program archive to test")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    home = (args.home or root / "home").resolve()
    pins = load_pins()
    system = detect_system()
    paths = InstallPaths.for_prefix(root / "prefix")
    paths.ensure()

    python = paths.python(system=system.os_name)
    if not python.exists():
        run(sys.executable, "-m", "venv", paths.venv)
        run(python, "-m", "pip", "install", "--quiet", "-e", ROOT)

    if args.cli_asset:
        extract_archive(args.cli_asset.resolve(), legacy_cosyvoice_root(home) / "bin")

    progress = Progress()
    progress.on_log = lambda message: print(message, flush=True)
    result = engines.install_cosyvoice(
        paths, pins, system, progress, device="cpu", home=home
    )
    print("installed:", result.cli, "ggml:", result.ggml_source, flush=True)

    reference = pins["cosyvoice"]["smoke_reference"]
    clip = download(
        reference["url"],
        root / "reference.wav",
        sha256=reference["sha256"],
        size=reference["size"],
    )
    env = dict(
        os.environ,
        PYTHONUTF8="1",
        PYTHONIOENCODING="utf-8",
        DRAMA_LLAMA_DATA=str(root / "data"),
        DRAMA_LLAMA_COSYVOICE_HOME=str(legacy_cosyvoice_root(home)),
        DRAMA_LLAMA_COSYVOICE_MODELS=str(legacy_cosyvoice_models(home)),
        DRAMA_LLAMA_COSYVOICE_BACKEND="cpu",
    )
    run(python, "-c", RENDER, clip, reference["text"], root / "work", env=env, timeout=1500)
    print("CosyVoice smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
