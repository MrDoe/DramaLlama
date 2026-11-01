"""Windows-specific installer behaviour, simulated on any host.

The installer runs on Windows but is developed on Linux, so these tests drive
the ``system="windows"`` seams explicitly instead of relying on ``os.name``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from installer.core import plan
from installer.core.detect import GpuInfo, SystemInfo
from installer.core.launchers import (
    _linux_desktop,
    _windows_cmd,
    launcher_env,
)
from installer.core.paths import (
    InstallPaths,
    default_prefix,
    runtime_search_path,
)
from installer.core.plan import Selection
from installer.core.util import (
    make_dir_link_or_copy,
    platform_key,
    split_paths,
)


def windows_system(**overrides: object) -> SystemInfo:
    values: dict[str, object] = {
        "os_name": "windows",
        "arch": "AMD64",
        "platform_key": "windows-x86_64",
        "cpu": "test cpu",
        "ram_gb": 32.0,
        "disk_free_gb": 500.0,
        "gpus": [GpuInfo(name="RTX 4090", driver="580.82", vram_mb=16376)],
        "vulkan": True,
        "docker": None,
        "docker_daemon": False,
        "wsl2": True,
        "admin": False,
    }
    values.update(overrides)
    return SystemInfo(**values)  # type: ignore[arg-type]


def test_windows_prefix_uses_localappdata(tmp_path: Path) -> None:
    local = tmp_path / "AppData" / "Local"
    prefix = default_prefix(
        system="windows",
        environ={"LOCALAPPDATA": str(local)},
        home=tmp_path,
    )
    assert prefix == local / "DramaLlama"


def test_windows_prefix_falls_back_to_home(tmp_path: Path) -> None:
    prefix = default_prefix(system="windows", environ={}, home=tmp_path)
    assert prefix == tmp_path / "AppData" / "Local" / "DramaLlama"


def test_windows_prefix_env_override(tmp_path: Path) -> None:
    prefix = default_prefix(
        system="windows",
        environ={"DRAMA_LLAMA_PREFIX": r"D:\LLama"},
        home=tmp_path,
    )
    assert str(prefix) == r"D:\LLama"


def test_windows_runtime_paths(tmp_path: Path) -> None:
    paths = InstallPaths.for_prefix(tmp_path / "DramaLlama", system="windows")
    assert paths.python(system="windows") == paths.venv / "Scripts" / "python.exe"
    assert paths.pythonw(system="windows") == paths.venv / "Scripts" / "pythonw.exe"
    assert paths.uv.name == "uv.exe"
    assert paths.node == paths.node_dir / "node.exe"
    assert paths.npm == paths.node_dir / "npm.cmd"
    assert paths.ffmpeg.name == "ffmpeg.exe"


def test_windows_launcher_cmd_uses_scripts_python_and_crlf(tmp_path: Path) -> None:
    paths = InstallPaths.for_prefix(tmp_path / "DramaLlama")
    env = launcher_env(paths, device="gpu")
    script = _windows_cmd(paths, env)
    assert "\r\n" in script
    assert str(paths.venv / "Scripts" / "python.exe") in script
    assert str(paths.prefix / "launch.py") in script
    assert "set \"DRAMA_LLAMA_DATA=" in script
    assert ";%PATH%" in script
    lines = script.splitlines()
    assert lines[0] == "@echo off"
    assert lines[-1] == "endlocal"


def test_windows_component_plan_matches_pinned_assets() -> None:
    import json

    pins = json.loads((ROOT / "installer" / "pins.json").read_text(encoding="utf-8"))
    for family in ("uv", "node"):
        assert pins[family]["assets"]["windows-x86_64"]["sha256"]
        assert pins[family]["assets"]["windows-x86_64"]["url"].startswith("https://")
    assert pins["breeze_de"]["assets"]["windows-x86_64"]["sha256"]
    assert pins["breeze_de"]["assets"]["windows-x86_64"]["binary"].endswith(".exe")
    steps = plan.build_steps(
        Selection(
            components=frozenset({plan.CORE, plan.BREEZE_DE}),
            accept_breeze_license=True,
        ),
        pins,
        windows_system(),
    )
    breeze = next(step for step in steps if step.id == plan.BREEZE_DE)
    assert breeze.download_bytes > 3_000_000_000


def test_platform_key_for_windows_simulations() -> None:
    assert platform_key("windows", "AMD64") == "windows-x86_64"
    assert platform_key("windows", "ARM64") == "windows-arm64"


def test_split_windows_path_list() -> None:
    paths = split_paths(r"C:\Sounds;D:\SFX", sep=";")
    assert [str(path) for path in paths] == [r"C:\Sounds", r"D:\SFX"]


def test_linux_desktop_entry_quotes_exec_paths(tmp_path: Path) -> None:
    paths = InstallPaths.for_prefix(tmp_path / "with space")
    desktop = _linux_desktop(paths)
    assert "[Desktop Entry]" in desktop
    assert "Icon=drama-llama" in desktop
    assert f'Exec="{paths.python(system="linux")}" "{paths.prefix / "launch.py"}" ui' in desktop


def test_runtime_search_path_order() -> None:
    paths = InstallPaths.for_prefix("/opt/llama")
    windows = runtime_search_path(paths, system="windows")
    linux = runtime_search_path(paths, system="linux")
    assert windows[2] == paths.venv / "Scripts"
    assert linux[1] == paths.node_dir / "bin"
    assert linux[2] == paths.venv / "bin"


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink behaviour")
def test_make_dir_link_or_copy_links_on_posix(tmp_path: Path) -> None:
    source = tmp_path / "third_party" / "bettersuno-mcp"
    (source / "src").mkdir(parents=True)
    (source / "package.json").write_text("{}", encoding="utf-8")
    destination = tmp_path / "suno-bridge" / "node_modules" / "bettersuno-mcp"
    make_dir_link_or_copy(source, destination)
    assert destination.is_symlink()
    assert (destination / "package.json").is_file()
    # Re-running replaces the existing link instead of failing.
    make_dir_link_or_copy(source, destination)
    assert destination.is_symlink()
