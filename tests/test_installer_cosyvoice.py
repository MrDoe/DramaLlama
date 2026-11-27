"""The CosyVoice installer component: reuse what is installed, pin the rest."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tarfile
from pathlib import Path

import pytest

from installer.core import cosyvoice_rl, engines, plan, state
from installer.core.app_payload import write_notices
from installer.core.launchers import launcher_env
from installer.core.paths import (
    InstallPaths,
    legacy_cosyvoice_models,
    legacy_cosyvoice_root,
    load_pins,
)
from installer.core.progress import Progress
from installer.core.util import InstallerError
from tests.test_installer import make_gpu_system, make_system

FILES = {
    "CosyVoice3-2512_Q8_0.gguf": b"q8",
    "CosyVoice3-2512_Q4_K_M.gguf": b"q4",
    "frontend-onnx/speech_tokenizer_v3.onnx": b"tokenizer",
    "frontend-onnx/campplus.onnx": b"campplus",
}


def _pin(path: str) -> dict:
    data = FILES[path]
    return {"path": path, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _pins(**assets) -> dict:
    return {
        "cosyvoice": {
            "release": "v0",
            "assets": assets,
            "ggml": {"linux-x86_64": {"url": "https://x/ggml.tar.gz", "sha256": "a" * 64, "size": 1}},
            "models": {
                "repo": "org/repo",
                "revision": "rev",
                "files": [
                    [_pin("CosyVoice3-2512_Q8_0.gguf"), _pin("CosyVoice3-2512_Q4_K_M.gguf")],
                    [_pin("frontend-onnx/speech_tokenizer_v3.onnx")],
                    [_pin("frontend-onnx/campplus.onnx")],
                ],
            },
        }
    }


class Machine:
    """Fake downloads and the app-side runtime probe."""

    def __init__(self, monkeypatch, tmp_path: Path) -> None:
        self.home = tmp_path / "home"
        self.paths = InstallPaths.for_prefix(tmp_path / "prefix")
        self.paths.ensure()
        self.downloads: list[str] = []
        self.probes: list[dict] = []
        self.answers: list[dict | None] = [{"ggml_source": "ollama", "backend": "Vulkan1"}]
        monkeypatch.setattr(engines, "which", lambda *names: None)
        monkeypatch.setattr(engines, "download", self.download)
        monkeypatch.setattr(engines, "run", self.run)
        monkeypatch.setattr(engines, "detect_vulkan", lambda os_name: True)

    def download(self, url, destination, **kwargs):
        self.downloads.append(url)
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        if url.endswith(".tar.gz"):
            staging = target.parent / "payload"
            staging.mkdir(exist_ok=True)
            names = ["libggml.so.1"] if "ggml" in url else ["cosyvoice-cli", "libcosyvoice.so.0"]
            with tarfile.open(target, "w:gz") as bundle:
                for name in names:
                    (staging / name).write_bytes(b"binary")
                    bundle.add(staging / name, arcname=name)
        else:
            name = url.split("/resolve/rev/")[1].split("?")[0]
            target.write_bytes(FILES[name])
        return target

    def run(self, command, **kwargs):
        self.probes.append(kwargs["env"])
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if answer is None:
            return subprocess.CompletedProcess(command, 1, "", "no usable GGML")
        return subprocess.CompletedProcess(command, 0, "log noise\n" + json.dumps(answer), "")

    def install(self, pins: dict, *, system=None, device: str = "gpu"):
        return engines.install_cosyvoice(
            self.paths,
            pins,
            system or make_gpu_system(),
            Progress(),
            device=device,
            home=self.home,
        )

    def own_cli(self) -> Path:
        cli = legacy_cosyvoice_root(self.home) / "bin" / "cosyvoice-cli"
        cli.parent.mkdir(parents=True, exist_ok=True)
        cli.write_bytes(b"mine")
        return cli

    def cached_models(self, *names: str) -> None:
        snapshot = self.home / ".cache/huggingface/hub/models--org--repo/snapshots/rev"
        for name in names:
            (snapshot / name).parent.mkdir(parents=True, exist_ok=True)
            (snapshot / name).write_bytes(FILES[name])


@pytest.fixture
def machine(monkeypatch, tmp_path) -> Machine:
    return Machine(monkeypatch, tmp_path)


def test_installed_parts_are_reused_and_only_a_spare_ggml_is_fetched(machine):
    cli = machine.own_cli()
    machine.cached_models(
        "CosyVoice3-2512_Q4_K_M.gguf",
        "frontend-onnx/speech_tokenizer_v3.onnx",
        "frontend-onnx/campplus.onnx",
    )

    result = machine.install(_pins())

    # Ollama updates itself; the small pinned copy is what a render falls back to.
    spare = legacy_cosyvoice_root(machine.home) / "ggml"
    assert machine.downloads == ["https://x/ggml.tar.gz"]
    assert result.created == [spare]
    assert (spare / "libggml.so.1").is_file()
    assert (result.cli, result.ggml_source, result.cpu_fallback) == (cli, "ollama", False)


def test_an_existing_spare_is_not_fetched_again(machine):
    machine.own_cli()
    spare = legacy_cosyvoice_root(machine.home) / "ggml"
    spare.mkdir(parents=True)
    (spare / "libggml.so.1").write_bytes(b"x")

    result = machine.install(_pins())

    assert not any("ggml" in url for url in machine.downloads)
    assert spare not in result.created


def test_a_failed_spare_download_does_not_fail_the_component(machine, monkeypatch):
    machine.own_cli()
    download = machine.download

    def flaky(url, destination, **kwargs):
        if "ggml" in url:
            raise InstallerError("network down")
        return download(url, destination, **kwargs)

    monkeypatch.setattr(engines, "download", flaky)

    assert machine.install(_pins()).ggml_source == "ollama"


def test_a_cli_on_the_path_wins(machine, monkeypatch, tmp_path):
    found = tmp_path / "usr-bin" / "cosyvoice-cli"
    found.parent.mkdir()
    found.write_bytes(b"x")
    monkeypatch.setattr(engines, "which", lambda *names: str(found))

    assert machine.install(_pins()).cli == found


def test_missing_models_are_downloaded_and_recorded(machine):
    machine.own_cli()

    result = machine.install(_pins())

    models = legacy_cosyvoice_models(machine.home)
    assert (models / "CosyVoice3-2512_Q8_0.gguf").is_file()
    assert (models / "frontend-onnx/campplus.onnx").is_file()
    assert len([url for url in machine.downloads if "resolve/rev" in url]) == 3
    assert models / "CosyVoice3-2512_Q8_0.gguf" in result.created


def test_a_corrupt_cached_file_is_not_trusted(machine):
    machine.own_cli()
    machine.cached_models("frontend-onnx/campplus.onnx")
    cached = next(machine.home.rglob("campplus.onnx"))
    cached.write_bytes(b"tampered")

    machine.install(_pins())

    assert any("campplus.onnx" in url for url in machine.downloads)


def test_the_pinned_cli_is_installed_when_none_exists(machine):
    asset = {"url": "https://x/cosy.tar.gz", "sha256": "b" * 64, "size": 1}

    result = machine.install(_pins(**{"linux-x86_64": asset}))

    root = legacy_cosyvoice_root(machine.home)
    assert result.cli == root / "bin" / "cosyvoice-cli"
    assert result.cli.is_file() and root / "bin" in result.created


def test_without_a_published_asset_the_message_says_where_to_build(machine):
    pins = _pins()
    pins["app"] = {"repository": "https://example.org/drama-llama"}

    with pytest.raises(InstallerError) as raised:
        machine.install(pins)

    # An installed release ships no scripts/, so the hint must name the source.
    message = str(raised.value)
    assert "scripts/build_cosyvoice.py" in message
    assert "source checkout" in message and "https://example.org/drama-llama" in message
    assert machine.downloads == []


def test_pinned_ggml_is_downloaded_only_when_the_probe_fails(machine):
    machine.own_cli()
    machine.answers = [None, {"ggml_source": "pinned", "backend": "Vulkan1"}]

    result = machine.install(_pins())

    ggml = legacy_cosyvoice_root(machine.home) / "ggml"
    assert result.ggml_source == "pinned"
    assert (ggml / "libggml.so.1").is_file() and ggml in result.created
    assert machine.probes[0]["DRAMA_LLAMA_COSYVOICE_HOME"] == str(
        legacy_cosyvoice_root(machine.home)
    )


def test_a_probe_that_still_fails_is_an_error(machine):
    machine.own_cli()
    machine.answers = [None]

    with pytest.raises(InstallerError, match="no usable GGML"):
        machine.install(_pins())


@pytest.mark.parametrize(("device", "fallback"), [("gpu", True), ("cpu", False)])
def test_cpu_backend_on_a_gpu_install_is_reported(machine, device, fallback):
    machine.own_cli()
    machine.answers = [{"ggml_source": "ollama", "backend": "CPU"}]

    system = make_gpu_system() if device == "gpu" else make_system()
    assert machine.install(_pins(), system=system, device=device).cpu_fallback is fallback


def test_component_is_selectable_optional_and_sized():
    pins = load_pins()
    assert plan.parse_components("cosyvoice") == [plan.CORE, plan.COSYVOICE]
    assert plan.COSYVOICE in plan.ALL_COMPONENTS
    selection = plan.Selection(components=frozenset({plan.CORE, plan.COSYVOICE}))
    steps = plan.build_steps(selection, pins, make_gpu_system())
    step = next(step for step in steps if step.id == plan.COSYVOICE)
    assert step.optional and step.download_bytes > 1_500_000_000
    core = plan.Selection(components=frozenset({plan.CORE}))
    system = make_gpu_system()
    assert plan.download_estimate(selection, pins, system) - plan.download_estimate(
        core, pins, system
    ) == step.download_bytes


def test_real_pins_are_complete():
    meta = load_pins()["cosyvoice"]
    entries = [item for group in meta["models"]["files"] for item in group]
    entries += list(meta["assets"].values()) + list(meta["ggml"].values())
    assert {"linux-x86_64", "windows-x86_64"} <= set(meta["assets"])
    entries.append(meta["build"]["onnxruntime"])
    for entry in entries:
        assert len(entry["sha256"]) == 64 and entry["size"] > 0
    assert len(meta["build"]["commit"]) == len(meta["build"]["ggml_commit"]) == 40
    assert len(meta["models"]["revision"]) == 40
    assert {"linux-x86_64", "windows-x86_64"} <= set(meta["ggml"])


def test_launcher_forces_the_cpu_backend_when_needed(tmp_path):
    paths = InstallPaths.for_prefix(tmp_path / "prefix")
    key = "DRAMA_LLAMA_COSYVOICE_BACKEND"
    assert key not in launcher_env(paths, device="gpu", cosyvoice=True)
    assert launcher_env(paths, device="cpu", cosyvoice=True)[key] == "cpu"
    assert launcher_env(paths, device="gpu", cosyvoice=True, cosyvoice_cpu=True)[key] == "cpu"
    assert key not in launcher_env(paths, device="cpu")


def test_state_and_notices_mention_cosyvoice(tmp_path):
    paths = InstallPaths.for_prefix(tmp_path / "prefix")
    paths.ensure()
    data = state.new_state(
        app_version="1",
        installer_version="1",
        prefix=paths,
        platform="linux",
        device="gpu",
        components=["core"],
    )
    assert data["cosyvoice_cpu"] is False
    notices = write_notices(paths, load_pins()).read_text(encoding="utf-8")
    assert "cosyvoice.cpp" in notices and "ONNX Runtime" in notices


# --------------------------------------------------------------------------- RL


def _rl_pins() -> dict:
    pins = _pins()
    meta = pins["cosyvoice"]
    converter = b"print('converter')"
    meta["upstream"] = {"repo": "up/stream", "revision": "r1"}
    meta["rl"] = {
        "file": "CosyVoice3-2512_RL_Q8_0.gguf",
        "sha256": hashlib.sha256(b"rl-model").hexdigest(),
        "size": 8,
        "converter": {
            "url": "https://x/convert.py",
            "sha256": hashlib.sha256(converter).hexdigest(),
            "size": len(converter),
        },
        "packages": ["gguf==0.17.1"],
        "sources": [
            {"path": "llm.rl.pt", "size": 3, "sha256": hashlib.sha256(b"llm").hexdigest()}
        ],
    }
    meta["models"]["files"][0].insert(
        1,
        {"path": meta["rl"]["file"], "size": 8, "sha256": meta["rl"]["sha256"]},
    )
    return pins


class Converting(Machine):
    def __init__(self, monkeypatch, tmp_path) -> None:
        super().__init__(monkeypatch, tmp_path)
        self.output = b"rl-model"
        self.conversions: list[tuple[list[str], dict]] = []
        self.packages: list[list[str]] = []
        self.converter_exit = 0
        monkeypatch.setattr(cosyvoice_rl, "download", self.download_converter)
        monkeypatch.setattr(cosyvoice_rl, "uv_pip_install", self.pip)
        monkeypatch.setattr(cosyvoice_rl, "run", self.convert)
        self.own_cli()
        self.cached_models(
            "frontend-onnx/speech_tokenizer_v3.onnx", "frontend-onnx/campplus.onnx"
        )

    def sources(self, data: bytes = b"llm") -> None:
        snapshot = self.home / ".cache/huggingface/hub/models--up--stream/snapshots/r1"
        snapshot.mkdir(parents=True, exist_ok=True)
        (snapshot / "llm.rl.pt").write_bytes(data)

    def download_converter(self, url, destination, **kwargs):
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        Path(destination).write_bytes(b"print('converter')")
        return Path(destination)

    def pip(self, paths, pins, args, **kwargs):
        self.packages.append(list(args))

    def convert(self, command, **kwargs):
        command = [str(part) for part in command]
        self.conversions.append((command, kwargs["env"]))
        Path(command[command.index("--gguf_model") + 1]).write_bytes(self.output)
        return subprocess.CompletedProcess(command, self.converter_exit, "", "boom")

    def model(self) -> Path:
        return legacy_cosyvoice_models(self.home) / "CosyVoice3-2512_RL_Q8_0.gguf"


@pytest.fixture
def converting(monkeypatch, tmp_path) -> Converting:
    return Converting(monkeypatch, tmp_path)


def test_rl_model_is_built_from_installed_sources_instead_of_downloading(converting):
    converting.sources()

    result = converting.install(_rl_pins())

    assert converting.model().read_bytes() == b"rl-model"
    assert converting.model() in result.created
    assert not any("resolve/rev" in url for url in converting.downloads)
    _, env = converting.conversions[0]
    assert env["CUDA_VISIBLE_DEVICES"] == ""
    assert env["PYTHONPATH"] == converting.packages[0][1]
    assert converting.packages[0][-1] == "gguf==0.17.1"


@pytest.mark.parametrize("sources", [None, b"tampered"])
def test_without_verified_sources_nothing_is_converted(converting, sources):
    if sources is not None:
        converting.sources(sources)

    converting.install(_rl_pins())

    assert converting.conversions == [] and converting.packages == []
    assert any("Q8_0.gguf" in url and "RL" not in url for url in converting.downloads)


@pytest.mark.parametrize("failure", ["wrong output", "converter crash"])
def test_a_failed_conversion_falls_back_to_the_published_model(converting, failure):
    converting.sources()
    if failure == "wrong output":
        converting.output = b"garbage!"
    else:
        converting.converter_exit = 1

    result = converting.install(_rl_pins())

    assert not converting.model().exists()
    assert not list(converting.model().parent.glob("*.part"))
    assert converting.model() not in result.created
    assert any("resolve/rev" in url for url in converting.downloads)


def test_an_existing_rl_model_is_not_rebuilt(converting):
    converting.sources()
    converting.model().parent.mkdir(parents=True, exist_ok=True)
    converting.model().write_bytes(b"rl-model")

    result = converting.install(_rl_pins())

    assert converting.conversions == []
    assert converting.model() not in result.created


def test_pins_stay_plain_ascii():
    """CI steps read pins.json with the platform encoding; Windows is cp1252."""
    pins_file = Path(__file__).resolve().parents[1] / "installer" / "pins.json"

    pins_file.read_bytes().decode("ascii")
    assert load_pins()["cosyvoice"]["smoke_reference"]["text"].startswith("希望")
