from __future__ import annotations

import importlib.util
from pathlib import Path


def _load():
    path = Path(__file__).resolve().parents[1] / "scripts" / "stop_running.py"
    spec = importlib.util.spec_from_file_location("stop_running", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_never_matches_itself():
    module = _load()
    own_pid = module.os.getpid()
    assert own_pid in module._ancestors()
    assert all(pid != own_pid for pid, _ in module.find_instances())


def test_skips_suno_bridge_and_tests():
    module = _load()
    for pid, cmd in module.find_instances():
        assert "suno-bridge" not in cmd
        assert "pytest" not in cmd
        assert "serve" in cmd or "worker" in cmd
