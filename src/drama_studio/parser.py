from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import (
    Character,
    Line,
    ParseReport,
    ParseResult,
    ParseWarning,
    Scene,
    Script,
)

SCENE_RE = re.compile(
    r"^(?:#{1,6}\s*)?(?:(?:SCENE|SZENE|ACT|AKT)\s+\d+\b|(?:INT|EXT)(?:\.|/EXT\.?)?\b)",
    re.IGNORECASE,
)
TITLE_RE = re.compile(r"^#\s+(.+)$")
CAST_ROW_RE = re.compile(r"^\|\s*\*\*([^*|]+)\*\*\s*\|\s*([^|]*)\|")
BOLD_RE = re.compile(r"^\*\*(.+?)\*\*\s*(.*)$")

SPEAKER_COLON_RE = re.compile(
    r"^(?P<name>[^\s:()*][^:()*]{0,30}?)\s*"
    r"(?:\*?\s*\((?P<direction>[^)]*)\)\s*\*?)?\s*:\s*(?P<text>.*)$"
)
SPEAKER_BARE_RE = re.compile(
    r"^(?P<name>[A-ZÄÖÜ][A-ZÄÖÜ0-9 .'\-]{0,30}?)\s*"
    r"(?:\*?\s*\((?P<direction>[^)]*)\)\s*\*?)?\s*$"
)
PARENTHETICAL_RE = re.compile(r"^\((?P<text>[^)]*)\)$")


@dataclass
class _PendingLine:
    speaker: str
    direction: str
    source_line: int
    parts: list[str] = field(default_factory=list)


def normalize_speaker(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip()).upper()


def display_name(name: str) -> str:
    cleaned = re.sub(r"\s+", " ", name.strip())
    return cleaned.title() if cleaned.isupper() else cleaned


def resolve_speaker(name: str, known: set[str]) -> str:
    """Resolve a short first-name label to the full cast name when unambiguous."""
    if name in known:
        return name
    first = name.split()[0]
    candidates = [
        candidate
        for candidate in known
        if candidate == name
        or candidate.split()[0] == first
        or candidate.startswith(name + " ")
    ]
    return candidates[0] if len(candidates) == 1 else name


def _strip_bold(line: str) -> tuple[str, str]:
    match = BOLD_RE.match(line)
    if not match:
        return line, ""
    return match.group(1).strip(), match.group(2).strip()


def _is_scene_heading(line: str) -> bool:
    return bool(SCENE_RE.match(line.strip()))


def _clean_heading(line: str) -> str:
    text = line.strip()
    text = re.sub(r"^#{1,6}\s*", "", text)
    return re.sub(r"\s+", " ", text).strip(" -—")


def _is_stage_direction(line: str) -> bool:
    text = line.strip()
    return (
        (text.startswith("[") and text.endswith("]"))
        or (text.startswith("*[") and text.endswith("]*"))
        or (text.startswith("**[") and text.endswith("]**"))
    )


def _clean_stage(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^\*{1,2}", "", text)
    text = re.sub(r"\*{1,2}$", "", text)
    return text.strip().strip("[]").strip()


def _is_parenthetical(line: str) -> bool:
    return bool(PARENTHETICAL_RE.match(line.strip()))


def clean_dialogue(text: str) -> str:
    """Remove Markdown emphasis markers that a TTS model would read literally."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


# Sentence boundary: . ! ? plus optional closing quotes/brackets, followed by
# whitespace. The lookbehind skips single-letter abbreviations ("z. B.") and
# decimals ("3.13"); the … ellipsis never splits. Multi-letter abbreviations
# ("Dr.", "Nr.") are glued back in _split_sentences.
_SENTENCE_SPLIT_RE = re.compile(
    r"([.!?]+[\"'\"„“«»)\]]*)(?<!\b[A-Za-zÄÖÜäöü]\.)\s+"
)

_ABBREV_TAILS = (
    "dr.",
    "hr.",
    "fr.",
    "nr.",
    "st.",
    "prof.",
    "sr.",
    "jr.",
    "bzw.",
    "evtl.",
    "etc.",
    "inkl.",
    "max.",
    "min.",
)


def _split_sentences(text: str) -> str:
    """One sentence per line so pauses survive (Suno pauses on newlines)."""
    bits = _SENTENCE_SPLIT_RE.split(text.strip())
    sentences: list[str] = []
    buf = ""
    for index, bit in enumerate(bits):
        if index % 2 == 0:
            buf = bit
        elif buf.strip():
            sentences.append((buf + bit).strip())
            buf = ""
    if buf.strip():
        sentences.append(buf.strip())
    merged: list[str] = []
    for sentence in sentences:
        if merged and merged[-1].rstrip().lower().endswith(_ABBREV_TAILS):
            merged[-1] = f"{merged[-1]} {sentence}"
        else:
            merged.append(sentence)
    return "\n".join(part for part in merged if part)


def _looks_like_metadata(line: str) -> bool:
    text = line.strip()
    return (
        not text
        or text.startswith((">", "|", "---", "## ", "### ", "**"))
        or text.lower().startswith("cast")
        or text.lower().startswith("tag")
    )


def _match_speaker(
    line: str, known: set[str]
) -> tuple[str | None, str, str]:
    """Return (normalized speaker, direction, inline text)."""
    stripped = line.strip()
    head, rest = _strip_bold(stripped)
    candidates = [f"{head} {rest}".strip(), stripped]

    for candidate in candidates:
        match = SPEAKER_COLON_RE.match(candidate)
        if match:
            name = match.group("name").strip()
            if not name:
                continue
            normalized = normalize_speaker(name)
            if normalized.upper() in {"INT", "EXT", "SCENE", "SZENE"}:
                continue
            # A colon can also appear in prose. Accept all-caps names, known
            # characters, or single-word names (e.g. "Mara:").
            if (
                name.isupper()
                or normalized in known
                or len(name.split()) == 1
            ):
                return normalized, (match.group("direction") or "").strip(), match.group("text").strip()

        match = SPEAKER_BARE_RE.match(candidate)
        if match:
            name = match.group("name").strip()
            normalized = normalize_speaker(name)
            if name.isupper() and len(name) <= 31:
                return normalized, (match.group("direction") or "").strip(), ""

    return None, "", ""


class _Builder:
    def __init__(self, language: str, title: str) -> None:
        self.script = Script(title=title, language=language)  # type: ignore[arg-type]
        self.scene: Scene | None = None
        self.pending: _PendingLine | None = None
        self.report = ParseReport()
        self.appendix = False
        self._accounted = 0
        self._seen_non_empty = 0
        self._words = 0

    def add_character(self, name: str, description: str = "") -> None:
        key = normalize_speaker(name)
        if key not in self.script.characters:
            self.script.characters[key] = Character(
                id=key,
                name=display_name(name),
                description=description.strip(),
            )
        elif description and not self.script.characters[key].description:
            self.script.characters[key].description = description.strip()

    def start_scene(self, heading: str, source_line: int) -> None:
        self.finalize()
        self.scene = Scene(id=len(self.script.scenes) + 1, heading=heading)
        self.script.scenes.append(self.scene)
        self.report.scene_headings += 1
        self._accounted += 1

    def ensure_scene(self, source_line: int) -> Scene:
        if self.scene is None:
            self.scene = Scene(id=1, heading="Scene 1")
            self.script.scenes.append(self.scene)
        return self.scene

    def start_line(self, speaker: str, direction: str, text: str, source_line: int) -> None:
        self.finalize()
        self.add_character(speaker)
        self.pending = _PendingLine(
            speaker=speaker,
            direction=direction,
            source_line=source_line,
            parts=[text] if text.strip() else [],
        )

    def append(self, text: str) -> None:
        if self.pending is not None:
            self.pending.parts.append(text.strip())

    def finalize(self) -> None:
        if self.pending is None:
            return
        pending = self.pending
        self.pending = None
        text = " ".join(part for part in pending.parts if part.strip()).strip()
        text = clean_dialogue(text)
        text = _split_sentences(text)
        if not text:
            self.report.warnings.append(
                ParseWarning(
                    source_line=pending.source_line,
                    message=f"{pending.speaker} has no spoken dialogue",
                )
            )
            return
        scene = self.ensure_scene(pending.source_line)
        line = Line(
            id=f"s{scene.id}-{len(scene.lines) + 1:02d}",
            scene=scene.id,
            speaker=pending.speaker,
            text=text,
            direction=pending.direction,
            source_index=pending.source_line,
        )
        scene.lines.append(line)
        self.report.dialogue_lines += 1
        self._accounted += 1
        self._words += len(text.split())

    def add_stage(self, text: str) -> None:
        self.finalize()
        scene = self.ensure_scene(0)
        scene.stage_directions.append(text)
        self.report.stage_directions += 1
        self._accounted += 1

    def finish(self) -> ParseResult:
        self.finalize()
        self.report.source_lines = self._seen_non_empty
        self.report.ignored_lines = max(
            0, self._seen_non_empty - self._accounted - self.report.unknown_lines
        )
        self.report.coverage = (
            round(
                self.report.dialogue_lines
                / (self.report.dialogue_lines + self.report.unknown_lines),
                3,
            )
            if self.report.dialogue_lines or self.report.unknown_lines
            else 0.0
        )
        self.report.runtime_estimate_s = round(self._words / 2.6, 1)
        if not self.script.scenes:
            self.report.warnings.append(
                ParseWarning(source_line=0, message="no scenes found")
            )
        return ParseResult(script=self.script, report=self.report)


def parse_script(raw: str, language: str = "en", title: str | None = None) -> ParseResult:
    """Parse a pasted screenplay into scenes and lines without rewriting text.

    This is the deterministic first pass. It understands the common
    `INT./EXT.` / `SCENE n` headings, `NAME:` and Markdown-bold speaker
    labels, stage directions in brackets, and Markdown cast tables. Anything
    ambiguous is reported instead of guessed.
    """

    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    detected_title = title or ""
    for line in lines:
        match = TITLE_RE.match(line.strip())
        if match:
            detected_title = match.group(1).strip()
            break

    builder = _Builder(language, detected_title or "Untitled drama")
    known: set[str] = set()

    for index, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line:
            builder.finalize()
            continue
        builder._seen_non_empty += 1

        scene_heading = _is_scene_heading(line)
        if scene_heading:
            builder.appendix = False
            builder.start_scene(_clean_heading(line), index)
            continue

        # Front matter: title, cast table, tag legend, blockquote, separators.
        if builder.scene is None:
            cast = CAST_ROW_RE.match(line)
            if cast:
                name = cast.group(1).strip()
                if name.isupper():
                    description = cast.group(2).strip()
                    builder.add_character(name, description)
                    known.add(normalize_speaker(name))
                    builder.report.ignored_lines += 1
                    builder._accounted += 1
                    continue
            if _looks_like_metadata(line) or TITLE_RE.match(line):
                builder.report.ignored_lines += 1
                builder._accounted += 1
                continue

        # A non-scene heading after the script starts an appendix. Skip it so
        # rendering notes and tag tables never become dialogue.
        if line.startswith(("## ", "### ", "# ")):
            builder.finalize()
            builder.appendix = True
            builder.report.ignored_lines += 1
            builder._accounted += 1
            continue

        if builder.appendix:
            builder.finalize()
            builder.report.ignored_lines += 1
            builder._accounted += 1
            continue

        end_marker = re.sub(r"[\s.*_]+", "", line).upper()
        if end_marker in {"END", "ENDE", "THEEND", "FIN"}:
            builder.finalize()
            builder.appendix = True
            builder.report.ignored_lines += 1
            builder._accounted += 1
            continue

        if line.startswith(("---", ">", "|")):
            builder.finalize()
            builder.report.ignored_lines += 1
            builder._accounted += 1
            continue

        if _is_stage_direction(line):
            builder.add_stage(_clean_stage(line))
            continue

        speaker, direction, inline = _match_speaker(line, known)
        if speaker:
            speaker = resolve_speaker(speaker, known)
            known.add(speaker)
            builder.start_line(speaker, direction, inline, index)
            continue

        if builder.pending is not None and _is_parenthetical(line):
            builder.pending.direction = (
                f"{builder.pending.direction} {line.strip('()')}".strip()
            )
            builder.report.ignored_lines += 1
            builder._accounted += 1
            continue

        if builder.pending is not None:
            builder.append(line)
            continue

        # A line outside dialogue and not a stage direction is either a
        # continuation of a previous speech or genuinely unparsed.
        if builder.scene is not None and builder.scene.lines:
            last = builder.scene.lines[-1]
            last.text = re.sub(r"\s+", " ", f"{last.text} {line}").strip()
            builder.report.warnings.append(
                ParseWarning(
                    source_line=index,
                    message="unattributed line appended to previous speech",
                    text=line,
                )
            )
            builder._accounted += 1
            continue

        builder.report.unknown_lines += 1
        builder.report.warnings.append(
            ParseWarning(source_line=index, message="could not parse line", text=line)
        )

    return builder.finish()
