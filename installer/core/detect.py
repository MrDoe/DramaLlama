"""Machine, GPU and service detection. Read-only and best-effort."""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .paths import default_prefix, legacy_breeze_de_binary, whisper_cache_root
from .util import InstallerError, human_bytes, is_windows, platform_key, run


@dataclass
class GpuInfo:
    name: str
    driver: str
    vram_mb: int = 0
    vendor: str = "nvidia"

    @property
    def driver_major(self) -> int:
        match = re.match(r"(\d+)", self.driver)
        return int(match.group(1)) if match else 0

    @property
    def vram_text(self) -> str:
        return human_bytes(self.vram_mb * 1024 * 1024) if self.vram_mb else "unknown VRAM"


@dataclass
class SystemInfo:
    os_name: str
    arch: str
    platform_key: str
    cpu: str
    ram_gb: float
    disk_free_gb: float
    gpus: list[GpuInfo] = field(default_factory=list)
    vulkan: bool = False
    docker: str | None = None
    docker_daemon: bool = False
    wsl2: bool | None = None
    ffmpeg: str | None = None
    ffmpeg_version: str | None = None
    admin: bool = False
    existing_prefix: Path | None = None
    whisper_present: bool = False

    @property
    def nvidia(self) -> GpuInfo | None:
        return self.gpus[0] if self.gpus else None

    @property
    def has_gpu(self) -> bool:
        return bool(self.gpus)

    @property
    def gpu_summary(self) -> str:
        if not self.gpus:
            return "No NVIDIA GPU detected"
        return ", ".join(f"{gpu.name} ({gpu.vram_text}, driver {gpu.driver})" for gpu in self.gpus)

    @property
    def docker_summary(self) -> str:
        if self.docker is None:
            return "not installed"
        return "running" if self.docker_daemon else "installed, daemon not running"

    def as_dict(self) -> dict:
        return {
            "os": self.os_name,
            "arch": self.arch,
            "platform": self.platform_key,
            "cpu": self.cpu,
            "ram_gb": round(self.ram_gb, 1),
            "disk_free_gb": round(self.disk_free_gb, 1),
            "gpus": [
                {
                    "name": gpu.name,
                    "driver": gpu.driver,
                    "vram_mb": gpu.vram_mb,
                    "vendor": gpu.vendor,
                }
                for gpu in self.gpus
            ],
            "vulkan": self.vulkan,
            "docker": self.docker,
            "docker_daemon": self.docker_daemon,
            "wsl2": self.wsl2,
            "ffmpeg": self.ffmpeg_version,
            "admin": self.admin,
            "existing_prefix": str(self.existing_prefix) if self.existing_prefix else None,
            "whisper_present": self.whisper_present,
        }


def _run_quiet(command: list[str], timeout: float = 5.0) -> str:
    try:
        result = run(command, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


def detect_nvidia() -> list[GpuInfo]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        candidates = [
            Path(r"C:\Windows\System32\nvidia-smi.exe"),
            Path("/usr/bin/nvidia-smi"),
            Path("/usr/local/bin/nvidia-smi"),
        ]
        exe = next((str(path) for path in candidates if path.is_file()), None)
    if not exe:
        return []
    output = _run_quiet(
        [
            exe,
            "--query-gpu=name,driver_version,memory.total",
            "--format=csv,noheader,nounits",
        ],
        timeout=8.0,
    )
    gpus: list[GpuInfo] = []
    for line in output.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            vram = int(float(parts[2]))
        except ValueError:
            vram = 0
        gpus.append(GpuInfo(name=parts[0], driver=parts[1], vram_mb=vram))
    return gpus


def detect_vulkan(system: str | None = None) -> bool:
    sysname = system or os.name
    if is_windows(sysname):
        try:
            ctypes.WinDLL("vulkan-1.dll")  # type: ignore[attr-defined]
            return True
        except OSError:
            return False
    if str(sysname).lower() == "darwin":
        return False
    try:
        return ctypes.util.find_library("vulkan") is not None
    except OSError:
        return False


def _ram_gb() -> float:
    if is_windows():
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
            return status.ullTotalPhys / (1024**3)
        return 0.0
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return pages * page_size / (1024**3)
    except (AttributeError, ValueError, OSError):
        return 0.0


def _disk_free_gb(path: Path) -> float:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    try:
        return shutil.disk_usage(candidate).free / (1024**3)
    except OSError:
        return 0.0


def _is_admin(system: str | None = None) -> bool:
    if is_windows(system):
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            return False
    return hasattr(os, "geteuid") and os.geteuid() == 0


def detect_docker(system: str | None = None) -> tuple[str | None, bool]:
    exe = shutil.which("docker")
    if not exe and is_windows(system):
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
        exe = next((str(path) for path in candidates if path.is_file()), None)
    if not exe:
        return None, False
    version = _run_quiet([exe, "info", "--format", "{{.ServerVersion}}"], timeout=6.0)
    return exe, bool(version)


def _detect_wsl2() -> bool | None:
    if not is_windows():
        return None
    exe = shutil.which("wsl") or shutil.which("wsl.exe")
    if not exe:
        return False
    output = _run_quiet([exe, "-l", "-v"], timeout=8.0)
    return bool(output) and "2" in output


def _ffmpeg_version(exe: str) -> str | None:
    output = _run_quiet([exe, "-version"], timeout=8.0)
    match = re.search(r"ffmpeg version (\S+)", output)
    return match.group(1) if match else (output.splitlines()[0] if output else None)


def detect_system(prefix: str | Path | None = None) -> SystemInfo:
    sysname = "windows" if is_windows() else platform.system().lower()
    machine = platform.machine()
    install_prefix = Path(prefix).expanduser() if prefix else default_prefix()
    ffmpeg = shutil.which("ffmpeg")
    docker, daemon = detect_docker()
    whisper = (
        whisper_cache_root()
        / "models--Systran--faster-whisper-large-v3"
        / "snapshots"
    )
    return SystemInfo(
        os_name=sysname,
        arch=machine,
        platform_key=platform_key(),
        cpu=platform.processor() or platform.machine() or "unknown",
        ram_gb=_ram_gb(),
        disk_free_gb=_disk_free_gb(install_prefix),
        gpus=detect_nvidia(),
        vulkan=detect_vulkan(sysname),
        docker=docker,
        docker_daemon=daemon,
        wsl2=_detect_wsl2(),
        ffmpeg=ffmpeg,
        ffmpeg_version=_ffmpeg_version(ffmpeg) if ffmpeg else None,
        admin=_is_admin(sysname),
        existing_prefix=install_prefix if (install_prefix / "install-state.json").is_file() else None,
        whisper_present=any(whisper.glob("*")),
    )


def cuda_channel(pins: dict, driver: str | int | None, requested: str = "auto") -> str:
    """Map a driver version to the pinned torch wheel channel.

    ``auto`` uses CUDA when an NVIDIA driver is present, else CPU. ``gpu``
    still falls back to CPU when no channel is compatible (caller warns);
    ``cpu`` always wins.
    """
    if requested == "cpu":
        return "cpu"
    if driver in (None, ""):
        return "cpu"
    try:
        major = int(str(driver).split(".")[0])
    except ValueError:
        return "cpu"
    channels = pins.get("torch", {}).get("channels", {})
    candidates = [
        (name, int(meta.get("min_driver", 0)))
        for name, meta in channels.items()
        if name != "cpu"
    ]
    compatible = [name for name, minimum in candidates if minimum <= major]
    if not compatible:
        if requested == "gpu":
            raise InstallerError(
                f"NVIDIA driver {driver} is too old for the pinned CUDA wheels; "
                "update the driver or choose the CPU-only install."
            )
        return "cpu"
    return max(compatible, key=lambda name: int(channels[name].get("min_driver", 0)))


def torch_index(pins: dict, channel: str) -> str:
    meta = pins.get("torch", {}).get("channels", {}).get(channel)
    if not meta:
        raise InstallerError(f"unknown torch channel {channel!r}")
    return str(meta["index"])


def recommended_device(system: SystemInfo) -> str:
    return "gpu" if system.has_gpu else "cpu"


def check_ports(ports: list[int]) -> dict[int, bool]:
    import socket

    result: dict[int, bool] = {}
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.3)
            result[port] = sock.connect_ex(("127.0.0.1", port)) == 0
    return result


def breeze_de_binary(system: SystemInfo, home: Path | None = None) -> Path:
    return legacy_breeze_de_binary(home, windows=system.os_name == "windows")


def summary_lines(system: SystemInfo) -> list[tuple[str, str]]:
    return [
        ("Operating system", f"{system.os_name} ({system.arch})"),
        ("CPU / RAM", f"{system.cpu} / {system.ram_gb:.0f} GiB"),
        ("Free disk", f"{system.disk_free_gb:.1f} GiB"),
        ("GPU", system.gpu_summary),
        ("Vulkan", "available" if system.vulkan else "not detected"),
        ("Docker", system.docker_summary),
        ("ffmpeg", system.ffmpeg_version or "not on PATH (installer provides one)"),
        ("Existing install", str(system.existing_prefix) if system.existing_prefix else "none"),
    ]


def python_version_of(python: Path) -> str | None:
    if not python.is_file():
        return None
    output = _run_quiet([str(python), "-c", "import sys; print(sys.version.split()[0])"], 10.0)
    return output or None


if __name__ == "__main__":  # pragma: no cover - manual diagnostic
    print(sys.version)
