from __future__ import annotations

import pytest
from pydantic import ValidationError

from drama_studio.models import Character, Line, Scene, Script, Stem, Take


def test_line_rejects_blank_text():
    with pytest.raises(ValidationError):
        Line(id="s1-01", scene=1, speaker="MARA", text="   ")


def test_script_iter_lines_in_order():
    script = Script(
        title="Test",
        characters={"MARA": Character(id="MARA", name="Mara")},
        scenes=[
            Scene(
                id=1,
                lines=[
                    Line(id="a", scene=1, speaker="MARA", text="One."),
                    Line(id="b", scene=1, speaker="ELIAS", text="Two."),
                ],
            ),
            Scene(id=2, lines=[Line(id="c", scene=2, speaker="MARA", text="Three.")]),
        ],
    )
    assert [line.id for line in script.iter_lines()] == ["a", "b", "c"]


def test_stem_selected_take():
    stem = Stem(
        id="s1-mara",
        scene=1,
        speaker="MARA",
        line_ids=["a"],
        text="One.",
        instruction="Cold.",
        takes=[
            Take(id="1", file="a.wav", duration_s=1, sample_rate=24000, seed=1),
            Take(
                id="2",
                file="b.wav",
                duration_s=1,
                sample_rate=24000,
                seed=2,
                selected=True,
            ),
        ],
    )
    assert stem.selected_take is not None
    assert stem.selected_take.id == "2"


def test_character_voice_modes():
    breeze = Character(
        id="MARA",
        name="Mara",
        engine="breeze",
        ref_text="You're late.",
        ref_instruction="Cold.",
    )
    qwen = Character(id="ELIAS", name="Elias", engine="chatterbox")
    assert breeze.ref_text == "You're late."
    assert qwen.engine == "chatterbox"
