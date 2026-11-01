"""Optional engine installers: Breeze repo/weights, Breeze DE and Chatterbox."""

from __future__ import annotations

import json
import os
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path

from .detect import SystemInfo, detect_vulkan
from .download import download, hf_url
from .paths import (
    InstallPaths,
    legacy_breeze_de_model,
    legacy_breeze_de_root,
    legacy_breeze_model,
    legacy_breeze_repo,
)
from .progress import Progress
from .util import (
    InstallerError,
    command_output,
    ensure_dir,
    extract_archive,
    find_file,
    human_bytes,
    is_windows,
    remove_tree,
    run,
    run_streaming,
    verify_file,
    which,
    write_text,
)


class DockerUnavailable(InstallerError):
    """Docker is missing and the user did not allow an elevated install."""


class DockerPendingReboot(InstallerError):
    """Docker was installed but needs a reboot, re-login or service start."""


# --------------------------------------------------------------------------- Breeze


def install_breeze_repo(
    paths: InstallPaths,
    pins: dict,
    progress: Progress,
    *,
    home: Path | None = None,
) -> tuple[Path, bool]:
    """Install the pinned Breeze repo code.

    Alignment needs ``breeze_infer`` even when Breeze English is not selected:
    the vendored ``breeze_drama`` module hardcodes ``~/code/breeze-tts``.
    """
    meta = pins["breeze"]["repo"]
    commit = str(meta["commit"])
    dest = legacy_breeze_repo(home)
    markers = [dest / "breeze_infer" / "runtime.py", dest / "models" / "fast_streaming.py"]
    marker = dest / ".dramallama-commit"
    if all(path.is_file() for path in markers):
        existing = ""
        try:
            existing = marker.read_text(encoding="utf-8").strip()
        except OSError:
            pass
        if existing == commit:
            progress.log(f"Breeze repo code already installed at {dest}")
        else:
            progress.log(f"using the existing Breeze checkout at {dest}")
        return dest, False

    created = not dest.exists()
    progress.update("breeze-repo", 0.1, "Downloading the Breeze repo code…")
    archive = paths.cache / "downloads" / f"breeze-tts-{commit[:12]}.tar.gz"
    download(
        str(meta["url"]),
        archive,
        label="Breeze repo code",
        progress=lambda item: progress.download("breeze-repo", item),
        cancel=progress.cancelled,
    )
    progress.update("breeze-repo", 0.5, "Extracting Breeze repo code…")
    import tempfile

    staging = Path(tempfile.mkdtemp(prefix="drama-breeze-repo-"))
    try:
        extract_archive(archive, staging)
        root = staging / str(meta.get("archive_root", ""))
        if not (root / "breeze_infer" / "runtime.py").is_file():
            found = find_file(staging, "runtime.py")
            if found is None or found.parent.name != "breeze_infer":
                raise InstallerError("the Breeze repo archive has an unexpected layout")
            root = found.parent.parent
        ensure_dir(dest)
        shutil.copytree(root, dest, dirs_exist_ok=True)
        write_text(marker, commit + "\n")
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    progress.update("breeze-repo", 1.0, f"Breeze repo code ready at {dest}")
    return dest, created


def install_breeze_en(
    paths: InstallPaths,
    pins: dict,
    progress: Progress,
    *,
    home: Path | None = None,
) -> tuple[Path, bool]:
    """Install the checkpoint and report whether anything was downloaded."""
    meta = pins["breeze"]["model"]
    destination = legacy_breeze_model(home)
    destination.mkdir(parents=True, exist_ok=True)
    files = {
        name: item
        for name, item in meta["files"].items()
        if not name.startswith("assets/")
    }
    total = sum(int(item.get("size", 0)) for item in files.values())
    done_before = 0
    downloaded = False
    progress.update("breeze-en", 0.01, f"Checking Breeze English ({human_bytes(total)})…")
    for name, item in files.items():
        size = int(item.get("size", 0)) or None
        sha = item.get("sha256")
        target = destination / name
        if verify_file(target, size=size, sha256=sha, cancel=progress.cancelled):
            done_before += size or 0
            continue

        def _report(update, *, base=done_before, item_size=size or 0, name=name) -> None:
            done = base + (min(update.done, item_size) if item_size else update.done)
            progress.update("breeze-en", done / total if total else 0.0, f"Breeze: {name}")

        downloaded = True
        download(
            hf_url(str(meta["repo"]), str(meta["revision"]), name),
            target,
            sha256=str(sha) if sha else None,
            size=size,
            label=f"Breeze {name}",
            progress=_report,
            cancel=progress.cancelled,
            retries=4,
        )
        done_before += size or target.stat().st_size

    required = [
        "config.json",
        "audio_tokenizer/model.safetensors",
        "model-00001-of-00002.safetensors",
        "model-00002-of-00002.safetensors",
        "tokenizer.json",
    ]
    missing = [name for name in required if not (destination / name).is_file()]
    if missing:
        raise InstallerError(f"Breeze English checkpoint is incomplete: {', '.join(missing)}")
    progress.update("breeze-en", 1.0, f"Breeze English ready at {destination}")
    return destination, downloaded


def install_breeze_de(
    paths: InstallPaths,
    pins: dict,
    system: SystemInfo,
    progress: Progress,
    *,
    device: str,
    allow_elevated: bool = False,
    home: Path | None = None,
) -> tuple[Path, Path, bool, bool]:
    """Return ``(binary, model, cpu_fallback, created)``.

    ``created`` is true when the installer downloaded the sidecar or the GGUF,
    so an uninstall only removes assets it owns.
    """
    meta = pins["breeze_de"]
    asset = meta["assets"].get(system.platform_key)
    if not asset:
        raise InstallerError(f"no Breeze German build pinned for {system.platform_key}")
    root = legacy_breeze_de_root(home)
    target_dir = root / str(asset["extract_dir"])
    binary = target_dir / str(asset["binary"])
    created = False
    progress.update("breeze-de", 0.02, "Checking the Breeze German sidecar…")
    if not binary.is_file():
        archive = paths.cache / "downloads" / Path(str(asset["url"])).name
        download(
            str(asset["url"]),
            archive,
            sha256=str(asset.get("sha256") or None),
            size=asset.get("size"),
            label=f"Breeze-TTS-2.cpp {meta['release']}",
            progress=lambda item: progress.download("breeze-de", item),
            cancel=progress.cancelled,
        )
        progress.update("breeze-de", 0.15, "Extracting the Breeze German sidecar…")
        import tempfile

        staging = Path(tempfile.mkdtemp(prefix="drama-breeze-de-"))
        try:
            extract_archive(archive, staging)
            found = find_file(staging, str(asset["binary"]))
            if found is None:
                raise InstallerError("the Breeze German archive did not contain breeze-server")
            source_root = found.parent
            ensure_dir(root)
            remove_tree(target_dir)
            shutil.copytree(source_root, target_dir)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        binary = target_dir / str(asset["binary"])
        created = True
        if not binary.is_file():
            raise InstallerError("Breeze German binary was not installed correctly")
        if not is_windows(system.os_name):
            binary.chmod(0o755)
    else:
        progress.log(f"Breeze German sidecar already installed at {binary}")

    model_meta = meta["model"]
    model = legacy_breeze_de_model(home)
    model_present = verify_file(
        model,
        size=int(model_meta["size"]),
        sha256=str(model_meta["sha256"]),
        cancel=progress.cancelled,
    )
    progress.update("breeze-de", 0.35, "Downloading the Articulatio-DE GGUF…")
    download(
        hf_url(str(model_meta["repo"]), str(model_meta["revision"]), str(model_meta["file"])),
        model,
        sha256=str(model_meta["sha256"]),
        size=int(model_meta["size"]),
        label="Articulatio-DE Q8_0",
        progress=lambda item: progress.download("breeze-de", item),
        cancel=progress.cancelled,
        retries=4,
    )
    created = created or not model_present

    cpu_fallback = False
    if device == "gpu" and not detect_vulkan(system.os_name):
        progress.log("Vulkan loader not detected; trying to install it (optional)…")
        if allow_elevated and ensure_vulkan_loader(system, progress):
            system.vulkan = True
        if not detect_vulkan(system.os_name):
            cpu_fallback = True
            progress.log(
                "Breeze German will use its CPU backend. "
                "Install libvulkan1/vulkan-loader and re-run to enable Vulkan."
            )
    progress.update("breeze-de", 1.0, f"Breeze German ready at {binary}")
    return binary, model, cpu_fallback, created


def _os_release() -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key] = value.strip().strip('"')
    except OSError:
        pass
    return values


def _elevated(command: list[str], *, timeout: float = 1800) -> bool:
    """Run a package-manager command through pkexec/sudo or a UAC prompt."""
    if is_windows():
        quoted = " ".join(f"'{item}'" for item in command)
        script = (
            "Start-Process -FilePath 'powershell' -ArgumentList "
            f"'-NoProfile','-Command','{quoted}' -Verb RunAs -Wait"
        )
        result = run(["powershell", "-NoProfile", "-Command", script], timeout=timeout)
        return result.returncode == 0
    pkexec = which("pkexec")
    if pkexec:
        result = run([pkexec, *command], timeout=timeout)
        return result.returncode == 0
    sudo = which("sudo")
    if sudo:
        result = run([sudo, "-n", *command], timeout=timeout)
        if result.returncode != 0:
            raise InstallerError(
                "administrator permission is required; run the installer from a "
                "terminal so sudo can ask for a password, or install the package "
                "manually and re-run"
            )
        return True
    raise InstallerError("no pkexec or sudo found; install the package manually")


def ensure_vulkan_loader(system: SystemInfo, progress: Progress) -> bool:
    if system.os_name != "linux":
        return False
    release = _os_release()
    family = " ".join(
        [release.get("ID", ""), release.get("ID_LIKE", "")]
    ).lower()
    if "debian" in family or "ubuntu" in family:
        command = ["sh", "-c", "apt-get update && apt-get install -y libvulkan1"]
    elif "fedora" in family or "rhel" in family:
        command = ["dnf", "install", "-y", "vulkan-loader"]
    elif "arch" in family:
        command = ["pacman", "-S", "--noconfirm", "vulkan-icd-loader"]
    elif "suse" in family:
        command = ["zypper", "--non-interactive", "install", "libvulkan1"]
    else:
        progress.log("unknown Linux distribution; install the Vulkan loader manually")
        return False
    try:
        return _elevated(command)
    except InstallerError as exc:
        progress.log(f"could not install the Vulkan loader: {exc}")
        return False


# --------------------------------------------------------------------------- Docker


def _docker_exe(system: SystemInfo) -> str | None:
    exe = which("docker")
    if exe:
        return exe
    if is_windows(system.os_name):
        candidates = [
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
            / "Docker"
            / "Docker"
            / "resources"
            / "bin"
            / "docker.exe",
            Path(os.environ.get("LOCALAPPDATA", ""))
            / "Docker"
            / "Docker"
            / "resources"
            / "bin"
            / "docker.exe",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
    return None


def docker_daemon_ready(exe: str) -> bool:
    result = run([exe, "info", "--format", "{{.ServerVersion}}"], timeout=10)
    return result.returncode == 0


def _start_docker_desktop(system: SystemInfo, progress: Progress) -> bool:
    if not is_windows(system.os_name):
        return False
    candidates = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "Docker"
        / "Docker"
        / "Docker Desktop.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            progress.log("starting Docker Desktop…")
            run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"Start-Process -FilePath '{candidate}'",
                ],
                timeout=60,
            )
            for _ in range(60):
                if docker_daemon_ready(_docker_exe(system) or "docker"):
                    return True
                time.sleep(2)
    return False


def _start_linux_docker(progress: Progress) -> bool:
    systemctl = which("systemctl")
    if not systemctl:
        return False
    progress.log("starting the Docker service…")
    if run([systemctl, "start", "docker"], timeout=120).returncode == 0:
        return True
    try:
        _elevated([systemctl, "enable", "--now", "docker"], timeout=300)
        return True
    except InstallerError as exc:
        progress.log(str(exc))
        return False


def install_docker(system: SystemInfo, progress: Progress) -> None:
    """Best-effort elevated Docker installation. Usually requires a reboot."""
    if is_windows(system.os_name):
        if system.wsl2 is False:
            progress.log("enabling WSL2 (administrator prompt)…")
            try:
                _elevated(["wsl", "--install", "--no-distribution"], timeout=1800)
            except InstallerError as exc:
                progress.log(f"WSL2 setup did not complete: {exc}")
        progress.log("installing Docker Desktop with winget (UAC prompt)…")
        ok = _elevated(
            [
                "winget",
                "install",
                "--id",
                "Docker.DockerDesktop",
                "-e",
                "--accept-package-agreements",
                "--accept-source-agreements",
                "--silent",
            ],
            timeout=3600,
        )
        if not ok:
            raise DockerUnavailable(
                "Docker Desktop could not be installed automatically. Install it "
                "from https://www.docker.com/products/docker-desktop/ and re-run "
                "the installer to add Chatterbox."
            )
        raise DockerPendingReboot(
            "Docker Desktop was installed. Reboot (or finish its first-run setup) "
            "and re-run the installer to finish Chatterbox."
        )

    release = _os_release()
    family = " ".join([release.get("ID", ""), release.get("ID_LIKE", "")]).lower()
    if "debian" in family or "ubuntu" in family:
        command = [
            "sh",
            "-c",
            (
                "apt-get update && apt-get install -y docker.io docker-compose-v2 "
                "|| apt-get install -y docker.io docker-compose-plugin"
            ),
        ]
    elif "fedora" in family or "rhel" in family:
        command = ["dnf", "install", "-y", "docker", "docker-compose-plugin"]
    elif "arch" in family:
        command = ["pacman", "-S", "--noconfirm", "docker", "docker-compose"]
    elif "suse" in family:
        command = ["zypper", "--non-interactive", "install", "docker", "docker-compose"]
    else:
        raise DockerUnavailable(
            "No supported package manager was detected. Install Docker Engine "
            "manually (https://docs.docker.com/engine/install/) and re-run."
        )
    progress.log("installing Docker Engine (administrator prompt)…")
    if not _elevated(command, timeout=3600):
        raise DockerUnavailable(
            "Docker installation failed. Install Docker Engine manually and re-run."
        )
    try:
        _elevated(["systemctl", "enable", "--now", "docker"], timeout=300)
        _elevated(["usermod", "-aG", "docker", os.environ.get("USER", "")], timeout=120)
    except InstallerError as exc:
        progress.log(f"post-install Docker setup: {exc}")
    raise DockerPendingReboot(
        "Docker Engine was installed. Log out and back in (for the docker group) "
        "and re-run the installer to finish Chatterbox."
    )


def ensure_docker(
    system: SystemInfo,
    progress: Progress,
    *,
    allow_elevated: bool,
) -> str:
    exe = _docker_exe(system)
    if exe and docker_daemon_ready(exe):
        return exe
    if exe:
        progress.log("Docker is installed but its daemon is not responding; trying to start it")
        if is_windows(system.os_name):
            started = _start_docker_desktop(system, progress)
        else:
            started = _start_linux_docker(progress)
        if started and docker_daemon_ready(exe):
            return exe
        raise DockerPendingReboot(
            "Docker is installed but not running. Start Docker and re-run the "
            "installer to finish Chatterbox."
        )
    if not allow_elevated:
        raise DockerUnavailable(
            "Chatterbox needs Docker, which is not installed. Install Docker "
            "manually, or allow the wizard to request administrator permission "
            "to install it."
        )
    install_docker(system, progress)
    raise DockerPendingReboot("Docker was installed; a restart is required.")


def _wait_for_chatterbox(
    container: str,
    *,
    port: int = 4123,
    timeout: float = 1800,
    progress: Progress | None = None,
) -> bool:
    deadline = time.monotonic() + timeout
    started = time.monotonic()
    last_note = 0.0
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/health", timeout=5
            ) as response:
                if response.status == 200:
                    payload = json.loads(response.read().decode("utf-8"))
                    if payload.get("model_loaded"):
                        return True
        except (urllib.error.URLError, OSError, ValueError):
            pass
        now = time.monotonic()
        if progress is not None and now - last_note > 30:
            last_note = now
            progress.log(
                f"Chatterbox {container} is still starting "
                f"({int(now - started)}s); the first run downloads its model"
            )
        time.sleep(2)
    return False


def install_chatterbox(
    paths: InstallPaths,
    pins: dict,
    system: SystemInfo,
    progress: Progress,
    *,
    device: str,
    allow_elevated: bool,
    timeout: float = 1800,
) -> dict:
    meta = pins["chatterbox"]
    variant = "gpu" if device == "gpu" else "cpu"
    variant_meta = meta["variants"][variant]
    image = f"{meta['image']}@{variant_meta['digest']}"
    container = str(meta["container"])
    port = int(meta["port"])
    # The chatterbox-tts-api server passes DEVICE straight to torch, so the
    # accepted values are "cuda"/"cpu" (not "gpu").
    torch_device = "cuda" if variant == "gpu" else "cpu"
    model_volume = str(meta.get("model_volume", "dramallama-chatterbox-cache"))

    exe = ensure_docker(system, progress, allow_elevated=allow_elevated)
    progress.update("chatterbox", 0.03, f"Docker ready; pulling Chatterbox ({variant})…")
    result = run_streaming(
        [exe, "pull", image],
        timeout=5400,
        on_line=progress.log,
    )
    if result.returncode != 0:
        raise InstallerError(
            "could not pull the Chatterbox Docker image; check the Docker "
            "output in the install log"
        )
    progress.update("chatterbox", 0.5, "Creating the Chatterbox container…")
    existing = run(
        [exe, "inspect", "--type", "container", container],
        timeout=30,
    ).returncode == 0
    if existing:
        config = command_output(
            [
                exe,
                "inspect",
                "--format",
                "{{.Config.Image}}|{{range .Config.Env}}{{println .}}{{end}}",
                container,
            ],
            timeout=30,
        )
        current_device = "cuda" if "DEVICE=cuda" in config else "cpu"
        image_ok = image in config or str(variant_meta["tag"]) in config
        if image_ok and current_device == variant:
            progress.log(f"reusing the existing Chatterbox container ({current_device})")
        else:
            progress.log("recreating the Chatterbox container for the new image/device")
            run([exe, "rm", "-f", container], timeout=120)
            existing = False
    if not existing:
        command = [
            exe,
            "create",
            "--name",
            container,
            "--restart",
            "no",
            "-p",
            f"{port}:{port}",
            "-e",
            f"DEVICE={torch_device}",
            "-e",
            "MODEL_CACHE_DIR=/cache",
            "-v",
            f"{model_volume}:/cache",
        ]
        if variant == "gpu":
            command += ["--gpus", "all"]
        command.append(image)
        created = run(command, timeout=120)
        if created.returncode != 0:
            detail = (created.stderr or created.stdout or "").strip()
            if variant == "gpu" and "gpu" in detail.lower():
                raise InstallerError(
                    "Docker could not attach the GPU. Install "
                    "nvidia-container-toolkit, restart Docker and re-run, or "
                    "choose the CPU-only install."
                )
            raise InstallerError(f"could not create the Chatterbox container: {detail}")

    progress.update("chatterbox", 0.6, "Starting Chatterbox (first start downloads its model)…")
    started = run([exe, "start", container], timeout=120)
    if started.returncode != 0:
        detail = (started.stderr or started.stdout or "").strip()
        raise InstallerError(f"could not start the Chatterbox container: {detail}")
    healthy = _wait_for_chatterbox(
        container, port=port, timeout=timeout, progress=progress
    )
    run([exe, "stop", container], timeout=180)
    if not healthy:
        raise InstallerError(
            "Chatterbox did not become healthy in time. The container may still "
            "be downloading its model; start it with `docker start "
            f"{container}` and check `docker logs {container}`."
        )
    progress.update("chatterbox", 1.0, "Chatterbox is installed and verified")
    return {
        "variant": variant,
        "image": image,
        "digest": variant_meta["digest"],
        "container": container,
        "port": port,
    }


def uninstall_chatterbox(
    pins: dict,
    *,
    purge_images: bool = False,
    progress: Progress | None = None,
) -> None:
    exe = which("docker")
    if not exe:
        return
    meta = pins["chatterbox"]
    container = str(meta["container"])
    run([exe, "rm", "-f", container], timeout=120)
    if purge_images:
        for variant in meta["variants"].values():
            ref = f"{meta['image']}@{variant['digest']}"
            run([exe, "rmi", ref], timeout=300)
            if progress:
                progress.log(f"removed Docker image {ref}")
        volume = str(meta.get("model_volume", "dramallama-chatterbox-cache"))
        run([exe, "volume", "rm", volume], timeout=120)
        if progress:
            progress.log(f"removed Docker volume {volume}")
