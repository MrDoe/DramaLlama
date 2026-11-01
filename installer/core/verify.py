"""Post-install checks: files, imports, engines and a real API smoke test."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .detect import SystemInfo
from .paths import (
    InstallPaths,
    legacy_breeze_de_binary,
    legacy_breeze_de_model,
    legacy_breeze_model,
    legacy_breeze_repo,
    runtime_search_path,
    whisper_cache_root,
)
from .plan import BREEZE_DE, BREEZE_EN, CHATTERBOX, Selection, effective_device
from .progress import Progress
from .util import InstallerError, command_output, human_bytes, is_windows, run

ENGINE_NAMES = {"fake", "breeze", "breeze-de", "chatterbox", "suno"}


@dataclass
class VerifyCheck:
    name: str
    ok: bool
    detail: str = ""
    required: bool = True


@dataclass
class VerifyResult:
    ok: bool
    checks: list[VerifyCheck] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        failed = [check for check in self.checks if not check.ok]
        if self.ok:
            return f"{len(self.checks)} checks passed"
        return f"{len(failed)} check(s) failed: " + ", ".join(
            check.name for check in failed
        )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def verify_files(
    paths: InstallPaths,
    pins: dict,
    system: SystemInfo,
    selection: Selection,
    *,
    home: Path | None = None,
) -> list[VerifyCheck]:
    checks: list[VerifyCheck] = []
    python = paths.python(system=system.os_name)
    checks.append(VerifyCheck("virtualenv", python.is_file(), str(python)))
    index = paths.app / "web" / "dist" / "index.html"
    checks.append(VerifyCheck("web UI", index.is_file(), str(index)))
    bridge = paths.app / "suno-bridge" / "server.mjs"
    checks.append(VerifyCheck("Suno bridge", bridge.is_file(), str(bridge)))
    bettersuno = paths.app / "third_party" / "bettersuno-mcp" / "package.json"
    checks.append(VerifyCheck("BetterSuno modules", bettersuno.is_file(), str(bettersuno)))
    checks.append(VerifyCheck("launcher", (paths.prefix / "launch.py").is_file()))
    checks.append(VerifyCheck("ffmpeg", paths.ffmpeg.is_file(), str(paths.ffmpeg)))

    whisper = whisper_cache_root(home) / "models--Systran--faster-whisper-large-v3"
    model = whisper / "snapshots" / str(pins["whisper"]["revision"]) / "model.bin"
    checks.append(VerifyCheck("Whisper large-v3", model.is_file(), str(model)))
    repo = legacy_breeze_repo(home)
    checks.append(
        VerifyCheck(
            "Breeze repo code",
            (repo / "breeze_infer" / "runtime.py").is_file()
            and (repo / "models" / "fast_streaming.py").is_file(),
            str(repo),
        )
    )
    if selection.has(BREEZE_EN):
        model_dir = legacy_breeze_model(home)
        required = [model_dir / "config.json", model_dir / "model-00001-of-00002.safetensors"]
        checks.append(
            VerifyCheck(
                "Breeze English",
                all(path.is_file() for path in required),
                str(model_dir),
            )
        )
    if selection.has(BREEZE_DE):
        binary = legacy_breeze_de_binary(home, windows=system.os_name == "windows")
        gguf = legacy_breeze_de_model(home)
        checks.append(
            VerifyCheck(
                "Breeze German",
                binary.is_file() and gguf.is_file(),
                f"{binary} / {gguf}",
            )
        )
    if selection.has(CHATTERBOX):
        checks.append(_chatterbox_check(pins))
    return checks


def _chatterbox_check(pins: dict) -> VerifyCheck:
    import shutil

    exe = shutil.which("docker")
    if not exe:
        return VerifyCheck("Chatterbox", False, "docker not found", required=True)
    container = str(pins["chatterbox"]["container"])
    result = run([exe, "inspect", "--type", "container", container], timeout=30)
    if result.returncode != 0:
        return VerifyCheck("Chatterbox", False, f"container {container} missing")
    return VerifyCheck("Chatterbox", True, f"container {container} exists")


def verify_imports(
    paths: InstallPaths,
    system: SystemInfo,
    selection: Selection,
    *,
    timeout: float = 300,
) -> VerifyCheck:
    modules = ["drama_studio", "soundfile", "numpy", "scipy", "faster_whisper", "torch"]
    if selection.has(BREEZE_EN):
        modules += ["transformers", "qwen_tts"]
    code = (
        "import importlib\n"
        f"for name in {modules!r}:\n"
        "    importlib.import_module(name)\n"
        "import torch\n"
        "print(torch.__version__, torch.version.cuda, torch.cuda.is_available())\n"
    )
    try:
        output = command_output(
            [paths.python(system=system.os_name), "-c", code], timeout=timeout
        )
    except InstallerError as exc:
        return VerifyCheck("Python imports", False, str(exc))
    return VerifyCheck("Python imports", True, output.splitlines()[-1] if output else "ok")


def _server_env(
    paths: InstallPaths,
    data_dir: Path,
    system: SystemInfo,
    selection: Selection,
    *,
    breeze_de_cpu: bool,
) -> dict[str, str]:
    from .launchers import launcher_env

    device = effective_device(selection, system)
    env = dict(os.environ)
    env.update(
        launcher_env(
            paths,
            data_dir=data_dir,
            device=device,
            breeze_de=selection.has(BREEZE_DE),
            breeze_de_cpu=breeze_de_cpu,
        )
    )
    env["PATH"] = os.pathsep.join(
        [str(path) for path in runtime_search_path(paths, system=system.os_name)]
        + [os.environ.get("PATH", "")]
    )
    return env


def verify_server(
    paths: InstallPaths,
    pins: dict,
    system: SystemInfo,
    selection: Selection,
    progress: Progress,
    *,
    breeze_de_cpu: bool = False,
    timeout: float = 120,
) -> list[VerifyCheck]:
    port = _free_port()
    data_dir = Path(tempfile.mkdtemp(prefix="drama-llama-verify-"))
    log_path = data_dir / "server.log"
    env = _server_env(paths, data_dir, system, selection, breeze_de_cpu=breeze_de_cpu)
    command = [
        str(paths.python(system=system.os_name)),
        "-m",
        "drama_studio.cli",
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--no-worker",
        "--no-suno-bridge",
    ]
    progress.update("verify", 0.2, "Starting the API for a smoke test…")
    creationflags = 0
    if is_windows(system.os_name):
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with open(log_path, "wb") as log:
        process = subprocess.Popen(
            command,
            cwd=str(paths.app),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
            start_new_session=not is_windows(system.os_name),
        )
    health_url = f"http://127.0.0.1:{port}/api/health"
    checks: list[VerifyCheck] = []
    try:
        payload: dict | None = None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            try:
                with urllib.request.urlopen(health_url, timeout=3) as response:
                    if response.status == 200:
                        payload = json.loads(response.read().decode("utf-8"))
                        break
            except (urllib.error.URLError, OSError, ValueError):
                pass
            time.sleep(0.5)
        if payload is None:
            detail = "API did not start"
            try:
                detail += "; log tail: " + "\n".join(
                    log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-10:]
                )
            except OSError:
                pass
            checks.append(VerifyCheck("API health", False, detail))
            return checks
        checks.append(
            VerifyCheck(
                "API health",
                payload.get("status") == "ok",
                f"version {payload.get('version')}",
            )
        )
        names = {item.get("name") for item in payload.get("engines", [])}
        checks.append(
            VerifyCheck(
                "Engine registry",
                ENGINE_NAMES.issubset(names),
                ", ".join(sorted(str(name) for name in names)),
            )
        )
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
                body = response.read(4096).decode("utf-8", errors="replace")
                ok = response.status == 200 and 'id="root"' in body
                checks.append(VerifyCheck("Web UI served", ok, "index.html"))
        except (urllib.error.URLError, OSError) as exc:
            checks.append(VerifyCheck("Web UI served", False, str(exc)))
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        shutil.rmtree(data_dir, ignore_errors=True)
    progress.update("verify", 0.8, "API smoke test finished")
    return checks


def verify_install(
    paths: InstallPaths,
    pins: dict,
    system: SystemInfo,
    selection: Selection,
    progress: Progress,
    *,
    breeze_de_cpu: bool = False,
) -> VerifyResult:
    checks = verify_files(paths, pins, system, selection)
    checks.append(verify_imports(paths, system, selection))
    checks.extend(
        verify_server(
            paths,
            pins,
            system,
            selection,
            progress,
            breeze_de_cpu=breeze_de_cpu,
        )
    )
    ok = all(check.ok for check in checks if check.required)
    warnings = [
        f"{check.name}: {check.detail}" for check in checks if not check.ok and not check.required
    ]
    progress.update(
        "verify",
        1.0,
        "Verification passed" if ok else "Verification found problems",
    )
    return VerifyResult(ok=ok, checks=checks, warnings=warnings)


def describe_install(paths: InstallPaths, pins: dict, system: SystemInfo) -> str:
    whisper = whisper_cache_root() / "models--Systran--faster-whisper-large-v3"
    size = 0
    for root, _dirs, files in os.walk(whisper):
        for name in files:
            try:
                size += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return f"Whisper cache {human_bytes(size)}; app at {paths.app}"
