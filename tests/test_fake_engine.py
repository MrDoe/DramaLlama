from __future__ import annotations

from pathlib import Path

from drama_studio.engines.fake import FakeEngine
from drama_studio.models import ReferenceRequest, StemRenderRequest


def test_fake_render_stem(tmp_path):
    engine = FakeEngine()
    request = StemRenderRequest(
        project_id="demo",
        stem_id="s1-mara",
        speaker="MARA",
        text="You're late. Four days after the funeral.",
        instruction="Cold and controlled.",
        output_dir=str(tmp_path),
        candidates=3,
        seed=7,
    )
    result = engine.render_stem(request)
    assert len(result.takes) == 3
    assert result.selected_take_id in {take.id for take in result.takes}
    selected = next(take for take in result.takes if take.selected)
    assert selected.duration_s > 0
    assert (tmp_path / selected.file).exists()
    assert result.text_sha256


def test_fake_reference(tmp_path):
    engine = FakeEngine()
    request = ReferenceRequest(
        character_id="MARA",
        text="You're late. For the funeral.",
        instruction="Cold, controlled.",
        output_path=str(tmp_path / "refs" / "mara.wav"),
    )
    result = engine.design_reference(request)
    assert Path(result.file).exists()
    assert result.duration_s > 0
    assert result.f0_hz and result.f0_hz > 50


def test_fake_engine_is_deterministic(tmp_path):
    engine = FakeEngine()
    request = StemRenderRequest(
        project_id="demo",
        stem_id="s1",
        speaker="ELIAS",
        text="I came, didn't I?",
        instruction="Tired.",
        output_dir=str(tmp_path),
        candidates=1,
        seed=3,
    )
    first = engine.render_stem(request)
    second = engine.render_stem(request)
    assert first.takes[0].duration_s == second.takes[0].duration_s
    assert first.takes[0].f0_hz == second.takes[0].f0_hz
