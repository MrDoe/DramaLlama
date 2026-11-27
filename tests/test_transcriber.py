from __future__ import annotations

import sys
import types

import pytest

from drama_studio import transcriber as module


@pytest.mark.parametrize(("logical", "threads"), [(2, 4), (8, 4), (16, 8), (64, 8), (None, 4)])
def test_cpu_threads_follow_the_physical_cores(monkeypatch, logical, threads):
    monkeypatch.setattr(module.os, "cpu_count", lambda: logical)

    assert module.default_cpu_threads() == threads


def test_the_model_is_loaded_with_those_threads(monkeypatch):
    loaded: dict = {}
    fake = types.SimpleNamespace(
        WhisperModel=lambda path, **kwargs: loaded.update(kwargs) or object()
    )
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)
    monkeypatch.setattr(module, "_MODEL_CACHE", {})
    monkeypatch.setattr(module, "default_cpu_threads", lambda: 6)

    module.WhisperTranscriber(model_path="/models/whisper").load()

    assert loaded["cpu_threads"] == 6
    assert loaded["device"] == "cpu"
