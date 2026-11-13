"""Tests for the dependency-free installer core.

The GUI is deliberately not imported here: PySide6 is a build/run dependency of
the wizard only, so the core must stay testable on a bare Python install.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import ssl
import sys
import tarfile
import types
import urllib.error
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import installer.core.download as download_module
from installer.core import cli, plan, state, tls, verify
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
    TlsError,
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


def test_cpu_torch_pins_use_channel_builds(tmp_path: Path, monkeypatch) -> None:
    """CPU installs must never resolve torch from PyPI (CUDA + nvidia deps).

    Regression test: the bare ``torch==x.y.z`` pin matched PyPI's CUDA wheel
    as soon as PyPI was visible, downloading ~2 GiB of nvidia-* libraries on
    a CPU-only install. Both pip installs now pin the channel build
    (``+cpu``/``+cuXX``); the torch-only step uses the channel index alone,
    and the app step repeats the pins with unsafe-best-match so uv sees the
    channel-only ``+suffix`` versions.
    """
    from installer.core import python_stack

    calls: list[dict] = []

    def fake_uv_pip_install(paths, pins, args, **kwargs):
        calls.append({"args": list(args), **kwargs})

    monkeypatch.setattr(python_stack, "uv_pip_install", fake_uv_pip_install)
    monkeypatch.setattr(python_stack, "uv_pip_check", lambda *a, **k: True)
    monkeypatch.setattr(python_stack, "_smoke_test", lambda *a, **k: None)

    pins = load_pins()
    paths = InstallPaths(prefix=tmp_path / "prefix")
    system = make_system()
    selection = Selection(device="cpu", components=["core"], prefix=tmp_path / "p")

    channel = python_stack.install_stack(paths, pins, system, selection, Progress())
    assert channel == "cpu"
    assert len(calls) == 2
    torch_pin = f"torch=={pins['torch']['version']}+cpu"
    torchaudio_pin = f"torchaudio=={pins['torch']['torchaudio']}+cpu"
    first, second = calls
    # Torch-only step: channel index alone, so PyPI's CUDA wheel is invisible.
    assert first["args"] == [torch_pin, torchaudio_pin]
    assert first["index_url"] == pins["torch"]["channels"]["cpu"]["index"]
    assert first.get("extra_index_url") is None
    # App step: repeats the channel pins so demucs/faster-whisper cannot
    # upgrade torch to PyPI's CUDA build.
    assert torch_pin in second["args"] and torchaudio_pin in second["args"]
    assert second["index_strategy"] == "unsafe-best-match"


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


def _whisper_pins(files: dict[str, bytes]) -> dict:
    return {
        "whisper": {
            "repo": "Systran/faster-whisper-large-v3",
            "revision": "deadbeefcafe",
            "cache_name": "models--Systran--faster-whisper-large-v3",
            "size": sum(len(payload) for payload in files.values()),
            "files": {name: {"size": len(payload)} for name, payload in files.items()},
        }
    }


def test_install_whisper_verifies_existing_files(tmp_path: Path, monkeypatch) -> None:
    """Re-verifying a complete cache must not crash (regression: files is a list)."""
    from installer.core import whisper

    payloads = {"config.json": b'{"model": "large-v3"}', "vocabulary.json": b"[]"}
    pins = _whisper_pins(payloads)
    home = tmp_path / "home"
    snapshot = whisper.whisper_snapshot(pins, home)
    snapshot.mkdir(parents=True)
    for name, payload in payloads.items():
        (snapshot / name).write_bytes(payload)

    def no_download(*args, **kwargs):
        raise AssertionError("existing files must not be downloaded again")

    monkeypatch.setattr(whisper, "download", no_download)
    path, downloaded = whisper.install_whisper(
        InstallPaths(prefix=tmp_path / "prefix"), pins, Progress(), home=home
    )

    assert path == snapshot
    assert downloaded is False
    refs = whisper_cache_root(home) / pins["whisper"]["cache_name"] / "refs" / "main"
    assert refs.read_text(encoding="utf-8").strip() == pins["whisper"]["revision"]


def test_install_whisper_downloads_missing_files(tmp_path: Path, monkeypatch) -> None:
    from installer.core import whisper

    payloads = {"config.json": b'{"model": "large-v3"}'}
    pins = _whisper_pins(payloads)
    home = tmp_path / "home"
    requests: list[str] = []

    def fake_download(url, target, **kwargs):
        requests.append(url)
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_bytes(payloads[Path(target).name])

    monkeypatch.setattr(whisper, "download", fake_download)
    path, downloaded = whisper.install_whisper(
        InstallPaths(prefix=tmp_path / "prefix"), pins, Progress(), home=home
    )

    assert downloaded is True
    assert path == whisper.whisper_snapshot(pins, home)
    assert len(requests) == 1
    assert requests[0].endswith("/config.json?download=true")
    assert (path / "config.json").read_bytes() == payloads["config.json"]


def test_resolve_prefix_follows_location_pointer(tmp_path: Path, monkeypatch) -> None:
    base = tmp_path / "default"
    custom = tmp_path / "custom"
    custom.mkdir()
    (custom / "install-state.json").write_text("{}", encoding="utf-8")
    # tmp_path itself lives under the OS temp root, so move the temp root
    # aside to exercise a persistent custom install.
    monkeypatch.setattr(
        "installer.core.paths.tempfile.gettempdir", lambda: str(tmp_path / "tmp")
    )
    monkeypatch.setattr("installer.core.paths.default_prefix", lambda **_: base)
    assert resolve_prefix() == base
    base.mkdir()
    (base / "location.txt").write_text(str(custom), encoding="utf-8")
    assert resolve_prefix() == custom
    assert resolve_prefix(tmp_path / "explicit") == tmp_path / "explicit"


def test_resolve_prefix_ignores_stale_location_pointer(tmp_path: Path, monkeypatch) -> None:
    base = tmp_path / "default"
    base.mkdir()
    (base / "location.txt").write_text(str(tmp_path / "deleted"), encoding="utf-8")
    monkeypatch.setattr(
        "installer.core.paths.tempfile.gettempdir", lambda: str(tmp_path / "tmp")
    )
    monkeypatch.setattr("installer.core.paths.default_prefix", lambda **_: base)
    assert resolve_prefix() == base


def test_resolve_prefix_ignores_temp_location_pointer(tmp_path: Path, monkeypatch) -> None:
    base = tmp_path / "default"
    base.mkdir()
    scratch = tmp_path / "scratch-install"
    scratch.mkdir()
    (scratch / "install-state.json").write_text("{}", encoding="utf-8")
    (base / "location.txt").write_text(str(scratch), encoding="utf-8")
    monkeypatch.setattr("installer.core.paths.default_prefix", lambda **_: base)
    monkeypatch.setattr(
        "installer.core.paths.tempfile.gettempdir", lambda: str(tmp_path)
    )
    assert resolve_prefix() == base


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


# --------------------------------------------------------------------------- tls

TEST_CA_PEM = """-----BEGIN CERTIFICATE-----
MIIDHzCCAgegAwIBAgIUDeqRRKiqvjihpDJ3dR9Uv/+yE5QwDQYJKoZIhvcNAQEL
BQAwHjEcMBoGA1UEAwwTRHJhbWEgTGxhbWEgVGVzdCBDQTAgFw0yNjExMDUxOTA2
NDJaGA8yMTI2MTAxMjE5MDY0MlowHjEcMBoGA1UEAwwTRHJhbWEgTGxhbWEgVGVz
dCBDQTCCASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBAJsvjY49uQxFLtgc
mrRdafUE8EVE3xf8py0wGlMAg/FASUvAKeWvgRMds5WAYrl4a+ZlZt1O05KbbEkM
/owPW0affhRwH9gES7qQQvdwVpWt3uG8BH9316M8/haBVDD+lfb9uzhGQ1TZpq8I
zYH6JtsdAOKw0rDoLQRAiYspdpmnPMsdjBj+FFfFiZk4ilQJmyidDDXcplsp+vpn
W4TtEwn4YeoNEcEOwJ4ns9IY7BCTIPt41NXnpiECkQqEXJY2gSu2M0557SW+WV/7
cYt+N+0EYm8fTc2/LWHKDdvwS1VqpYiBtwBIWhBEeSYyZzIEpwhhObo2bPu4gpIY
SvGTKUECAwEAAaNTMFEwHQYDVR0OBBYEFCjq9/AFskCrLbtQR3u2Ty+8iJjCMB8G
A1UdIwQYMBaAFCjq9/AFskCrLbtQR3u2Ty+8iJjCMA8GA1UdEwEB/wQFMAMBAf8w
DQYJKoZIhvcNAQELBQADggEBAC5kfIrXdy2yQUBcGSitfK2Nm8XcqlTRIbtOi7iK
PF5vfByyZG3THyzGxMt3klhpg0a3VXS42yn+ITrNbcG3TbQICb88LQRdPFclKwmj
YhVpLZgNHiOrC/B9FKCfi2Qmmlvzou4ct2Uen5GvmSIQ+KUf+1jzVElr0ZjK7DZL
/nClj0iplkubRomVnr+2K+1IprTdvIkT/OWmOdrE8Xe6Z3GJ9qCeaiLdnoY0N1SP
HzhZtB7L/bIkO3xJ6MUdJTfqDoIzseulIthWlJz18JWNrbMkR3sk57vayI7SFDCF
hJjRcimbF8uFrQA1Hja3PCRVNcKshHZm5329x54gahVLBnM=
-----END CERTIFICATE-----
"""


def _write_test_ca(path: Path) -> Path:
    path.write_text(TEST_CA_PEM, encoding="utf-8")
    return path


@pytest.fixture()
def tls_state(monkeypatch):
    """Isolate the cached trust-store decision and the environment it mutates."""
    saved = {
        key: os.environ.get(key)
        for key in ("SSL_CERT_FILE", "SSL_CERT_DIR", tls.INSECURE_ENV)
    }
    monkeypatch.setattr(tls, "is_windows", lambda *_: False)
    monkeypatch.setattr(tls, "CANDIDATE_FILES", ())
    for key in saved:
        monkeypatch.delenv(key, raising=False)
    tls.reset()
    yield monkeypatch
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    tls.reset()


def test_tls_prefers_configured_bundle(tmp_path: Path, tls_state) -> None:
    bundle = _write_test_ca(tmp_path / "ca.pem")
    tls_state.setenv("SSL_CERT_FILE", str(bundle))
    path, source = tls.discover()
    assert path == bundle
    assert "SSL_CERT_FILE" in source


def test_tls_skips_empty_bundles(tmp_path: Path, tls_state) -> None:
    empty = tmp_path / "empty.pem"
    empty.write_text("", encoding="utf-8")
    valid = _write_test_ca(tmp_path / "ca.pem")
    tls_state.setattr(tls, "CANDIDATE_FILES", (str(empty), str(valid)))
    assert tls.discover()[0] == valid


def test_tls_exports_bundle_and_verifies(tmp_path: Path, tls_state) -> None:
    bundle = _write_test_ca(tmp_path / "ca.pem")
    tls_state.setattr(tls, "CANDIDATE_FILES", (str(bundle),))
    assert tls.configure() == bundle
    assert os.environ["SSL_CERT_FILE"] == str(bundle)
    assert tls.verified()
    context = tls.ssl_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname


def test_tls_without_a_store_disables_verification(tls_state) -> None:
    assert tls.configure() is None
    assert not tls.verified()
    context = tls.ssl_context()
    assert context.verify_mode == ssl.CERT_NONE
    assert not context.check_hostname


def test_tls_insecure_flag_overrides_a_store(tmp_path: Path, tls_state) -> None:
    bundle = _write_test_ca(tmp_path / "ca.pem")
    tls_state.setattr(tls, "CANDIDATE_FILES", (str(bundle),))
    assert tls.configure(insecure=True) == bundle
    assert not tls.verified()
    assert tls.ssl_context().verify_mode == ssl.CERT_NONE


def test_tls_errors_are_not_retried(tmp_path: Path, tls_state) -> None:
    bundle = _write_test_ca(tmp_path / "ca.pem")
    tls_state.setattr(tls, "CANDIDATE_FILES", (str(bundle),))
    tls.configure()
    attempts: list[int] = []

    class FailingOpener:
        def open(self, *_args, **_kwargs):
            attempts.append(1)
            raise urllib.error.URLError(
                ssl.SSLCertVerificationError(1, "certificate verify failed")
            )

    tls_state.setattr(tls, "opener", lambda: FailingOpener())
    with pytest.raises(TlsError) as caught:
        download("https://example.invalid/uv.tar.gz", tmp_path / "uv.tar.gz", retries=3)
    assert len(attempts) == 1
    assert "SSL_CERT_FILE" in str(caught.value)


def test_download_still_retries_transient_errors(tmp_path: Path, tls_state) -> None:
    attempts: list[int] = []

    def failing_open(*_args, **_kwargs):
        attempts.append(1)
        raise urllib.error.URLError("connection reset")

    tls_state.setattr(
        tls, "opener", lambda: types.SimpleNamespace(open=failing_open)
    )
    tls_state.setattr(download_module, "_sleep", lambda *_args, **_kwargs: None)
    with pytest.raises(DownloadError):
        download("https://example.invalid/x", tmp_path / "x", retries=2)
    assert len(attempts) == 2


def test_cli_check_tls_reports_failure(
    tls_state, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(tls, "check", lambda url: (False, f"failed {url}"))
    assert cli.main(["--check-tls", "https://example.invalid"]) == 1
    assert "failed https://example.invalid" in capsys.readouterr().out


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
    # No cu128 builds exist for the pinned torch (upstream dropped the
    # channel); older drivers fall back to CPU.
    assert cuda_channel(pins, "535.10", "auto") == "cpu"
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
    # Auto mode on a GPU box with a pre-CUDA-13 driver warns about the
    # CPU fallback instead of failing resolution later.
    old_driver = make_system(
        gpus=[GpuInfo(name="GTX 1080", driver="535.10", vram_mb=8192)]
    )
    auto = Selection(prefix=Path("/tmp/x"))
    fallback = plan.validate_selection(auto, pins, old_driver)
    assert any("too old" in warning for warning in fallback)
    # An explicit --cpu is a deliberate choice: no nagging.
    explicit = Selection(device="cpu", prefix=Path("/tmp/x"))
    assert not any(
        "too old" in warning
        for warning in plan.validate_selection(explicit, pins, old_driver)
    )


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


# ------------------------------------------------------------------ spatial renderer


def make_spatial_archive(path: Path, member: str, payload: bytes) -> None:
    """A tar.gz holding one file, shaped like the CI artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    info = tarfile.TarInfo(member)
    info.size = len(payload)
    with tarfile.open(path, "w:gz") as tar:
        tar.addfile(info, io.BytesIO(payload))


def spatial_pins(archive: Path) -> dict:
    return {
        "spatial": {
            "abi": 1,
            "assets": {
                "linux-x86_64": {
                    "url": "https://example.invalid/dramallama-spatial-linux.tar.gz",
                    "sha256": sha256_file(archive),
                    "size": archive.stat().st_size,
                }
            },
        }
    }


def copy_over_url(_url: str, target: Path, archive: Path, **_kwargs: object) -> Path:
    # Stands in for download(): the asset is not published yet, so the test
    # serves the locally built archive instead.
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(archive, target)
    return target


def test_spatial_step_is_planned_for_core() -> None:
    # Staging is a core capability, not an engine, so it is not a component the
    # wizard can decline; the step itself decides whether anything to install
    # exists. It must run before verify so the report reflects it.
    ids = [
        step.id
        for step in plan.build_steps(
            Selection(components=frozenset({plan.CORE})), load_pins(), make_system()
        )
    ]
    assert "spatial" in ids
    assert ids.index("spatial") < ids.index("verify")
    assert not any(step.id == "spatial" and step.optional for step in
                   plan.build_steps(Selection(components=frozenset({plan.CORE})),
                                    load_pins(), make_system()))


def test_ensure_spatial_skips_when_nothing_is_published(tmp_path: Path) -> None:
    from installer.core import runtimes

    paths = InstallPaths(prefix=tmp_path / "prefix").ensure()
    logs: list[str] = []

    result = runtimes.ensure_spatial(
        paths, load_pins(), make_system(), Progress(on_log=logs.append)
    )

    assert result is None
    assert not (paths.lib / "libdrama_llama_spatial.so").exists()
    assert any("no pinned spatial asset" in line for line in logs)


def test_ensure_spatial_skips_on_a_platform_without_an_asset(tmp_path: Path) -> None:
    from installer.core import runtimes

    archive = tmp_path / "asset.tar.gz"
    make_spatial_archive(archive, "libdrama_llama_spatial.so", b"x")
    paths = InstallPaths(prefix=tmp_path / "prefix").ensure()
    macos = make_system(os_name="darwin", platform_key="macos-arm64")

    assert runtimes.ensure_spatial(paths, spatial_pins(archive), macos, Progress()) is None


def test_ensure_spatial_installs_and_loads_the_asset(tmp_path: Path, monkeypatch) -> None:
    from drama_studio import spatial
    from installer.core import runtimes

    library = spatial.library_path()
    if library is None:  # self-skip, as in tests/test_spatial.py
        pytest.skip("native spatial renderer not built")
    archive = tmp_path / "asset.tar.gz"
    make_spatial_archive(archive, "libdrama_llama_spatial.so", library.read_bytes())
    paths = InstallPaths(prefix=tmp_path / "prefix").ensure()
    monkeypatch.setattr(
        runtimes, "download", lambda url, dest, **kw: copy_over_url(url, dest, archive)
    )

    installed = runtimes.ensure_spatial(
        paths, spatial_pins(archive), make_system(), Progress()
    )

    assert installed == paths.lib / "libdrama_llama_spatial.so"
    assert installed.is_file()
    assert installed.stat().st_mode & 0o111, "must be executable for dlopen"


def test_ensure_spatial_rejects_a_library_that_does_not_load(
    tmp_path: Path, monkeypatch
) -> None:
    from installer.core import runtimes

    archive = tmp_path / "asset.tar.gz"
    make_spatial_archive(archive, "libdrama_llama_spatial.so", b"not a shared object")
    paths = InstallPaths(prefix=tmp_path / "prefix").ensure()
    monkeypatch.setattr(
        runtimes, "download", lambda url, dest, **kw: copy_over_url(url, dest, archive)
    )

    with pytest.raises(InstallerError, match="does not load"):
        runtimes.ensure_spatial(paths, spatial_pins(archive), make_system(), Progress())


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
