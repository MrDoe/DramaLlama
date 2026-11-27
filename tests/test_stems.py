from __future__ import annotations

from drama_studio.engines.cosyvoice_directions import instruction_for
from drama_studio.models import Character, Line, ParseReport, Project, Scene, Script
from drama_studio.stems import build_stems, delivery_segments


def make_project() -> Project:
    script = Script(
        title="Test",
        language="en",
        characters={
            "MARA": Character(id="MARA", name="Mara", description="Cold, controlled."),
            "ELIAS": Character(id="ELIAS", name="Elias", description="Tired."),
        },
        scenes=[
            Scene(
                id=1,
                heading="SCENE 1 — ROOM",
                lines=[
                    Line(id="s1-01", scene=1, speaker="MARA", text="You're late.", direction="cold"),
                    Line(id="s1-02", scene=1, speaker="ELIAS", text="Traffic.", direction="tired"),
                    Line(id="s1-03", scene=1, speaker="MARA", text="Pack.", direction="sharp"),
                ],
            ),
            Scene(
                id=2,
                heading="SCENE 2 — HALL",
                lines=[Line(id="s2-01", scene=2, speaker="ELIAS", text="I came.")],
            ),
        ],
    )
    return Project(
        id="test-abc",
        title="Test",
        language="en",
        raw_script="",
        script=script,
        report=ParseReport(dialogue_lines=4, coverage=1.0),
    )


def test_build_stems_groups_scene_and_speaker():
    project = make_project()
    stems = build_stems(project)
    assert [stem.id for stem in stems] == ["s1-mara", "s1-elias", "s2-elias"]
    mara = stems[0]
    assert mara.line_ids == ["s1-01", "s1-03"]
    assert mara.text == "You're late. ... Pack."
    assert "Cold, controlled." in mara.instruction
    assert "SCENE 1 — ROOM" in mara.instruction


def test_build_stems_preserves_takes_when_unchanged():
    project = make_project()
    project.stems = build_stems(project)
    project.stems[0].takes = [
        # Minimal duck-typed take placeholder is not validated here; real takes
        # are covered by the engine tests.
    ]
    project.stems[0].takes = []
    rebuilt = build_stems(project)
    assert rebuilt[0].id == "s1-mara"


def test_build_stems_new_text_drops_old_takes():
    project = make_project()
    project.stems = build_stems(project)
    project.script.scenes[0].lines[0].text = "You are late."
    rebuilt = build_stems(project)
    assert rebuilt[0].text.startswith("You are late.")


def _directed_project(*directions: str) -> Project:
    project = make_project()
    project.script.scenes[0].lines = [
        Line(id=f"s1-{n:02d}", scene=1, speaker="MARA", text=f"Line {n}.", direction=direction)
        for n, direction in enumerate(directions, 1)
    ]
    project.stems = build_stems(project)
    return project


def test_delivery_segments_group_neighbours_that_sound_the_same():
    project = _directed_project("quiet", "hushed", "", "blickt weg", "furious")
    styles = {"quiet": "soft", "hushed": "soft", "blickt weg": "none", "furious": "angry"}
    lines = project.script.scenes[0].lines

    segments = delivery_segments(project.stems[0], lines, styles)

    assert [segment.text for segment in segments] == [
        "Line 1. ... Line 2.",
        "Line 3. ... Line 4.",
        "Line 5.",
    ]
    assert segments[0].instruction == instruction_for("soft")
    assert segments[1].instruction is None
    assert segments[2].instruction == instruction_for("angry")


def test_delivery_segments_without_styles_are_one_plain_piece():
    project = _directed_project("quiet", "furious")

    segments = delivery_segments(project.stems[0], project.script.scenes[0].lines, {})

    assert [(s.text, s.instruction) for s in segments] == [("Line 1. ... Line 2.", None)]


def test_delivery_segments_respect_an_edited_stem_text():
    project = _directed_project("quiet", "furious")
    stem = project.stems[0]
    stem.text = "Something else entirely."

    segments = delivery_segments(
        stem, project.script.scenes[0].lines, {"quiet": "soft", "furious": "angry"}
    )

    assert [(s.text, s.instruction) for s in segments] == [("Something else entirely.", None)]


def test_delivery_segments_use_the_project_language():
    project = _directed_project("quiet")

    segments = delivery_segments(
        project.stems[0], project.script.scenes[0].lines, {"quiet": "soft"}, "de"
    )

    assert segments[0].instruction == instruction_for("soft", "de")
