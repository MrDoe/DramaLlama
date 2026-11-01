"""Component catalogue, selection validation and the install plan."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .detect import SystemInfo, cuda_channel
from .paths import InstallPaths
from .util import InstallerError, human_bytes, is_windows

CORE = "core"
BREEZE_EN = "breeze-en"
BREEZE_DE = "breeze-de"
CHATTERBOX = "chatterbox"
ALL_COMPONENTS = (CORE, BREEZE_EN, BREEZE_DE, CHATTERBOX)


@dataclass(frozen=True)
class ComponentSpec:
    id: str
    title: str
    description: str
    required: bool = False
    licence: str = ""
    needs_docker: bool = False
    needs_gpu_recommended: bool = False


COMPONENTS: dict[str, ComponentSpec] = {
    CORE: ComponentSpec(
        id=CORE,
        title="Drama Llama core",
        description=(
            "App and web UI, Python 3.12 + Node 22 runtimes, PyTorch, Whisper "
            "large-v3 alignment, ffmpeg, the Suno bridge and the Breeze repo code."
        ),
        required=True,
        licence="Drama Llama is provided as-is. Bundled third-party notices are in NOTICE.md.",
    ),
    BREEZE_EN: ComponentSpec(
        id=BREEZE_EN,
        title="Breeze English (Breeze TTS 2)",
        description=(
            "English voice design and reference cloning. Downloads the 7.2 GiB "
            "checkpoint. CUDA strongly recommended; CPU works but is very slow."
        ),
        licence=(
            "Breeze TTS 2 model weights are released for research and "
            "non-commercial use. By installing this component you accept the "
            "model licence shown at https://huggingface.co/BreezeBlue/Breeze-TTS-2."
        ),
        needs_gpu_recommended=True,
    ),
    BREEZE_DE: ComponentSpec(
        id=BREEZE_DE,
        title="Breeze German (Articulatio-DE)",
        description=(
            "German Breeze sidecar (Breeze-TTS-2.cpp v0.1.6) with the "
            "Articulatio-DE Q8_0 GGUF. Runs on Vulkan or CPU."
        ),
        licence=(
            "The Breeze-TTS-2.cpp binaries and the Articulatio-DE GGUF are "
            "research/non-commercial releases. Check the upstream licence before "
            "any commercial use."
        ),
    ),
    CHATTERBOX: ComponentSpec(
        id=CHATTERBOX,
        title="Chatterbox (Docker)",
        description=(
            "Chatterbox Multilingual voice cloning in Docker. Uses the NVIDIA "
            "image on GPU machines and the smaller CPU image otherwise."
        ),
        needs_docker=True,
        licence="Chatterbox and the chatterbox-tts-api image carry their own licences.",
    ),
}

DEVICE_CHOICES = ("auto", "gpu", "cpu")


@dataclass
class Selection:
    components: frozenset[str] = frozenset({CORE})
    device: str = "auto"
    prefix: Path = field(default_factory=lambda: Path.home())
    data_dir: Path | None = None
    accept_breeze_license: bool = False
    allow_docker_install: bool = False

    def has(self, component: str) -> bool:
        return component in self.components

    @property
    def data(self) -> Path:
        return self.data_dir if self.data_dir is not None else self.prefix / "data"

    def paths(self) -> InstallPaths:
        return InstallPaths.for_prefix(self.prefix)

    def with_components(self, components: list[str]) -> Selection:
        return Selection(
            components=frozenset(components),
            device=self.device,
            prefix=self.prefix,
            data_dir=self.data_dir,
            accept_breeze_license=self.accept_breeze_license,
            allow_docker_install=self.allow_docker_install,
        )


@dataclass(frozen=True)
class Step:
    id: str
    title: str
    component: str
    weight: float
    optional: bool = False
    download_bytes: int = 0


def parse_components(value: str | None) -> list[str]:
    if not value or value.strip().lower() in {"all", "*"}:
        return list(ALL_COMPONENTS)
    requested = [item.strip().lower() for item in value.split(",") if item.strip()]
    unknown = [item for item in requested if item not in COMPONENTS]
    if unknown:
        raise InstallerError(
            f"unknown component(s): {', '.join(unknown)}; "
            f"choose from {', '.join(ALL_COMPONENTS)}"
        )
    if CORE not in requested:
        requested.insert(0, CORE)
    return requested


def core_download_bytes(pins: dict, system: SystemInfo, device: str) -> int:
    sizes = pins.get("install_sizes", {})
    node = sizes.get("node_windows" if system.os_name == "windows" else "node_linux", 0)
    wheels = sizes.get("core_wheels_gpu" if device == "gpu" else "core_wheels_cpu", 0)
    whisper = int(pins.get("whisper", {}).get("size", 0))
    return int(
        sizes.get("uv", 0)
        + node
        + sizes.get("python", 0)
        + wheels
        + sizes.get("ffmpeg", 0)
        + sizes.get("breeze_repo", 0)
        + whisper
    )


def download_estimate(selection: Selection, pins: dict, system: SystemInfo) -> int:
    device = effective_device(selection, system)
    total = core_download_bytes(pins, system, device)
    if selection.has(BREEZE_EN):
        total += int(pins.get("breeze", {}).get("model", {}).get("size", 0))
    if selection.has(BREEZE_DE):
        asset = pins.get("breeze_de", {}).get("assets", {}).get(system.platform_key, {})
        model = pins.get("breeze_de", {}).get("model", {})
        total += int(asset.get("size", 0)) + int(model.get("size", 0))
    if selection.has(CHATTERBOX):
        variant = chatterbox_variant(device)
        meta = pins.get("chatterbox", {}).get("variants", {}).get(variant, {})
        total += int(meta.get("size", 0))
    return total


def installed_estimate(selection: Selection, pins: dict, system: SystemInfo) -> int:
    # Wheels unpack to roughly their download size; leave headroom for the
    # venv, caches and the Docker model download.
    return download_estimate(selection, pins, system) + 2 * 1024**3


def chatterbox_variant(device: str) -> str:
    return "gpu" if device == "gpu" else "cpu"


def effective_device(selection: Selection, system: SystemInfo) -> str:
    if selection.device == "cpu":
        return "cpu"
    if selection.device == "gpu":
        if not system.has_gpu:
            raise InstallerError(
                "GPU mode was requested, but no NVIDIA GPU is visible. "
                "Use --cpu to install a CPU-only setup."
            )
        return "gpu"
    return "gpu" if system.has_gpu else "cpu"


def validate_selection(selection: Selection, pins: dict, system: SystemInfo) -> list[str]:
    missing = [name for name in selection.components if name not in COMPONENTS]
    if missing:
        raise InstallerError(f"unknown component(s): {', '.join(missing)}")
    if CORE not in selection.components:
        raise InstallerError("the core component is required")
    if selection.device not in DEVICE_CHOICES:
        raise InstallerError(f"device must be one of {', '.join(DEVICE_CHOICES)}")
    breezes = [name for name in (BREEZE_EN, BREEZE_DE) if selection.has(name)]
    if breezes and not selection.accept_breeze_license:
        raise InstallerError(
            "Breeze is a research/non-commercial release. Re-run with "
            "--accept-breeze-license (or tick the licence box in the wizard) "
            "to install it."
        )
    device = effective_device(selection, system)
    warnings: list[str] = []
    if device == "cpu" and selection.has(BREEZE_EN):
        warnings.append(
            "Breeze English on CPU is functional but very slow (minutes per "
            "line). A CUDA GPU is strongly recommended."
        )
    if selection.has(BREEZE_EN) and system.os_name == "windows":
        warnings.append(
            "The Breeze English path is unverified on Windows. Treat it as "
            "experimental until you have rendered a test stem."
        )
    if selection.has(BREEZE_DE) and not system.vulkan:
        warnings.append(
            "No Vulkan loader detected; Breeze German will run on CPU. "
            "Install the Vulkan runtime for GPU acceleration."
        )
    if selection.has(CHATTERBOX) and not system.docker_daemon:
        warnings.append(
            "Chatterbox needs Docker. The installer can request an elevated "
            "Docker installation, then a reboot/re-login is usually required."
        )
    estimate = installed_estimate(selection, pins, system)
    if system.disk_free_gb and estimate / (1024**3) > system.disk_free_gb:
        warnings.append(
            f"Only {system.disk_free_gb:.1f} GiB free; this selection needs "
            f"about {human_bytes(estimate)}. Free disk space first."
        )
    if is_windows(system.os_name) and selection.prefix.drive.upper() not in {"", "C:"}:
        warnings.append("Installing on a non-system drive is supported but less tested.")
    return warnings


def torch_channel_for(selection: Selection, pins: dict, system: SystemInfo) -> str:
    requested = effective_device(selection, system)
    driver = system.nvidia.driver if system.nvidia else None
    return cuda_channel(pins, driver, requested)


def build_steps(selection: Selection, pins: dict, system: SystemInfo) -> list[Step]:
    device = effective_device(selection, system)
    steps = [
        Step("payload", "Unpack the app", CORE, 2, download_bytes=0),
        Step("runtimes", "Install Python, uv and Node", CORE, 6, download_bytes=(
            pins.get("install_sizes", {}).get("uv", 0)
            + pins.get("install_sizes", {}).get(
                "node_windows" if system.os_name == "windows" else "node_linux", 0
            )
            + pins.get("install_sizes", {}).get("python", 0)
        )),
        Step("python", "Install the Python audio stack", CORE, 32, download_bytes=(
            pins.get("install_sizes", {}).get(
                "core_wheels_gpu" if device == "gpu" else "core_wheels_cpu", 0
            )
        )),
        Step("whisper", "Download Whisper large-v3", CORE, 24, download_bytes=int(
            pins.get("whisper", {}).get("size", 0)
        )),
        Step("ffmpeg", "Prepare ffmpeg", CORE, 2, download_bytes=int(
            pins.get("install_sizes", {}).get("ffmpeg", 0)
        )),
        Step("breeze-repo", "Install the Breeze repo code", CORE, 1, download_bytes=int(
            pins.get("install_sizes", {}).get("breeze_repo", 0)
        )),
    ]
    if selection.has(BREEZE_EN):
        steps.append(Step(
            BREEZE_EN,
            "Download Breeze English weights",
            BREEZE_EN,
            22,
            optional=True,
            download_bytes=int(pins.get("breeze", {}).get("model", {}).get("size", 0)),
        ))
    if selection.has(BREEZE_DE):
        asset = pins.get("breeze_de", {}).get("assets", {}).get(system.platform_key, {})
        model = pins.get("breeze_de", {}).get("model", {})
        steps.append(Step(
            BREEZE_DE,
            "Install Breeze German",
            BREEZE_DE,
            12,
            optional=True,
            download_bytes=int(asset.get("size", 0)) + int(model.get("size", 0)),
        ))
    if selection.has(CHATTERBOX):
        meta = pins.get("chatterbox", {}).get(
            "variants", {}
        ).get(chatterbox_variant(device), {})
        steps.append(Step(
            CHATTERBOX,
            "Install Chatterbox in Docker",
            CHATTERBOX,
            14,
            optional=True,
            download_bytes=int(meta.get("size", 0)),
        ))
    steps.extend([
        Step("launchers", "Create launchers and shortcuts", CORE, 1),
        Step("verify", "Verify the installation", CORE, 2),
    ])
    return steps


def plan_payload(selection: Selection, pins: dict, system: SystemInfo) -> dict:
    device = effective_device(selection, system)
    steps = build_steps(selection, pins, system)
    return {
        "app": "Drama Llama",
        "components": sorted(selection.components),
        "device": device,
        "torch_channel": torch_channel_for(selection, pins, system),
        "prefix": str(selection.prefix),
        "data_dir": str(selection.data),
        "download_bytes": download_estimate(selection, pins, system),
        "installed_bytes": installed_estimate(selection, pins, system),
        "steps": [
            {
                "id": step.id,
                "title": step.title,
                "component": step.component,
                "weight": step.weight,
                "optional": step.optional,
                "download_bytes": step.download_bytes,
            }
            for step in steps
        ],
        "system": system.as_dict(),
    }


def step_weights(steps: list[Step]) -> list[tuple[str, float]]:
    return [(step.id, step.weight) for step in steps]
