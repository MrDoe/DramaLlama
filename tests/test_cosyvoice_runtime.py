"""Finding and driving ``cosyvoice-cli``. No real runtime, model or GPU is used."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from drama_studio import config
from drama_studio.engines import cosyvoice_runtime as runtime_module
from drama_studio.engines.base import EngineNotAvailable

BACKENDS = """
Available GGML backends (pass a name to --backend):
  Vulkan0    0000:00:01.0         AMD Radeon Integrated Graphics (RADV)
  Vulkan1    0000:00:02.0         NVIDIA GeForce Example GPU
  CPU        -                    Example 8-Core Processor
"""


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


@pytest.fixture
def layout(tmp_path, monkeypatch):
    """An isolated machine: no env overrides, nothing on PATH, no Ollama."""
    home = tmp_path / "cosy-home"
    monkeypatch.setattr(config, "COSYVOICE_CLI", "")
    monkeypatch.setattr(config, "COSYVOICE_GGML", "")
    monkeypatch.setattr(config, "COSYVOICE_MODEL", "")
    monkeypatch.setattr(config, "COSYVOICE_BACKEND", "auto")
    monkeypatch.setattr(config, "COSYVOICE_HOME", home)
    monkeypatch.setattr(config, "COSYVOICE_MODELS", tmp_path / "models")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(runtime_module, "HF_HUB", tmp_path / "hub")
    monkeypatch.setattr(runtime_module, "ollama_library_dirs", list)
    monkeypatch.setattr(runtime_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(runtime_module, "WINDOWS", False)
    return tmp_path


def _ggml(directory: Path, *, major: int = 1, extra: tuple[str, ...] = ()) -> Path:
    for name in (f"libggml.so.{major}", f"libggml-base.so.{major}", *extra):
        _touch(directory / name)
    return directory


def _install(layout: Path) -> None:
    _touch(config.COSYVOICE_HOME / "bin" / "cosyvoice-cli")
    snapshot = runtime_module.HF_HUB / runtime_module.GGUF_CACHE / "snapshots" / "abc"
    _touch(snapshot / "CosyVoice3-2512_Q8_0.gguf")
    _touch(snapshot / "frontend-onnx" / runtime_module.TOKENIZER_FILES[0])
    _touch(snapshot / "frontend-onnx" / "campplus.onnx")


def _fake_run(monkeypatch, *, output: str = BACKENDS, returncode: int = 0):
    calls: list[tuple[list[str], dict]] = []

    def fake(command, env, *, cancelled=None, timeout=600.0):
        calls.append((list(command), env))
        if "--prompt-speech-output" in command:
            _touch(Path(command[command.index("--prompt-speech-output") + 1]))
        if "--output" in command:
            _touch(Path(command[command.index("--output") + 1]))
        return returncode, output

    monkeypatch.setattr(runtime_module, "run_process", fake)
    return calls


def test_cli_prefers_override_then_path_then_own_install(layout, monkeypatch):
    own = _touch(config.COSYVOICE_HOME / "bin" / "cosyvoice-cli")
    assert runtime_module.find_cli() == own

    installed = _touch(layout / "usr" / "cosyvoice-cli")
    monkeypatch.setattr(runtime_module.shutil, "which", lambda name: str(installed))
    assert runtime_module.find_cli() == installed

    override = _touch(layout / "mine" / "cosyvoice-cli")
    monkeypatch.setattr(config, "COSYVOICE_CLI", str(override))
    assert runtime_module.find_cli() == override


def test_a_broken_override_is_an_error_not_a_silent_fallback(layout, monkeypatch):
    _touch(config.COSYVOICE_HOME / "bin" / "cosyvoice-cli")
    monkeypatch.setattr(config, "COSYVOICE_CLI", str(layout / "missing"))
    with pytest.raises(EngineNotAvailable, match="DRAMA_LLAMA_COSYVOICE_CLI"):
        runtime_module.find_cli()


def test_installed_ollama_ggml_wins_and_never_exposes_cuda(layout, monkeypatch):
    _install(layout)
    ollama = _ggml(layout / "ollama")
    _touch(ollama / "vulkan" / "libggml-vulkan.so")
    _touch(ollama / "cuda_v13" / "libggml-cuda.so")
    _touch(ollama / "libggml-rpc.so")
    _ggml(config.COSYVOICE_HOME / "ggml")
    monkeypatch.setattr(runtime_module, "ollama_library_dirs", lambda: [ollama])
    _fake_run(monkeypatch)

    resolved = runtime_module.resolve_runtime()

    assert resolved.ggml_source == "ollama"
    names = {path.name for path in resolved.ggml_dir.iterdir()}
    assert "libggml-vulkan.so" in names and "libggml.so.1" in names
    assert not any("cuda" in name or "rpc" in name for name in names)
    assert resolved.backend == "Vulkan1"


def test_ggml_zero_is_rejected_in_favour_of_the_pinned_copy(layout, monkeypatch):
    _install(layout)
    old = _ggml(layout / "ollama", major=0)
    _ggml(config.COSYVOICE_HOME / "ggml")
    monkeypatch.setattr(runtime_module, "ollama_library_dirs", lambda: [old])
    _fake_run(monkeypatch)

    assert runtime_module.resolve_runtime().ggml_source == "pinned"


def test_a_candidate_that_fails_its_probe_is_skipped(layout, monkeypatch):
    _install(layout)
    ollama = _ggml(layout / "ollama")
    _ggml(config.COSYVOICE_HOME / "ggml")
    monkeypatch.setattr(runtime_module, "ollama_library_dirs", lambda: [ollama])

    def fake(command, env, *, cancelled=None, timeout=600.0):
        broken = "ggml-ollama" in command[command.index("--backend-path") + 1]
        return (127, "symbol lookup error") if broken else (0, BACKENDS)

    monkeypatch.setattr(runtime_module, "run_process", fake)

    assert runtime_module.resolve_runtime().ggml_source == "pinned"


def test_no_usable_ggml_reports_why(layout, monkeypatch):
    _install(layout)
    _fake_run(monkeypatch)
    with pytest.raises(EngineNotAvailable, match="GGML 1"):
        runtime_module.resolve_runtime()


@pytest.mark.parametrize(
    ("requested", "listing", "expected"),
    [
        ("auto", BACKENDS, "Vulkan1"),
        ("auto", BACKENDS.replace("NVIDIA GeForce", "Intel Arc"), "Vulkan0"),
        ("auto", "Available GGML backends:\n  CPU        -    Some CPU\n", "CPU"),
        ("cpu", BACKENDS, "CPU"),
        ("Vulkan0", BACKENDS, "Vulkan0"),
    ],
)
def test_backend_choice(requested, listing, expected):
    devices = runtime_module.parse_backends(listing)
    assert runtime_module.choose_backend(devices, requested) == expected


def test_missing_model_names_the_remedy(layout, monkeypatch):
    _touch(config.COSYVOICE_HOME / "bin" / "cosyvoice-cli")
    with pytest.raises(EngineNotAvailable, match="installer"):
        runtime_module.find_models()
    assert runtime_module.installed() is False
    _install(layout)
    assert runtime_module.installed() is False  # no GGML libraries yet
    _ggml(config.COSYVOICE_HOME / "ggml")
    assert runtime_module.installed() is True


def _runtime(layout: Path) -> runtime_module.CosyRuntime:
    return runtime_module.CosyRuntime(
        cli=_touch(layout / "bin" / "cosyvoice-cli"),
        ggml_dir=_ggml(layout / "g"),
        ggml_source="pinned",
        model=_touch(layout / "model.gguf"),
        speech_tokenizer=_touch(layout / "tok.onnx"),
        campplus=_touch(layout / "camp.onnx"),
        backend="Vulkan1",
    )


def test_synthesize_passes_voice_seed_and_optional_instruction(layout, monkeypatch):
    runtime = _runtime(layout)
    calls = _fake_run(monkeypatch)
    voice = _touch(layout / "voice.gguf")

    runtime_module.synthesize(runtime, voice, "Grüß dich.", layout / "a.wav", seed=7)
    runtime_module.synthesize(
        runtime, voice, "Nein.", layout / "b.wav", seed=8, instruction="请非常生气地说一句话。"
    )

    plain, steered = calls[0][0], calls[1][0]
    assert plain[plain.index("--text") + 1] == "Grüß dich."
    assert plain[plain.index("--seed") + 1] == "7"
    assert plain[plain.index("--prompt-speech") + 1] == str(voice)
    assert plain[plain.index("--backend") + 1] == "Vulkan1"
    assert "--instruction" not in plain
    assert steered[steered.index("--instruction") + 1] == "请非常生气地说一句话。"
    library_path = calls[0][1]["LD_LIBRARY_PATH"]
    assert str(runtime.cli.parent) in library_path and str(runtime.ggml_dir) in library_path


def test_synthesis_failure_carries_the_tool_output(layout, monkeypatch):
    _fake_run(monkeypatch, output="ggml_vulkan: out of device memory", returncode=1)
    with pytest.raises(EngineNotAvailable, match="out of device memory"):
        runtime_module.synthesize(
            _runtime(layout), layout / "v.gguf", "Hallo.", layout / "a.wav", seed=1
        )


def test_voice_prompt_is_cached_by_audio_and_transcript(layout, monkeypatch):
    runtime = _runtime(layout)
    calls = _fake_run(monkeypatch)
    clip = _touch(layout / "ref.wav")
    cache = layout / "voices"

    first = runtime_module.prepare_voice(runtime, clip, "Guten Tag.", cache)
    again = runtime_module.prepare_voice(runtime, clip, "Guten Tag.", cache)
    other = runtime_module.prepare_voice(runtime, clip, "Guten Abend.", cache)

    assert first == again != other
    assert len(calls) == 2
    assert "--frontend-only" in calls[0][0]


def test_run_process_returns_output_and_can_be_cancelled(tmp_path):
    code, output = runtime_module.run_process(
        [sys.executable, "-c", "print('hallo')"], None
    )
    assert code == 0 and "hallo" in output

    with pytest.raises(RuntimeError, match="cancelled"):
        runtime_module.run_process(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            None,
            cancelled=lambda: True,
        )


def test_the_converted_rl_model_wins_over_a_published_one(layout):
    _install(layout)
    published, _, _ = runtime_module.find_models()
    rl = _touch(config.COSYVOICE_MODELS / "CosyVoice3-2512_RL_Q8_0.gguf")

    assert published != rl
    assert runtime_module.find_models()[0] == rl


def _two_candidates(layout: Path, monkeypatch) -> Path:
    _install(layout)
    ollama = _ggml(layout / "ollama")
    _ggml(config.COSYVOICE_HOME / "ggml")
    monkeypatch.setattr(runtime_module, "ollama_library_dirs", lambda: [ollama])
    _fake_run(monkeypatch)
    return ollama


def test_rejected_libraries_are_skipped_until_they_change(layout, monkeypatch):
    ollama = _two_candidates(layout, monkeypatch)
    first = runtime_module.resolve_runtime()
    assert first.ggml_source == "ollama" and first.ggml_fingerprint

    runtime_module.reject_ggml(first)

    assert runtime_module.resolve_runtime().ggml_source == "pinned"
    # An Ollama update is a different set of libraries and gets another chance.
    (ollama / "libggml.so.1").write_bytes(b"a newer build")
    assert runtime_module.resolve_runtime().ggml_source == "ollama"


def test_a_resolve_can_exclude_libraries_without_remembering_it(layout, monkeypatch):
    _two_candidates(layout, monkeypatch)
    first = runtime_module.resolve_runtime()

    other = runtime_module.resolve_runtime(exclude={first.ggml_fingerprint})

    assert other.ggml_source == "pinned"
    assert runtime_module.resolve_runtime().ggml_source == "ollama"
