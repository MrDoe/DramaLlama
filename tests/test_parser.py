from __future__ import annotations

from drama_studio.parser import parse_script

SAMPLE = """# THE TEST HOUSE

| Character | Description |
|---|---|
| **MARA** | 38, controlled. |
| **ELIAS** | 41, tired. |

## SCENE 1 — LIVING ROOM

*[AMBIENCE: rain against the windows]*
*[SFX: a door closes]*

**MARA** *(cold, measured)*
You're late.

**ELIAS** *(out of breath)*
Traffic. The bridge was—

**MARA** *(cutting him off)*
I don't need the weather report.

**END.**

---

## Rendering notes

This should never become dialogue.
"""


def test_markdown_script():
    result = parse_script(SAMPLE, "en")
    script = result.script
    assert script.title == "THE TEST HOUSE"
    assert len(script.scenes) == 1
    assert len(script.scenes[0].lines) == 3
    assert [line.speaker for line in script.scenes[0].lines] == ["MARA", "ELIAS", "MARA"]
    assert script.scenes[0].lines[0].text == "You're late."
    assert script.scenes[0].lines[0].direction == "cold, measured"
    assert script.scenes[0].lines[2].text == "I don't need the weather report."
    assert len(script.scenes[0].stage_directions) == 2
    assert result.report.dialogue_lines == 3
    assert result.report.coverage == 1.0
    assert "Rendering" not in " ".join(line.text for line in script.iter_lines())


def test_colon_format():
    raw = """INT. INTERROGATION ROOM - NIGHT

MARA: You're late.
ELIAS: Traffic.
"""
    result = parse_script(raw, "en")
    assert len(result.script.scenes) == 1
    assert [line.text for line in result.script.scenes[0].lines] == [
        "You're late.",
        "Traffic.",
    ]
    assert result.report.unknown_lines == 0


def test_short_name_resolves_to_cast_name():
    raw = """# KALTES LICHT

| **ANNA BERG** | Kommissarin. |
| **MARCUS VOGT** | Beschuldigter. |

## SZENE 1 — VERHÖRRAUM

**ANNA** *(kalt)*
Setzen Sie sich.

**MARCUS** *(arrogant)*
Ich sitze seit vier Stunden hier.
"""
    result = parse_script(raw, "de")
    assert set(result.script.characters) == {"ANNA BERG", "MARCUS VOGT"}
    assert [line.speaker for line in result.script.scenes[0].lines] == [
        "ANNA BERG",
        "MARCUS VOGT",
    ]


def test_unattributed_line_is_reported():
    raw = """## SCENE 1

A line with no speaker.
"""
    result = parse_script(raw, "en")
    assert result.report.unknown_lines == 1
    assert result.report.coverage == 0.0
    assert result.report.warnings[0].message == "could not parse line"


def test_runtime_estimate():
    raw = """## SCENE 1

MARA: one two three four five six seven eight nine ten.
"""
    result = parse_script(raw, "en")
    assert result.report.runtime_estimate_s > 3.0
