"""Find and drive ``cosyvoice-cli`` (cosyvoice.cpp), preferring what is installed.

Each call is its own process, so the model only occupies the GPU while a piece
is being synthesized. Nothing here imports torch.

Resolution order for every part is: environment override, something already
installed on the machine, then the copy the Drama Llama installer pinned.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .. import config
from .base import EngineNotAvailable

WINDOWS = os.name == "nt"
# Same hub layout the Whisper cache uses; deliberately not derived from HF_HOME.
HF_HUB = Path.home() / ".cache" / "huggingface" / "hub"
GGUF_CACHE = "models--Lourdle--Fun-CosyVoice3-0.5B-2512-GGUF"
UPSTREAM_CACHE = "models--FunAudioLLM--Fun-CosyVoice3-0.5B-2512"
#: Best first (measured with scripts/bench_cosyvoice.py). Whatever is present
#: is used; the RL file comes from scripts/convert_cosyvoice_rl.py.
MODEL_FILES = (
    "CosyVoice3-2512_RL_Q8_0.gguf",
    "CosyVoice3-2512_Q8_0.gguf",
    "CosyVoice3-2512_F16.gguf",
    "CosyVoice3-2512_Q4_K_M.gguf",
)
TOKENIZER_FILES = ("speech_tokenizer_v3.onnx", "speech_tokenizer_v3.int8.onnx")
CAMPPLUS_FILES = ("campplus.onnx", "campplus.int8.onnx")

CancelCheck = Callable[[], bool]


@dataclass(frozen=True)
class CosyRuntime:
    cli: Path
    ggml_dir: Path
    ggml_source: str
    model: Path
    speech_tokenizer: Path
    campplus: Path
    backend: str
    #: Identifies the exact GGML files, so a bad update can be told apart.
    ggml_fingerprint: str = ""


def _cli_name() -> str:
    return "cosyvoice-cli.exe" if WINDOWS else "cosyvoice-cli"


def find_cli() -> Path:
    if config.COSYVOICE_CLI:
        override = Path(config.COSYVOICE_CLI).expanduser()
        if not override.is_file():
            raise EngineNotAvailable(
                f"DRAMA_LLAMA_COSYVOICE_CLI points at {override}, which does not exist"
            )
        return override
    on_path = shutil.which(_cli_name())
    if on_path:
        return Path(on_path)
    own = config.COSYVOICE_HOME / "bin" / _cli_name()
    if own.is_file():
        return own
    raise EngineNotAvailable(
        "cosyvoice-cli was not found; run the installer with the CosyVoice "
        "component, or build it from a source checkout with "
        "scripts/build_cosyvoice.py --install"
    )


def _find_file(names: tuple[str, ...], caches: tuple[str, ...]) -> Path | None:
    roots = [config.COSYVOICE_MODELS]
    for cache in caches:
        roots.extend(sorted((HF_HUB / cache / "snapshots").glob("*")))
    # Preference between file names beats preference between locations.
    for name in names:
        for root in roots:
            for candidate in (root / name, root / "frontend-onnx" / name):
                if candidate.is_file():
                    return candidate
    return None


def find_models() -> tuple[Path, Path, Path]:
    """Return ``(gguf, speech_tokenizer, campplus)``."""
    if config.COSYVOICE_MODEL:
        model: Path | None = Path(config.COSYVOICE_MODEL).expanduser()
        if not model.is_file():
            raise EngineNotAvailable(
                f"DRAMA_LLAMA_COSYVOICE_MODEL points at {model}, which does not exist"
            )
    else:
        model = _find_file(MODEL_FILES, (GGUF_CACHE,))
    tokenizer = _find_file(TOKENIZER_FILES, (UPSTREAM_CACHE, GGUF_CACHE))
    campplus = _find_file(CAMPPLUS_FILES, (UPSTREAM_CACHE, GGUF_CACHE))
    if model is None or tokenizer is None or campplus is None:
        raise EngineNotAvailable(
            "CosyVoice model files are missing; run the installer with the "
            "CosyVoice component"
        )
    return model, tokenizer, campplus


def installed() -> bool:
    """Cheap check for status displays: files only, no process is started."""
    try:
        cli = find_cli()
        find_models()
    except EngineNotAvailable:
        return False
    return any(_has_ggml_1(directory) for _, directory in _ggml_candidates(cli))


def ollama_library_dirs() -> list[Path]:
    # Windows GGML DLL names do not carry the major version, and an older ggml
    # loads but produces garbage there, so Ollama is only reused where the
    # version can be checked.
    if WINDOWS:
        return []
    return [
        Path("/usr/local/lib/ollama"),
        Path("/usr/lib/ollama"),
        Path.home() / ".local" / "lib" / "ollama",
    ]


def _has_ggml_1(directory: Path) -> bool:
    if WINDOWS:
        return (directory / "ggml.dll").is_file()
    # is_file() follows symlinks, so a dangling link after an update fails too.
    return all(
        (directory / name).is_file() for name in ("libggml.so.1", "libggml-base.so.1")
    )


def _ggml_candidates(cli: Path) -> list[tuple[str, Path]]:
    candidates: list[tuple[str, Path]] = []
    if config.COSYVOICE_GGML:
        candidates.append(("env", Path(config.COSYVOICE_GGML).expanduser()))
    candidates.extend(("ollama", path) for path in ollama_library_dirs())
    candidates.append(("pinned", config.COSYVOICE_HOME / "ggml"))
    candidates.append(("bundled", cli.parent))
    return candidates


def _ggml_view(source: str, directory: Path) -> Path:
    """One flat directory of the libraries the runtime may load.

    Ollama keeps backends in sub-folders; the prebuilt CUDA backend (audible
    noise, cosyvoice.cpp issue #15) and the RPC backend are left out.
    """
    if WINDOWS:
        return directory
    view = config.DATA_DIR / "cosyvoice" / f"ggml-{source}"
    view.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="ggml-", dir=view.parent))
    found = [*directory.glob("libggml*.so*"), *directory.glob("vulkan/libggml*.so*")]
    for library in found:
        if "cuda" in library.name or "rpc" in library.name:
            continue
        link = staging / library.name
        if not link.exists():
            link.symlink_to(library.resolve())
    shutil.rmtree(view, ignore_errors=True)
    staging.rename(view)
    return view


def ggml_fingerprint(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.iterdir()):
        real = path.resolve()
        if real.is_file():
            stat = real.stat()
            digest.update(f"{real}|{stat.st_size}|{stat.st_mtime_ns}\n".encode())
    return digest.hexdigest()[:24]


def _rejected_file() -> Path:
    # Beside the runtime, so the installer's probe and the app agree.
    return config.COSYVOICE_HOME / "ggml-rejected.json"


def _rejected() -> set[str]:
    try:
        entries = json.loads(_rejected_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {str(entry) for entry in entries} if isinstance(entries, list) else set()


def reject_ggml(runtime: CosyRuntime) -> None:
    """Never use these exact libraries again: they produced broken audio."""
    target = _rejected_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    entries = sorted(_rejected() | {runtime.ggml_fingerprint})
    target.write_text(json.dumps(entries), encoding="utf-8")


def _environment(cli: Path, ggml_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    variable = "PATH" if WINDOWS else "LD_LIBRARY_PATH"
    parts = [str(cli.parent), str(ggml_dir), env.get(variable, "")]
    env[variable] = os.pathsep.join(part for part in parts if part)
    return env


def run_process(
    command: list[str],
    env: dict[str, str] | None,
    *,
    cancelled: CancelCheck | None = None,
    timeout: float = 600.0,
) -> tuple[int, str]:
    """Run to completion, killing exactly this child on cancel or timeout."""
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT)
        waited = 0.0
        try:
            while True:
                if cancelled and cancelled():
                    raise RuntimeError("cancelled")
                try:
                    process.wait(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    waited += 0.2
                    if waited >= timeout:
                        raise EngineNotAvailable(
                            f"cosyvoice-cli did not finish within {timeout:.0f} s"
                        ) from None
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        log.seek(0)
        return process.returncode, log.read().decode("utf-8", errors="replace")


def parse_backends(listing: str) -> list[tuple[str, str]]:
    """``--list-backends`` output as ``(name, description)`` pairs."""
    _, marker, table = listing.partition("Available GGML backends")
    if not marker:
        return []
    return [
        (match.group(1), match.group(2).strip())
        for match in re.finditer(r"^\s+(\S+)\s+\S+\s+(\S.*)$", table, re.MULTILINE)
    ]


def choose_backend(devices: list[tuple[str, str]], requested: str) -> str:
    if requested.lower() == "cpu":
        return "CPU"
    if requested.lower() != "auto":
        return requested
    vulkan = [item for item in devices if item[0].lower().startswith("vulkan")]
    for name, description in vulkan:
        if "nvidia" in description.lower():
            return name
    return vulkan[0][0] if vulkan else "CPU"


def resolve_runtime(exclude: frozenset[str] = frozenset()) -> CosyRuntime:
    """Locate everything and probe the GGML libraries; raises EngineNotAvailable.

    ``exclude`` skips library fingerprints for this call only.
    """
    cli = find_cli()
    model, tokenizer, campplus = find_models()
    failures: list[str] = []
    for source, directory in _ggml_candidates(cli):
        if not _has_ggml_1(directory):
            continue
        view = _ggml_view(source, directory)
        fingerprint = ggml_fingerprint(view)
        # An explicit override is the user's choice and is never second-guessed.
        if fingerprint in exclude or (source != "env" and fingerprint in _rejected()):
            failures.append(f"{directory}: skipped after producing broken audio")
            continue
        code, output = run_process(
            [str(cli), "--backend-path", str(view), "--list-backends"],
            _environment(cli, view),
            timeout=60,
        )
        devices = parse_backends(output)
        if code != 0 or not devices:
            failures.append(f"{directory}: {output.strip()[-300:]}")
            continue
        return CosyRuntime(
            cli=cli,
            ggml_dir=view,
            ggml_source=source,
            model=model,
            speech_tokenizer=tokenizer,
            campplus=campplus,
            backend=choose_backend(devices, config.COSYVOICE_BACKEND),
            ggml_fingerprint=fingerprint,
        )
    detail = "; ".join(failures) or "none was found"
    raise EngineNotAvailable(
        f"no usable GGML 1.x libraries for cosyvoice-cli ({detail}). Install the "
        "CosyVoice component or set DRAMA_LLAMA_COSYVOICE_GGML."
    )


def _invoke(
    runtime: CosyRuntime, arguments: list[str], output: Path, cancelled: CancelCheck | None
) -> None:
    command = [
        str(runtime.cli),
        "--backend-path",
        str(runtime.ggml_dir),
        "--backend",
        runtime.backend,
        *arguments,
    ]
    code, log = run_process(
        command, _environment(runtime.cli, runtime.ggml_dir), cancelled=cancelled
    )
    if code != 0 or not output.is_file():
        raise EngineNotAvailable(
            f"cosyvoice-cli failed ({runtime.ggml_source} GGML, exit {code}): "
            f"{log.strip()[-600:]}"
        )


def prepare_voice(
    runtime: CosyRuntime,
    clip: Path,
    transcript: str,
    cache_dir: Path,
    *,
    cancelled: CancelCheck | None = None,
) -> Path:
    """Encode a reference clip once; later renders reuse the prompt file."""
    digest = hashlib.sha256()
    digest.update(clip.read_bytes())
    for part in (transcript, runtime.speech_tokenizer.name, runtime.campplus.name):
        digest.update(b"\0" + part.encode("utf-8"))
    voice = cache_dir / f"{digest.hexdigest()[:24]}.gguf"
    if voice.is_file():
        return voice
    cache_dir.mkdir(parents=True, exist_ok=True)
    partial = voice.with_suffix(".partial")
    partial.unlink(missing_ok=True)
    _invoke(
        runtime,
        [
            "--frontend-only",
            "--speech-tokenizer", str(runtime.speech_tokenizer),
            "--campplus", str(runtime.campplus),
            "--prompt-audio", str(clip),
            "--prompt-text", transcript,
            "--prompt-speech-output", str(partial),
        ],
        partial,
        cancelled,
    )
    partial.replace(voice)
    return voice


def synthesize(
    runtime: CosyRuntime,
    voice: Path,
    text: str,
    output: Path,
    *,
    seed: int,
    instruction: str | None = None,
    cancelled: CancelCheck | None = None,
) -> None:
    """Write one piece of speech; instruct mode when ``instruction`` is given."""
    output.unlink(missing_ok=True)
    arguments = [
        "--model", str(runtime.model),
        "--prompt-speech", str(voice),
        "--text", text,
        "--seed", str(seed),
        "--output", str(output),
    ]
    if instruction:
        arguments += ["--instruction", instruction]
    _invoke(runtime, arguments, output, cancelled)


def main() -> int:
    """Print the resolved runtime as one JSON line (used by the installer)."""
    try:
        runtime = resolve_runtime()
    except EngineNotAvailable as exc:
        print(exc, file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "cli": str(runtime.cli),
                "ggml_source": runtime.ggml_source,
                "backend": runtime.backend,
                "model": str(runtime.model),
            }
        )
    )
    return 0
