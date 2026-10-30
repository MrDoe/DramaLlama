"""On-demand lifecycle for the Chatterbox container.

load() starts the docker container when the server is down; unload() and the
end of render_stem() stop it again so its VRAM is only held while needed.
All docker/HTTP interaction is faked: these tests never touch a real server.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import numpy as np
import pytest

from drama_studio.engines.base import EngineNotAvailable, UnsupportedOperation
from drama_studio.engines.chatterbox import ChatterboxEngine
from drama_studio.models import StemRenderRequest


class _Response:
    def __init__(self, payload=None, exc=None):
        self._payload = payload or {}
        self._exc = exc

    def raise_for_status(self):
        if self._exc is not None:
            raise self._exc

    def json(self):
        return self._payload


class _Proc:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.stdout = ""
        self.stderr = ""


@pytest.fixture
def docker_calls(monkeypatch):
    calls: list[list[str]] = []
    state = {"fail_start": False, "missing": False}

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        if state["missing"]:
            raise FileNotFoundError("no docker")
        if argv[1] == "start" and state["fail_start"]:
            return _Proc(returncode=1)
        return _Proc(returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)
    return calls, state


def _serve(monkeypatch, healthy: bool = True, fail_until_started=None):
    """Fake httpx.get; optionally down until the container is started."""

    def fake_get(url, **kwargs):
        if fail_until_started is not None and not fail_until_started["started"]:
            raise httpx.ConnectError("down")
        if not healthy:
            raise httpx.ConnectError("down")
        return _Response({"model_loaded": True})

    monkeypatch.setattr("httpx.get", fake_get)


def test_load_starts_stopped_container(monkeypatch, docker_calls):
    calls, _ = docker_calls
    calls.clear()
    started = {"started": False}

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        assert argv == ["docker", "start", "cb-test"]
        started["started"] = True
        return _Proc(returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)
    _serve(monkeypatch, fail_until_started=started)

    engine = ChatterboxEngine(container="cb-test")
    engine.load()
    assert engine.loaded
    assert calls == [["docker", "start", "cb-test"]]


def test_load_skips_docker_when_already_healthy(monkeypatch, docker_calls):
    calls, _ = docker_calls
    _serve(monkeypatch, healthy=True)

    def fake_run(argv, **kwargs):
        raise AssertionError(f"docker should not be called: {argv}")

    monkeypatch.setattr("subprocess.run", fake_run)
    engine = ChatterboxEngine(container="cb-test")
    engine.load()
    assert engine.loaded
    assert calls == []


def test_load_without_docker_falls_back_to_health_check(monkeypatch, docker_calls):
    _, state = docker_calls
    state["missing"] = True
    _serve(monkeypatch, healthy=False)
    engine = ChatterboxEngine(container="cb-test")
    with pytest.raises(EngineNotAvailable):
        engine.load()
    assert not engine.loaded


def test_load_missing_container_but_server_up(monkeypatch, docker_calls):
    _, state = docker_calls
    state["fail_start"] = True  # e.g. no such container: externally managed
    _serve(monkeypatch, healthy=True)
    engine = ChatterboxEngine(container="cb-test")
    engine.load()  # falls back to the reachable server
    assert engine.loaded


def test_unload_stops_container(monkeypatch, docker_calls):
    calls, _ = docker_calls
    _serve(monkeypatch, healthy=True)
    engine = ChatterboxEngine(container="cb-test")
    engine.load()
    assert engine.loaded
    engine.unload()
    assert not engine.loaded
    assert calls[-1] == ["docker", "stop", "cb-test"]


def test_unload_never_raises(monkeypatch, docker_calls):
    _, state = docker_calls
    state["missing"] = True
    engine = ChatterboxEngine(container="cb-test")
    engine.unload()  # must not raise without docker
    assert not engine.loaded


def test_empty_container_disables_docker(monkeypatch, docker_calls):
    calls, _ = docker_calls
    _serve(monkeypatch, healthy=True)

    def fake_run(argv, **kwargs):
        raise AssertionError(f"docker should not be called: {argv}")

    monkeypatch.setattr("subprocess.run", fake_run)
    engine = ChatterboxEngine(container="")
    engine.load()
    assert engine.loaded
    engine.unload()
    assert not engine.loaded
    assert calls == []


def test_render_stem_stops_container_afterwards(monkeypatch, docker_calls, tmp_path):
    calls, _ = docker_calls
    _serve(monkeypatch, healthy=True)

    def fake_speak(self, request, ref_path):
        return np.zeros(2400, dtype=np.float32), 24000

    def fake_write_mono(path, audio, sample_rate):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"fake")
        return target

    monkeypatch.setattr(ChatterboxEngine, "_speak", fake_speak)
    monkeypatch.setattr(
        "drama_studio.engines.chatterbox.write_mono", fake_write_mono
    )
    monkeypatch.setattr(
        "drama_studio.engines.chatterbox._median_f0", lambda audio, sr: 0.0
    )

    engine = ChatterboxEngine(container="cb-test")
    request = StemRenderRequest(
        project_id="p",
        stem_id="s1",
        speaker="ANNA",
        text="Servus.",
        instruction="",
        output_dir=str(tmp_path),
        candidates=1,
    )
    result = engine.render_stem(request)
    assert len(result.takes) == 1
    assert not engine.loaded
    assert calls[-1] == ["docker", "stop", "cb-test"]


def test_design_reference_releases_container(monkeypatch, docker_calls):
    from drama_studio.models import ReferenceRequest

    calls, _ = docker_calls
    engine = ChatterboxEngine(container="cb-test")
    engine._loaded = True  # as if an eager EngineManager.get() started it
    with pytest.raises(UnsupportedOperation):
        engine.design_reference(
            ReferenceRequest(
                character_id="A", text="x", instruction="y", output_path="z"
            )
        )
    assert calls == [["docker", "stop", "cb-test"]]
    assert not engine.loaded
