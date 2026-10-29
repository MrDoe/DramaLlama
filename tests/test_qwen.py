from __future__ import annotations

import numpy as np
import pytest

from drama_studio.engines.base import EngineNotAvailable
from drama_studio.engines.qwen3 import Qwen3Engine
from drama_studio.models import StemRenderRequest


class FakeQwenModel:
    def __init__(self):
        self.calls = []

    def generate_custom_voice(self, **kwargs):
        self.calls.append(kwargs)
        return [np.zeros(24000, dtype=np.float32)], 24000


def make_request(tmp_path, preset="Vivian"):
    return StemRenderRequest(
        project_id="de-test",
        stem_id="s1-anna",
        speaker="ANNA BERG",
        text="Setzen Sie sich, Herr Vogt.",
        instruction="Ruhig, präzise, unerbittlich.",
        output_dir=str(tmp_path),
        voice_preset=preset,
        language="de",
        candidates=1,
        seed=52,
    )


def test_qwen_render_uses_preset_and_german(tmp_path):
    engine = Qwen3Engine()
    model = FakeQwenModel()
    engine._model = model
    engine._loaded = True

    result = engine.render_stem(make_request(tmp_path))
    assert result.engine == "qwen3"
    assert result.takes[0].duration_s == 1.0
    assert (tmp_path / result.takes[0].file).exists()
    call = model.calls[0]
    assert call["speaker"] == "Vivian"
    assert call["language"] == "German"
    assert call["text"] == "Setzen Sie sich, Herr Vogt."


def test_qwen_rejects_unknown_preset(tmp_path):
    engine = Qwen3Engine()
    engine._model = FakeQwenModel()
    engine._loaded = True
    with pytest.raises(EngineNotAvailable):
        engine.render_stem(make_request(tmp_path, preset="Nobody"))
