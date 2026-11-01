"""Install the Python stack, choosing the pinned torch wheel channel."""

from __future__ import annotations

import os

from .detect import SystemInfo
from .paths import InstallPaths
from .plan import BREEZE_EN, Selection, torch_channel_for
from .progress import Progress
from .runtimes import uv_pip_check, uv_pip_install
from .util import InstallerError, command_output, human_bytes

PYPI = "https://pypi.org/simple"


def install_stack(
    paths: InstallPaths,
    pins: dict,
    system: SystemInfo,
    selection: Selection,
    progress: Progress,
) -> str:
    channel = torch_channel_for(selection, pins, system)
    torch_version = str(pins["torch"]["version"])
    torchaudio_version = str(pins["torch"]["torchaudio"])
    index = pins["torch"]["channels"][channel]["index"]
    env = dict(os.environ)
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    progress.update(
        "python",
        0.02,
        f"Installing PyTorch {torch_version} ({channel.upper()})…",
    )
    progress.log(
        f"torch channel: {channel} ({index}); "
        f"GPU: {system.gpu_summary if channel != 'cpu' else 'not used'}"
    )
    uv_pip_install(
        paths,
        pins,
        [f"torch=={torch_version}", f"torchaudio=={torchaudio_version}"],
        progress=progress,
        index_url=str(index),
        extra_index_url=PYPI,
        timeout=5400,
        system=system,
    )
    progress.update("python", 0.6, "Installing Drama Llama and its audio dependencies…")
    extras = "tts"
    if selection.has(BREEZE_EN):
        extras = "tts,breeze"
    uv_pip_install(
        paths,
        pins,
        ["-e", f"{paths.app}[{extras}]"],
        progress=progress,
        timeout=3600,
        system=system,
    )
    progress.update("python", 0.9, "Checking the virtualenv…")
    uv_pip_check(paths, progress, system=system)
    _smoke_test(paths, system, selection, channel, progress)
    progress.update("python", 1.0, "Python stack ready")
    return channel


def _smoke_test(
    paths: InstallPaths,
    system: SystemInfo,
    selection: Selection,
    channel: str,
    progress: Progress,
) -> None:
    imports = [
        "drama_studio",
        "soundfile",
        "numpy",
        "scipy",
        "faster_whisper",
        "imageio_ffmpeg",
        "torch",
        "torchaudio",
    ]
    if selection.has(BREEZE_EN):
        imports += ["transformers", "qwen_tts"]
    code = (
        "import importlib, json\n"
        f"mods = {imports!r}\n"
        "for name in mods:\n"
        "    importlib.import_module(name)\n"
        "import torch\n"
        "print(json.dumps({\n"
        "    'torch': torch.__version__,\n"
        "    'cuda': torch.version.cuda,\n"
        "    'cuda_available': torch.cuda.is_available(),\n"
        "    'devices': torch.cuda.device_count(),\n"
        "}))\n"
    )
    try:
        output = command_output([paths.python(system=system.os_name), "-c", code], timeout=300)
    except InstallerError as exc:
        raise InstallerError(
            "the Python stack installed but an import smoke test failed; "
            "see the install log"
        ) from exc
    progress.log(f"import smoke test: {output}")
    if channel != "cpu" and '"cuda_available": false' in output.lower():
        progress.log(
            "warning: PyTorch cannot see the GPU. Check the NVIDIA driver and "
            "the selected CUDA wheel channel; CPU fallback still works."
        )
    try:
        import json

        info = json.loads(output.splitlines()[-1])
        progress.log(
            f"torch {info.get('torch')} (CUDA {info.get('cuda') or 'none'}), "
            f"{info.get('devices', 0)} device(s)"
        )
    except (ValueError, IndexError):
        pass


def stack_summary(paths: InstallPaths, system: SystemInfo) -> str:
    python = paths.python(system=system.os_name)
    if not python.is_file():
        return "virtualenv missing"
    try:
        version = command_output([python, "--version"], timeout=30)
    except InstallerError:
        version = "unknown"
    return f"{version} at {paths.venv}"


def venv_size(paths: InstallPaths) -> int:
    total = 0
    for root, _dirs, files in os.walk(paths.venv):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def describe_venv(paths: InstallPaths) -> str:
    try:
        return human_bytes(venv_size(paths))
    except OSError:
        return "unknown"
