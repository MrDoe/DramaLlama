"""Tests for the dependency-free installer core.

The GUI is deliberately not imported here: PySide6 is a build/run dependency of
the wizard only, so the core must stay testable on a bare Python install.
"""

from __future__ import annotations

import io
import json
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from installer.core import plan, state, verify
from installer.core.detect import GpuInfo, SystemInfo, cuda_channel
from installer.core.download import download, hf_url
from installer.core.engines import DockerUnavailable, ensure_docker
from installer.core.launchers import launcher_env
from installer.core.paths import (
    InstallPaths,
    default_prefix,
    legacy_breeze_de_binary,
    legacy_breeze_de_model,
    legacy_breeze_model,
    legacy_breeze_repo,
    resolve_prefix,
    runtime_search_path,
    whisper_cache_root,
)
from installer.core.plan import Selection, parse_components
from installer.core.progress import DownloadProgress, Progress
from installer.core.uninstall import _purge_models, uninstall
from installer.core.util import (
    Cancelled,
    DownloadError,
    InstallerError,
    app_version,
    human_bytes,
    human_duration,
    is_windows,
    join_paths,
    platform_key,
    safe_extract_tar,
    safe_extract_zip,
    sha256_file,
    split_paths,
    verify_file,
)

PINS_PATH = ROOT / "installer" / "pins.json"


def load_pins() -> dict:
    return json.loads(PINS_PATH.read_text(encoding="utf-8"))


def make_system(**overrides: object) -> SystemInfo:
    values: dict[str, object] = {
        "os_name": "linux",
        "arch": "x86_64",
        "platform_key": "linux-x86_64",
        "cpu": "test cpu",
        "ram_gb": 32.0,
        "disk_free_gb": 500.0,
        "gpus": [],
        "vulkan": False,
        "docker": None,
        "docker_daemon": False,
        "wsl2": None,
        "admin": False,
    }
    values.update(overrides)
    return SystemInfo(**values)  # type: ignore[arg-type]


def make_gpu_system(**overrides: object) -> SystemInfo:
    return make_system(
        gpus=[GpuInfo(name="RTX 4090", driver="580.82", vram_mb=16376)],
        **overrides,
    )


# --------------------------------------------------------------------------- basics


def test_human_bytes_and_duration() -> None:
    assert human_bytes(0) == "0 B"
    assert human_bytes(1023) == "1023 B"
    assert human_bytes(1024) == "1.0 KiB"
    assert human_bytes(5 * 1024**3) == "5.0 GiB"
    assert human_duration(9) == "9s"
    assert human_duration(61) == "1m 01s"
    assert human_duration(3 * 3600 + 4 * 60) == "3h 04m"


def test_platform_key_simulates_windows_and_macos() -> None:
    assert platform_key("linux", "x86_64") == "linux-x86_64"
    assert platform_key("windows", "AMD64") == "windows-x86_64"
    assert platform_key("nt", "x64") == "windows-x86_64"
    assert platform_key("darwin", "arm64") == "macos-arm64"


def test_is_windows_accepts_simulated_names() -> None:
    assert is_windows("windows")
    assert is_windows("nt")
    assert is_windows("win32")
    assert not is_windows("linux")


def test_split_and_join_paths_use_the_requested_separator() -> None:
    windows = split_paths(r"C:\Sounds;D:\FX", sep=";")
    assert [str(path) for path in windows] == [r"C:\Sounds", r"D:\FX"]
    posix = split_paths("/tmp/a:/tmp/b", sep=":")
    assert [str(path) for path in posix] == ["/tmp/a", "/tmp/b"]
    assert join_paths([Path("/a"), Path("/b")], sep=":") == "/a:/b"


def test_app_version_reads_the_package_version() -> None:
    version = app_version(ROOT)
    assert version and version[0].isdigit()


# --------------------------------------------------------------------------- paths


def test_default_prefix_linux_windows_and_macos(tmp_path: Path) -> None:
    home = tmp_path / "home"
    linux = default_prefix(system="linux", environ={}, home=home)
    assert linux == home / ".local" / "share" / "drama-llama"
    xdg = default_prefix(system="linux", environ={"XDG_DATA_HOME": "/data"}, home=home)
    assert xdg == Path("/data/drama-llama")
    windows = default_prefix(
        system="windows",
        environ={"LOCALAPPDATA": r"C:\Users\me\AppData\Local"},
        home=home,
    )
    assert windows == Path(r"C:\Users\me\AppData\Local/DramaLlama")
    macos = default_prefix(system="darwin", environ={}, home=home)
    assert macos == home / "Library" / "Application Support" / "DramaLlama"


def test_prefix_env_override_wins(tmp_path: Path) -> None:
    prefix = default_prefix(
        system="linux",
        environ={"DRAMA_LLAMA_PREFIX": str(tmp_path / "custom")},
        home=tmp_path,
    )
    assert prefix == tmp_path / "custom"


def test_install_layout_and_launchers(tmp_path: Path) -> None:
    paths = InstallPaths.for_prefix(tmp_path / "prefix")
    paths.ensure()
    assert paths.app == paths.prefix / "app"
    assert paths.data.is_dir()
    assert paths.logs.is_dir()
    assert paths.python(system="linux") == paths.venv / "bin" / "python"
    assert paths.pythonw(system="windows") == paths.venv / "Scripts" / "pythonw.exe"
    assert paths.ffmpeg.name in {"ffmpeg", "ffmpeg.exe"}
    assert runtime_search_path(paths, system="windows") == [
        paths.bin,
        paths.node_dir,
        paths.venv / "Scripts",
    ]
    assert runtime_search_path(paths, system="linux")[1] == paths.node_dir / "bin"


def test_legacy_engine_paths_are_stable(tmp_path: Path) -> None:
    home = tmp_path / "home"
    assert legacy_breeze_repo(home) == home / "code" / "breeze-tts"
    assert legacy_breeze_model(home) == home / "models" / "Breeze-TTS-2"
    assert legacy_breeze_de_binary(home, windows=False).name == "breeze-server"
    assert legacy_breeze_de_binary(home, windows=True).name == "breeze-server.exe"
    assert legacy_breeze_de_model(home).name.endswith("Q8_0.gguf")


def test_runtime_paths_are_well_formed_on_posix_and_windows() -> None:
    posix = InstallPaths(prefix=Path("/tmp/foo"))
    assert posix.node == Path("/tmp/foo/runtime/node/bin/node")
    assert posix.npm == Path("/tmp/foo/runtime/node/bin/npm")
    assert posix.uv == Path("/tmp/foo/runtime/uv/uv")
    windows = InstallPaths(prefix=Path("C:/foo"), system="windows")
    assert windows.node == Path("C:/foo/runtime/node/node.exe")
    assert windows.npm == Path("C:/foo/runtime/node/npm.cmd")
    assert windows.uv == Path("C:/foo/runtime/uv/uv.exe")
    assert windows.python() == Path("C:/foo/venv/Scripts/python.exe")


def test_whisper_cache_matches_the_vendored_aligner(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = whisper_cache_root(home)
    assert root == home / ".cache" / "huggingface" / "hub"
    pins = load_pins()
    snapshot = (
        root
        / pins["whisper"]["cache_name"]
        / "snapshots"
        / pins["whisper"]["revision"]
    )
    assert snapshot.name == pins["whisper"]["revision"]


def test_resolve_prefix_follows_location_pointer(tmp_path: Path, monkeypatch) -> None:
    base = tmp_path / "default"
    custom = tmp_path / "custom"
    custom.mkdir()
    monkeypatch.setattr("installer.core.paths.default_prefix", lambda **_: base)
    assert resolve_prefix() == base
    base.mkdir()
    (base / "location.txt").write_text(str(custom), encoding="utf-8")
    assert resolve_prefix() == custom
    assert resolve_prefix(tmp_path / "explicit") == tmp_path / "explicit"


# --------------------------------------------------------------------------- archives


def _tar_bytes(name: str, payload: bytes) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
        info = tarfile.TarInfo(name)
        info.size = len(payload)
        bundle.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def test_safe_extract_tar_accepts_normal_members(tmp_path: Path) -> None:
    archive = tmp_path / "ok.tar.gz"
    archive.write_bytes(_tar_bytes("folder/file.txt", b"hello"))
    target = tmp_path / "out"
    safe_extract_tar(archive, target)
    assert (target / "folder" / "file.txt").read_bytes() == b"hello"


def test_safe_extract_tar_rejects_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "evil.tar.gz"
    archive.write_bytes(_tar_bytes("../escape.txt", b"nope"))
    with pytest.raises(InstallerError):
        safe_extract_tar(archive, tmp_path / "out")
    assert not (tmp_path / "escape.txt").exists()


def test_safe_extract_zip_rejects_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../escape.txt", "nope")
    with pytest.raises(InstallerError):
        safe_extract_zip(archive, tmp_path / "out")
    assert not (tmp_path / "escape.txt").exists()


# --------------------------------------------------------------------------- download


def test_download_verifies_hash_and_skips_existing_file(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"payload")
    digest = sha256_file(source)
    target = tmp_path / "nested" / "copy.bin"
    download(source.as_uri(), target, sha256=digest, size=7)
    assert target.read_bytes() == b"payload"
    # A second call must not touch the network at all.
    download("https://invalid.invalid/nope", target, sha256=digest, size=7)


def test_download_retries_checksum_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"payload")
    target = tmp_path / "copy.bin"
    with pytest.raises(DownloadError):
        download(source.as_uri(), target, sha256="0" * 64, retries=1)
    assert not target.exists()
    assert not target.with_name(target.name + ".part").exists()


def test_download_rejects_wrong_size(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"payload")
    with pytest.raises(DownloadError):
        download(source.as_uri(), tmp_path / "copy.bin", size=999, retries=1)


def test_verify_file_checks_size_and_hash(tmp_path: Path) -> None:
    target = tmp_path / "file.bin"
    target.write_bytes(b"abc")
    assert verify_file(target, size=3)
    assert not verify_file(target, size=4)
    assert verify_file(target, sha256=sha256_file(target))
    assert not verify_file(target, sha256="0" * 64)
    assert not verify_file(tmp_path / "missing")


def test_hf_url_quotes_nested_paths() -> None:
    url = hf_url("BreezeBlue/Breeze-TTS-2", "abc123", "audio_tokenizer/model.safetensors")
    assert url == (
        "https://huggingface.co/BreezeBlue/Breeze-TTS-2/resolve/abc123/"
        "audio_tokenizer/model.safetensors?download=true"
    )


def test_download_progress_reports_fraction() -> None:
    item = DownloadProgress("x", 50, 100)
    assert item.fraction == 0.5
    assert DownloadProgress("x", 1, 0).fraction == 0.0


def test_progress_cancel_event() -> None:
    progress = Progress()
    progress.cancel()
    assert progress.cancelled()
    with pytest.raises(Cancelled):
        progress.check_cancelled()


# --------------------------------------------------------------------------- plan


def test_parse_components_defaults_and_validation() -> None:
    assert parse_components(None) == list(plan.ALL_COMPONENTS)
    assert parse_components("all") == list(plan.ALL_COMPONENTS)
    assert parse_components("breeze-de") == [plan.CORE, plan.BREEZE_DE]
    with pytest.raises(InstallerError):
        parse_components("nope")


def test_selection_helpers() -> None:
    selection = Selection(components=frozenset({plan.CORE}), prefix=Path("/tmp/x"))
    assert selection.has(plan.CORE)
    assert selection.data == Path("/tmp/x/data")
    assert selection.paths().prefix == Path("/tmp/x").resolve()


def test_cuda_channel_mapping() -> None:
    pins = load_pins()
    assert cuda_channel(pins, None) == "cpu"
    assert cuda_channel(pins, "580.82", "auto") == "cu132"
    assert cuda_channel(pins, "535.10", "auto") == "cu128"
    assert cuda_channel(pins, "580.82", "cpu") == "cpu"
    assert cuda_channel(pins, "10.0", "auto") == "cpu"
    with pytest.raises(InstallerError):
        cuda_channel(pins, "10.0", "gpu")


def test_effective_device_and_validation() -> None:
    pins = load_pins()
    cpu = make_system()
    gpu = make_gpu_system()
    assert plan.effective_device(Selection(), cpu) == "cpu"
    assert plan.effective_device(Selection(), gpu) == "gpu"
    assert plan.effective_device(Selection(device="cpu"), gpu) == "cpu"
    assert plan.effective_device(Selection(device="gpu"), gpu) == "gpu"
    with pytest.raises(InstallerError):
        plan.effective_device(Selection(device="gpu"), cpu)

    breeze = Selection(
        components=frozenset({plan.CORE, plan.BREEZE_EN}),
        device="cpu",
        prefix=Path("/tmp/x"),
    )
    with pytest.raises(InstallerError, match="accept-breeze"):
        plan.validate_selection(breeze, pins, cpu)
    accepted = Selection(
        components=breeze.components,
        device="cpu",
        prefix=breeze.prefix,
        accept_breeze_license=True,
    )
    warnings = plan.validate_selection(accepted, pins, cpu)
    assert any("very slow" in warning for warning in warnings)


def test_chatterbox_validation_warns_about_docker() -> None:
    pins = load_pins()
    selection = Selection(
        components=frozenset({plan.CORE, plan.CHATTERBOX}),
        prefix=Path("/tmp/x"),
    )
    warnings = plan.validate_selection(selection, pins, make_system())
    assert any("Docker" in warning for warning in warnings)
    assert plan.chatterbox_variant("gpu") == "gpu"
    assert plan.chatterbox_variant("cpu") == "cpu"


def test_build_steps_respects_selection_and_optionality() -> None:
    pins = load_pins()
    core = plan.build_steps(
        Selection(components=frozenset({plan.CORE})), pins, make_system()
    )
    core_ids = [step.id for step in core]
    assert core_ids[0] == "payload"
    assert core_ids[-1] == "verify"
    assert not any(step.optional for step in core)

    full = plan.build_steps(
        Selection(
            components=frozenset(
                {plan.CORE, plan.BREEZE_EN, plan.BREEZE_DE, plan.CHATTERBOX}
            ),
            accept_breeze_license=True,
        ),
        pins,
        make_gpu_system(),
    )
    optional = {step.id for step in full if step.optional}
    assert optional == {plan.BREEZE_EN, plan.BREEZE_DE, plan.CHATTERBOX}
    # Launchers and verification run last, after optional components.
    assert [step.id for step in full][-2:] == ["launchers", "verify"]


def test_estimates_grow_with_components() -> None:
    pins = load_pins()
    system = make_system()
    core = Selection(components=frozenset({plan.CORE}))
    more = Selection(
        components=frozenset({plan.CORE, plan.BREEZE_EN, plan.BREEZE_DE}),
        accept_breeze_license=True,
    )
    assert plan.download_estimate(more, pins, system) > plan.download_estimate(
        core, pins, system
    )
    assert plan.installed_estimate(core, pins, system) > plan.download_estimate(
        core, pins, system
    )


def test_plan_payload_is_json_serialisable() -> None:
    pins = load_pins()
    payload = plan.plan_payload(
        Selection(components=frozenset({plan.CORE}), device="cpu"),
        pins,
        make_system(),
    )
    json.dumps(payload)
    assert payload["device"] == "cpu"
    assert payload["torch_channel"] == "cpu"
    assert payload["steps"]


# --------------------------------------------------------------------------- state


def test_state_roundtrip_and_pending_reboot(tmp_path: Path) -> None:
    paths = InstallPaths.for_prefix(tmp_path / "prefix")
    paths.ensure()
    data = state.new_state(
        app_version="0.2.0",
        installer_version="0.2.0",
        prefix=paths,
        platform="linux",
        device="cpu",
        components=["core", "chatterbox"],
    )
    state.set_component(data, "chatterbox", "pending_reboot", message="reboot")
    assert data["pending_reboot"] is True
    state.add_created_path(data, tmp_path / "created")
    state.add_shortcut(data, "/tmp/shortcut")
    state.save_state(paths, data)
    loaded = state.load_state(paths)
    assert loaded is not None
    assert loaded["components"]["chatterbox"]["status"] == "pending_reboot"
    assert loaded["created_paths"] == [str(tmp_path / "created")]
    assert state.component_status(loaded, "chatterbox") == "pending_reboot"
    assert state.component_status(loaded, "core") == "pending"


def test_location_pointer_written_only_for_custom_prefix(tmp_path: Path, monkeypatch) -> None:
    base = tmp_path / "default"
    monkeypatch.setattr("installer.core.state.default_prefix", lambda: base)
    custom = tmp_path / "custom"
    pointer = state.write_location_pointer(custom)
    assert pointer is not None and pointer == base / "location.txt"
    assert pointer.read_text(encoding="utf-8").strip() == str(custom.resolve())
    assert state.write_location_pointer(base) is None
    assert not pointer.exists()


# --------------------------------------------------------------------------- launchers


def test_launcher_env_cpu_and_breeze_flags(tmp_path: Path) -> None:
    paths = InstallPaths.for_prefix(tmp_path / "prefix")
    env = launcher_env(paths, device="cpu", breeze_de=True)
    assert env["DRAMA_LLAMA_DATA"] == str(paths.data)
    assert env["CUDA_VISIBLE_DEVICES"] == ""
    assert env["BREEZE_DE_CPU"] == "1"
    assert env["BETTERSUNO_MCP_DIR"].endswith("third_party/bettersuno-mcp")
    gpu = launcher_env(paths, device="gpu", breeze_de=True)
    assert "CUDA_VISIBLE_DEVICES" not in gpu
    assert "BREEZE_DE_CPU" not in gpu


# --------------------------------------------------------------------------- uninstall


def _fake_install(tmp_path: Path) -> InstallPaths:
    paths = InstallPaths.for_prefix(tmp_path / "prefix")
    paths.ensure()
    (paths.app / "src" / "drama_studio").mkdir(parents=True)
    (paths.app / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (paths.venv / "bin").mkdir(parents=True)
    (paths.venv / "bin" / "python").write_text("", encoding="utf-8")
    data = state.new_state(
        app_version="0.2.0",
        installer_version="0.2.0",
        prefix=paths,
        platform="linux",
        device="cpu",
        components=["core"],
    )
    state.save_state(paths, data)
    return paths


def test_uninstall_keeps_data_by_default(tmp_path: Path) -> None:
    paths = _fake_install(tmp_path)
    (paths.data / "projects").mkdir(parents=True)
    (paths.data / "projects" / "demo.json").write_text("{}", encoding="utf-8")
    uninstall(paths, keep_data=True)
    assert paths.data.is_dir()
    assert (paths.data / "projects" / "demo.json").is_file()
    assert not paths.app.exists()
    assert not paths.state_file.exists()


def test_uninstall_without_keep_data_removes_everything(tmp_path: Path) -> None:
    paths = _fake_install(tmp_path)
    uninstall(paths, keep_data=False)
    assert not paths.prefix.exists()


def test_purge_models_only_removes_installer_created_paths(tmp_path: Path) -> None:
    paths = _fake_install(tmp_path)
    user_model = tmp_path / "user-breeze-model"
    (user_model / "weights").mkdir(parents=True)
    installer_model = tmp_path / "installer-whisper-snapshot"
    (installer_model / "snapshot").mkdir(parents=True)
    data = state.load_state(paths)
    assert data is not None
    state.add_created_path(data, installer_model)
    state.save_state(paths, data)

    removed = _purge_models(paths, data, Progress())

    assert installer_model in removed
    assert not installer_model.exists()
    # A model the user already had is never touched, even with --purge.
    assert user_model.is_dir()


# --------------------------------------------------------------------------- verify


def test_engine_registry_contract() -> None:
    assert verify.ENGINE_NAMES == {"fake", "breeze", "breeze-de", "chatterbox", "suno"}


def test_verify_files_reports_missing_checks(tmp_path: Path) -> None:
    paths = _fake_install(tmp_path)
    pins = load_pins()
    checks = verify.verify_files(paths, pins, make_system(), Selection(), home=tmp_path)
    by_name = {check.name: check for check in checks}
    assert by_name["virtualenv"].ok
    assert not by_name["web UI"].ok
    assert not by_name["Whisper large-v3"].ok
    assert "Breeze repo code" in by_name


# --------------------------------------------------------------------------- docker


def test_ensure_docker_without_docker_or_permission(monkeypatch) -> None:
    monkeypatch.setattr("installer.core.engines._docker_exe", lambda system: None)
    with pytest.raises(DockerUnavailable):
        ensure_docker(make_system(), Progress(), allow_elevated=False)
