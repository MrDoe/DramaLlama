from __future__ import annotations

import re

from .engines.cosyvoice_directions import instruction_for
from .models import Character, Line, Project, RenderSegment, Stem
from .suno_lyrics import format_suno_lyrics


def _slug(value: str, limit: int = 32) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return text[:limit].strip("-") or "speaker"


def _instruction(character: Character | None, scene_heading: str, lines: list[Line]) -> str:
    name = character.name if character else lines[0].speaker
    description = (character.description if character and character.description else "").strip()
    beats = "; ".join(line.direction.strip() for line in lines if line.direction.strip())
    parts = [f"{name} performs the whole scene as one continuous take."]
    if description:
        parts.append(f"Voice and baseline: {description}")
    if scene_heading:
        parts.append(f"Scene: {scene_heading}")
    if beats:
        parts.append(f"Performance beats in order: {beats}")
    parts.append(
        "Keep the voice, pitch and room consistent across every line; "
        "let the emotional arc move naturally between them."
    )
    return " ".join(parts)


def join_line_texts(lines: list[Line]) -> str:
    """How a stem's lines become one block of text; "..." keeps a pause."""
    return " ... ".join(line.text.strip() for line in lines if line.text.strip())


def delivery_segments(
    stem: Stem, lines: list[Line], styles: dict[str, str]
) -> list[RenderSegment]:
    """Split a stem where the delivery changes, for engines that steer per piece.

    ``styles`` maps a line direction to a delivery style; neighbouring lines
    with the same resulting instruction stay together. A hand-edited stem text
    no longer matches its lines, so it renders as one plain piece.
    """
    if stem.text != join_line_texts(lines):
        return [RenderSegment(text=stem.text)]
    groups: list[tuple[str | None, list[Line]]] = []
    for line in lines:
        if not line.text.strip():
            continue
        instruction = instruction_for(styles.get(line.direction.strip(), ""))
        if groups and groups[-1][0] == instruction:
            groups[-1][1].append(line)
        else:
            groups.append((instruction, [line]))
    return [
        RenderSegment(text=join_line_texts(group), instruction=instruction)
        for instruction, group in groups
    ]


def build_stems(project: Project) -> list[Stem]:
    """Group lines by (scene, speaker) and keep existing takes when unchanged."""
    existing = {(stem.scene, stem.speaker): stem for stem in project.stems}
    stems: list[Stem] = []

    for scene in project.script.scenes:
        groups: dict[str, list[Line]] = {}
        for line in scene.lines:
            groups.setdefault(line.speaker, []).append(line)

        for speaker, lines in groups.items():
            text = join_line_texts(lines)
            if not text:
                continue
            instruction = _instruction(
                project.script.characters.get(speaker), scene.heading, lines
            )
            character = project.script.characters.get(speaker)
            stem = Stem(
                id=f"s{scene.id}-{_slug(speaker)}",
                scene=scene.id,
                speaker=speaker,
                line_ids=[line.id for line in lines],
                text=text,
                instruction=instruction,
                suno_lyrics=format_suno_lyrics(lines, character, scene.heading),
                cfg_scale=2.5,
                seed=42 + scene.id * 10,
            )
            old = existing.get((scene.id, speaker))
            if old and old.text == stem.text and old.instruction == stem.instruction:
                stem.takes = old.takes
                # Keep an LLM-upgraded sheet across rebuilds of unchanged stems.
                if old.suno_lyrics:
                    stem.suno_lyrics = old.suno_lyrics
            stems.append(stem)

    return stems
